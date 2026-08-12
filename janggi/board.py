"""Janggi (Korean chess) board representation and move generation.

This module is the single source of truth for the rules. The search and
evaluation modules must never reimplement movement logic; they call into here.

Coordinate system
------------------
The board is 10 rows (ranks) x 9 columns (files), indexed [row][col] with
row 0 at the top (HAN side) and row 9 at the bottom (CHO side).

Sides
-----
HAN = +1  (한, conventionally the "second" player, top of the board)
CHO = -1  (초, conventionally the player who moves first, bottom)

Piece type codes
----------------
K general (궁/장), C chariot (차), P cannon (포), M horse (마),
S elephant (상), G guard (사), J soldier (졸/병).

Representation
--------------
A ``Board`` keeps three views of the same position and guarantees they never
drift apart:

* ``_g``      -- list-of-lists of ``(kind, side)`` tuples, the Python view;
* ``_pc``/``_sd`` -- flat int arrays consumed by the Cython accelerators;
* ``_hash``   -- the incrementally maintained Zobrist key.

``board.grid`` exposes the Python view through a *write-through* proxy, so the
natural ``board.grid[r][c] = ("C", CHO)`` idiom used by tests, the server and
ad-hoc scripts updates the int arrays and the hash as well. Before this, direct
grid writes silently left the accelerators looking at a stale board -- that is
exactly the bug that broke server-side check detection in production, and the
same bug made 12 of the unit tests fail whenever the extensions were compiled.
"""

from __future__ import annotations

import array as _array
import random
from dataclasses import dataclass
from typing import Iterable

HAN = 1
CHO = -1

ROWS = 10
COLS = 9
N_SQUARES = ROWS * COLS

# --- Cython fast-attack acceleration (optional, falls back to pure Python) ---
# Setting JANGGI_NO_ACCEL=1 forces the pure-Python path. Deployment never sets
# it; CI does, so both the compiled and the fallback implementations are
# exercised by the same test suite.
import os as _os

DISABLE_ACCEL = _os.environ.get("JANGGI_NO_ACCEL", "") not in ("", "0", "false", "False")

if DISABLE_ACCEL:
    _HAVE_CATTACK = False
    _HAVE_CMOVEGEN = False
else:
    try:
        from janggi._attack import fast_is_attacked_c as _c_fast_is_attacked
        _HAVE_CATTACK = True
    except Exception:
        _HAVE_CATTACK = False

    try:
        from janggi._movegen import generate_pseudo_c as _c_generate_pseudo
        _HAVE_CMOVEGEN = _HAVE_CATTACK  # int arrays are maintained only when attack accel is on
    except Exception:
        _HAVE_CMOVEGEN = False

_CODE_PIECE = {1: "C", 2: "P", 3: "M", 4: "S", 5: "J", 6: "K", 7: "G"}

_PIECE_CODE = {"C": 1, "P": 2, "M": 3, "S": 4, "J": 5, "K": 6, "G": 7}

PIECE_KINDS = ("K", "C", "P", "M", "S", "G", "J")

# Material values in centipawn-like units. Tuned for Janggi: the chariot is
# the strongest line piece, the cannon needs a screen so it is worth less, and
# elephant/horse are similar with the horse slightly more flexible.
PIECE_VALUE = {
    "K": 10000,
    "C": 1300,
    "P": 700,
    "M": 500,
    "S": 300,
    "G": 300,
    "J": 200,
}

# Palace squares per side. Generals and guards are confined here.
PALACE_COLS = (3, 4, 5)
HAN_PALACE_ROWS = (0, 1, 2)
CHO_PALACE_ROWS = (7, 8, 9)

# The five points in each palace that sit on a drawn diagonal. Pieces that move
# orthogonally (chariot, cannon, soldier, general, guard) may use the diagonal
# only between these connected points.
PALACE_DIAGONAL_POINTS = frozenset(
    {
        (0, 3), (0, 5), (1, 4), (2, 3), (2, 5),
        (7, 3), (7, 5), (8, 4), (9, 3), (9, 5),
    }
)

# Flat lookup tables. Set membership on a tuple allocates; a preallocated list
# of bools indexed by square is markedly faster in the pure-Python hot paths.
_IS_PDIAG = [False] * N_SQUARES
for _r, _c in PALACE_DIAGONAL_POINTS:
    _IS_PDIAG[_r * COLS + _c] = True

_IN_PALACE_HAN = [False] * N_SQUARES
_IN_PALACE_CHO = [False] * N_SQUARES
for _r in range(ROWS):
    for _c in range(COLS):
        if _c in PALACE_COLS:
            if _r in HAN_PALACE_ROWS:
                _IN_PALACE_HAN[_r * COLS + _c] = True
            elif _r in CHO_PALACE_ROWS:
                _IN_PALACE_CHO[_r * COLS + _c] = True


# ----------------------------------------------------------------- Zobrist
_PIECE_INDEX = {k: i for i, k in enumerate("KCPMSGJ")}


def _build_zobrist() -> dict:
    """Build the Zobrist key table.

    The seed and the exact draw order are load-bearing: ``data/opening_book.json``
    is keyed by these hashes, so changing either silently invalidates the book.
    """
    rng = random.Random(20260619)  # fixed seed for reproducible hashing
    table = {}
    for r in range(ROWS):
        for c in range(COLS):
            for kind in _PIECE_INDEX:
                for side in (HAN, CHO):
                    table[(r, c, kind, side)] = rng.getrandbits(64)
    table["side"] = rng.getrandbits(64)
    return table


_ZOBRIST = _build_zobrist()

# Flat mirror of _ZOBRIST for the incremental updates: index by
# (square * 16) + (piece_code * 2) + side_bit, with side_bit 0 = HAN, 1 = CHO.
_ZFLAT = [0] * (N_SQUARES * 16)
for _r in range(ROWS):
    for _c in range(COLS):
        for _kind, _code in _PIECE_CODE.items():
            for _side, _bit in ((HAN, 0), (CHO, 1)):
                _ZFLAT[(_r * COLS + _c) * 16 + _code * 2 + _bit] = _ZOBRIST[
                    (_r, _c, _kind, _side)
                ]
_ZSIDE = _ZOBRIST["side"]


@dataclass(frozen=True)
class Move:
    """A single move: from (fr, fc) to (tr, tc), optionally capturing."""

    fr: int
    fc: int
    tr: int
    tc: int
    captured: str | None = None  # piece type captured, for unmake / display

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.fr, self.fc, self.tr, self.tc)


def in_board(r: int, c: int) -> bool:
    return 0 <= r < ROWS and 0 <= c < COLS


def in_palace(r: int, c: int, side: int) -> bool:
    if not (0 <= r < ROWS and 0 <= c < COLS):
        return False
    idx = r * COLS + c
    return _IN_PALACE_HAN[idx] if side == HAN else _IN_PALACE_CHO[idx]


def on_palace_diagonal(r: int, c: int) -> bool:
    if not (0 <= r < ROWS and 0 <= c < COLS):
        return False
    return _IS_PDIAG[r * COLS + c]


# Standard formations: order of pieces on the inner back-row files [c1, c2, c6, c7].
# Outer files (0, 8) are always chariots; files 3, 5 guards; file 4 general row.
FORMATIONS = {
    "msm_s": ["M", "S", "S", "M"],   # 마상상마
    "smsm": ["S", "M", "S", "M"],    # 상마상마
    "msms": ["M", "S", "M", "S"],    # 마상마상
    "smms": ["S", "M", "M", "S"],    # 상마마상
}


class _RowView:
    """One board row, writing through to every representation of the board."""

    __slots__ = ("_b", "_r")

    def __init__(self, board: "Board", r: int) -> None:
        self._b = board
        self._r = r

    def __getitem__(self, c):
        return self._b._g[self._r][c]

    def __setitem__(self, c, value) -> None:
        if isinstance(c, slice):
            indices = range(*c.indices(COLS))
            values = list(value)
            if len(values) != len(indices):
                raise ValueError("row slice assignment length mismatch")
            for i, v in zip(indices, values):
                self._b._set_cell(self._r, i, v)
            return
        if c < 0:
            c += COLS
        if not 0 <= c < COLS:
            raise IndexError(f"column out of range: {c}")
        self._b._set_cell(self._r, c, value)

    def __len__(self) -> int:
        return COLS

    def __iter__(self):
        return iter(self._b._g[self._r])

    def __eq__(self, other) -> bool:
        if isinstance(other, _RowView):
            return self._b._g[self._r] == other._b._g[other._r]
        if isinstance(other, list):
            return self._b._g[self._r] == other
        return NotImplemented

    def __repr__(self) -> str:
        return repr(self._b._g[self._r])


class _GridView:
    """``board.grid`` -- indexable like ``list[list[piece]]``, but write-through."""

    __slots__ = ("_b",)

    def __init__(self, board: "Board") -> None:
        self._b = board

    def __getitem__(self, r):
        if isinstance(r, slice):
            return [_RowView(self._b, i) for i in range(*r.indices(ROWS))]
        if r < 0:
            r += ROWS
        if not 0 <= r < ROWS:
            raise IndexError(f"row out of range: {r}")
        return _RowView(self._b, r)

    def __setitem__(self, r, value) -> None:
        if isinstance(r, slice):
            indices = range(*r.indices(ROWS))
            rows = list(value)
            if len(rows) != len(indices):
                raise ValueError("grid slice assignment length mismatch")
            for i, row in zip(indices, rows):
                self[i] = row
            return
        if r < 0:
            r += ROWS
        row = list(value)
        if len(row) != COLS:
            raise ValueError(f"row must have {COLS} cells")
        for c, cell in enumerate(row):
            self._b._set_cell(r, c, cell)

    def __len__(self) -> int:
        return ROWS

    def __iter__(self):
        return (_RowView(self._b, r) for r in range(ROWS))

    def __eq__(self, other) -> bool:
        if isinstance(other, _GridView):
            return self._b._g == other._b._g
        if isinstance(other, list):
            return self._b._g == [list(row) for row in other]
        return NotImplemented

    def __repr__(self) -> str:
        return repr(self._b._g)


class Board:
    """Mutable board with make/unmake for efficient search."""

    __slots__ = ("_g", "_stm", "_history", "_pc", "_sd", "_hash", "_king", "_grid_view")

    def __init__(self) -> None:
        # _g[r][c] is None or a (type, side) tuple.
        self._g: list[list[tuple[str, int] | None]] = [
            [None] * COLS for _ in range(ROWS)
        ]
        self._stm = CHO  # Cho always moves first.
        self._history: list[tuple[Move, tuple[str, int] | None]] = []
        # Parallel integer board for the Cython accelerators.
        # _pc[r*COLS+c]: piece code (0 empty), _sd: side code (0/1/2).
        self._pc = _array.array('i', bytes(4 * N_SQUARES))
        self._sd = _array.array('i', bytes(4 * N_SQUARES))
        # Incrementally maintained Zobrist key; CHO to move toggles _ZSIDE.
        self._hash = _ZSIDE
        # General locations, kept current so find_general() is O(1).
        self._king: dict[int, tuple[int, int] | None] = {HAN: None, CHO: None}
        self._grid_view = _GridView(self)

    # ------------------------------------------------------------------ setup
    @classmethod
    def standard(cls, cho_formation: str = "msm_s", han_formation: str = "msm_s") -> "Board":
        b = cls()
        b._place_side(HAN, FORMATIONS[han_formation])
        b._place_side(CHO, FORMATIONS[cho_formation])
        return b

    @classmethod
    def from_grid(
        cls, grid: Iterable[Iterable[tuple[str, int] | None]], side_to_move: int = CHO
    ) -> "Board":
        """Build a board from a 10x9 iterable of ``(kind, side)`` cells.

        Raises ValueError on a malformed grid or an unknown piece kind, so
        callers handling untrusted input (the web API) can answer 400 instead
        of dying with a KeyError.
        """
        rows = [list(row) for row in grid]
        if len(rows) != ROWS or any(len(row) != COLS for row in rows):
            raise ValueError(f"grid must be {ROWS}x{COLS}")
        b = cls()
        for r in range(ROWS):
            for c in range(COLS):
                cell = rows[r][c]
                if cell is None:
                    continue
                kind, side = cell
                if kind not in _PIECE_CODE:
                    raise ValueError(f"unknown piece kind: {kind!r}")
                if side not in (HAN, CHO):
                    raise ValueError(f"unknown side: {side!r}")
                b._set_cell(r, c, (kind, side))
        b.side_to_move = side_to_move
        return b

    def copy(self) -> "Board":
        """An independent board with the same position and side to move.

        The move history is intentionally not copied: the copy is a position,
        not a continuation, so unmake() on it is undefined.
        """
        b = Board()
        b._g = [row[:] for row in self._g]
        b._pc = _array.array('i', self._pc)
        b._sd = _array.array('i', self._sd)
        b._stm = self._stm
        b._hash = self._hash
        b._king = dict(self._king)
        return b

    def _place_side(self, side: int, formation: list[str]) -> None:
        if side == HAN:
            back, pal, po, jol = 0, 1, 2, 3
        else:
            back, pal, po, jol = 9, 8, 7, 6
        place = self._set_cell
        place(back, 0, ("C", side))
        place(back, 8, ("C", side))
        place(back, 3, ("G", side))
        place(back, 5, ("G", side))
        place(back, 1, (formation[0], side))
        place(back, 2, (formation[1], side))
        place(back, 6, (formation[2], side))
        place(back, 7, (formation[3], side))
        place(pal, 4, ("K", side))
        place(po, 1, ("P", side))
        place(po, 7, ("P", side))
        for c in (0, 2, 4, 6, 8):
            place(jol, c, ("J", side))

    # -------------------------------------------------------------- accessors
    @property
    def grid(self):
        """Write-through view of the board; see the module docstring."""
        return self._grid_view

    @grid.setter
    def grid(self, rows) -> None:
        for r, row in enumerate(rows):
            if r >= ROWS:
                raise ValueError(f"grid must have {ROWS} rows")
            cells = list(row)
            if len(cells) != COLS:
                raise ValueError(f"row must have {COLS} cells")
            for c, cell in enumerate(cells):
                self._set_cell(r, c, cell)

    @property
    def side_to_move(self) -> int:
        return self._stm

    @side_to_move.setter
    def side_to_move(self, side: int) -> None:
        if side not in (HAN, CHO):
            raise ValueError(f"side must be HAN or CHO, got {side!r}")
        if side != self._stm:
            self._stm = side
            self._hash ^= _ZSIDE

    def piece_at(self, r: int, c: int) -> tuple[str, int] | None:
        return self._g[r][c]

    def set_piece(self, r: int, c: int, piece: tuple[str, int] | None) -> None:
        """Place or clear a square, keeping every representation in sync."""
        if not (0 <= r < ROWS and 0 <= c < COLS):
            raise IndexError(f"square out of range: ({r}, {c})")
        self._set_cell(r, c, piece)

    def find_general(self, side: int) -> tuple[int, int] | None:
        return self._king[side]

    def zobrist(self) -> int:
        """The position's Zobrist key (maintained incrementally)."""
        return self._hash

    def pieces(self) -> Iterable[tuple[int, int, str, int]]:
        """Yield (row, col, kind, side) for every occupied square."""
        g = self._g
        for r in range(ROWS):
            row = g[r]
            for c in range(COLS):
                p = row[c]
                if p is not None:
                    yield r, c, p[0], p[1]

    # ----------------------------------------------------------- make/unmake
    def _set_cell(self, r: int, c: int, p) -> None:
        """Write one square across the Python grid, int arrays, hash and kings."""
        idx = r * COLS + c
        old_code = self._pc[idx]
        if old_code:
            old_side = self._sd[idx]
            self._hash ^= _ZFLAT[idx * 16 + old_code * 2 + (old_side - 1)]
            if old_code == 6:
                self._king[HAN if old_side == 1 else CHO] = None
        if p is None:
            self._g[r][c] = None
            self._pc[idx] = 0
            self._sd[idx] = 0
            return
        kind, side = p
        code = _PIECE_CODE[kind]
        bit = 0 if side == HAN else 1
        self._g[r][c] = (kind, side)
        self._pc[idx] = code
        self._sd[idx] = bit + 1
        self._hash ^= _ZFLAT[idx * 16 + code * 2 + bit]
        if code == 6:
            self._king[side] = (r, c)

    def _sync_cell(self, r: int, c: int, p) -> None:
        """Backwards-compatible alias for :meth:`_set_cell`."""
        self._set_cell(r, c, p)

    def resync_int_arrays(self) -> None:
        """Rebuild every derived representation from the Python grid.

        Kept for backwards compatibility. Since ``board.grid`` now writes
        through, callers no longer need to remember this; it is a no-op on a
        consistent board and a repair on an inconsistent one.
        """
        self._pc = _array.array('i', bytes(4 * N_SQUARES))
        self._sd = _array.array('i', bytes(4 * N_SQUARES))
        self._hash = _ZSIDE if self._stm == CHO else 0
        self._king = {HAN: None, CHO: None}
        g = self._g
        for r in range(ROWS):
            for c in range(COLS):
                p = g[r][c]
                if p is not None:
                    self._set_cell(r, c, p)

    def make(self, mv: Move) -> None:
        g = self._g
        fr, fc, tr, tc = mv.fr, mv.fc, mv.tr, mv.tc
        moving = g[fr][fc]
        captured = g[tr][tc]
        self._history.append((mv, captured))
        g[tr][tc] = moving
        g[fr][fc] = None

        fi = fr * COLS + fc
        ti = tr * COLS + tc
        code = self._pc[fi]
        sd = self._sd[fi]
        h = self._hash
        cap_code = self._pc[ti]
        if cap_code:
            h ^= _ZFLAT[ti * 16 + cap_code * 2 + (self._sd[ti] - 1)]
            if cap_code == 6:
                self._king[HAN if self._sd[ti] == 1 else CHO] = None
        h ^= _ZFLAT[fi * 16 + code * 2 + (sd - 1)]
        h ^= _ZFLAT[ti * 16 + code * 2 + (sd - 1)]
        self._hash = h ^ _ZSIDE
        self._pc[ti] = code
        self._sd[ti] = sd
        self._pc[fi] = 0
        self._sd[fi] = 0
        if code == 6:
            self._king[HAN if sd == 1 else CHO] = (tr, tc)
        self._stm = -self._stm

    def unmake(self) -> None:
        mv, captured = self._history.pop()
        g = self._g
        fr, fc, tr, tc = mv.fr, mv.fc, mv.tr, mv.tc
        moving = g[tr][tc]
        g[fr][fc] = moving
        g[tr][tc] = captured

        fi = fr * COLS + fc
        ti = tr * COLS + tc
        code = self._pc[ti]
        sd = self._sd[ti]
        h = self._hash ^ _ZSIDE
        h ^= _ZFLAT[ti * 16 + code * 2 + (sd - 1)]
        h ^= _ZFLAT[fi * 16 + code * 2 + (sd - 1)]
        self._pc[fi] = code
        self._sd[fi] = sd
        if code == 6:
            self._king[HAN if sd == 1 else CHO] = (fr, fc)
        if captured is None:
            self._pc[ti] = 0
            self._sd[ti] = 0
        else:
            cap_code = _PIECE_CODE[captured[0]]
            cap_bit = 0 if captured[1] == HAN else 1
            self._pc[ti] = cap_code
            self._sd[ti] = cap_bit + 1
            h ^= _ZFLAT[ti * 16 + cap_code * 2 + cap_bit]
            if cap_code == 6:
                self._king[captured[1]] = (tr, tc)
        self._hash = h
        self._stm = -self._stm

    def last_move(self) -> Move | None:
        """The most recent move actually played on this board (with its real
        captured flag set), or None at the root. Used by the search to detect
        recaptures for selective extensions."""
        if not self._history:
            return None
        mv, captured = self._history[-1]
        if captured is not None and mv.captured is None:
            # Reflect the real capture even if the Move object wasn't tagged.
            return Move(mv.fr, mv.fc, mv.tr, mv.tc, captured[0])
        return mv

    # ------------------------------------------------------ move generation
    def generate_pseudo(self, side: int) -> list[Move]:
        """All moves ignoring self-check and the facing-generals rule.
        Uses the Cython generator when available (identical results, verified);
        falls back to the pure-Python implementation otherwise."""
        if _HAVE_CMOVEGEN:
            bs = 1 if side == HAN else 2
            raw = _c_generate_pseudo(self._pc, self._sd, bs)
            return [
                Move(fr, fc, tr, tc, _CODE_PIECE[cap] if cap else None)
                for fr, fc, tr, tc, cap in raw
            ]
        return self._py_generate_pseudo(side)

    def _py_generate_pseudo(self, side: int) -> list[Move]:
        """All moves ignoring self-check and the facing-generals rule."""
        moves: list[Move] = []
        g = self._g
        for r in range(ROWS):
            row = g[r]
            for c in range(COLS):
                p = row[c]
                if p is None or p[1] != side:
                    continue
                self._piece_moves(r, c, p[0], side, moves)
        return moves

    def _add(self, r: int, c: int, nr: int, nc: int, side: int, moves: list[Move]) -> bool:
        """Append a move to (nr, nc) if legal landing; return True if empty (slide can continue)."""
        if not (0 <= nr < ROWS and 0 <= nc < COLS):
            return False
        target = self._g[nr][nc]
        if target is None:
            moves.append(Move(r, c, nr, nc, None))
            return True
        if target[1] != side:
            moves.append(Move(r, c, nr, nc, target[0]))
        return False

    def _piece_moves(self, r: int, c: int, kind: str, side: int, moves: list[Move]) -> None:
        if kind == "C":
            self._chariot(r, c, side, moves)
        elif kind == "P":
            self._cannon(r, c, side, moves)
        elif kind == "M":
            self._horse(r, c, side, moves)
        elif kind == "S":
            self._elephant(r, c, side, moves)
        elif kind in ("G", "K"):
            self._palace_piece(r, c, side, moves)
        elif kind == "J":
            self._soldier(r, c, side, moves)

    def _chariot(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            while self._add(r, c, nr, nc, side, moves):
                nr += dr
                nc += dc
        # Palace diagonal slides (only between connected diagonal points, same palace).
        if on_palace_diagonal(r, c):
            for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                nr, nc = r + dr, c + dc
                while in_board(nr, nc) and on_palace_diagonal(nr, nc):
                    same_palace = (r <= 2 and nr <= 2) or (r >= 7 and nr >= 7)
                    if not same_palace:
                        break
                    if not self._add(r, c, nr, nc, side, moves):
                        break
                    nr += dr
                    nc += dc

    def _cannon(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        # Cannon jumps exactly one screen piece; cannot screen over or capture
        # another cannon.
        g = self._g
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            jumped = False
            while in_board(nr, nc):
                t = g[nr][nc]
                if not jumped:
                    if t is not None:
                        if t[0] == "P":
                            break
                        jumped = True
                else:
                    if t is None:
                        moves.append(Move(r, c, nr, nc, None))
                    else:
                        if t[0] != "P" and t[1] != side:
                            moves.append(Move(r, c, nr, nc, t[0]))
                        break
                nr += dr
                nc += dc
        # Cannon may also jump along a palace diagonal over a screen.
        if on_palace_diagonal(r, c):
            for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                nr, nc = r + dr, c + dc
                jumped = False
                while in_board(nr, nc) and on_palace_diagonal(nr, nc):
                    same_palace = (r <= 2 and nr <= 2) or (r >= 7 and nr >= 7)
                    if not same_palace:
                        break
                    t = g[nr][nc]
                    if not jumped:
                        if t is not None:
                            if t[0] == "P":
                                break
                            jumped = True
                    else:
                        if t is None:
                            moves.append(Move(r, c, nr, nc, None))
                        else:
                            if t[0] != "P" and t[1] != side:
                                moves.append(Move(r, c, nr, nc, t[0]))
                            break
                    nr += dr
                    nc += dc

    def _horse(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        # One orthogonal step then one diagonal; the orthogonal step square must
        # be empty (leg-block / 멱).
        legs = (
            (-1, 0, -2, -1), (-1, 0, -2, 1),
            (1, 0, 2, -1), (1, 0, 2, 1),
            (0, -1, -1, -2), (0, -1, 1, -2),
            (0, 1, -1, 2), (0, 1, 1, 2),
        )
        g = self._g
        for br, bc, dr, dc in legs:
            if in_board(r + br, c + bc) and g[r + br][c + bc] is None:
                self._add(r, c, r + dr, c + dc, side, moves)

    def _elephant(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        # One orthogonal step, then two diagonal steps in the same direction.
        # Both intermediate squares (the orthogonal step and the first diagonal
        # step) must be empty, or the elephant's "leg" is blocked.
        # Tuple = (b1r, b1c, b2r, b2c, dr, dc): b1 = orthogonal step,
        # b2 = first diagonal step, (dr, dc) = landing square.
        legs = (
            (-1, 0, -2, -1, -3, -2), (-1, 0, -2, 1, -3, 2),
            (1, 0, 2, -1, 3, -2), (1, 0, 2, 1, 3, 2),
            (0, -1, -1, -2, -2, -3), (0, -1, 1, -2, 2, -3),
            (0, 1, -1, 2, -2, 3), (0, 1, 1, 2, 2, 3),
        )
        g = self._g
        for b1r, b1c, b2r, b2c, dr, dc in legs:
            if (
                in_board(r + b1r, c + b1c) and g[r + b1r][c + b1c] is None
                and in_board(r + b2r, c + b2c) and g[r + b2r][c + b2c] is None
            ):
                self._add(r, c, r + dr, c + dc, side, moves)

    def _palace_piece(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        # General and guard: one orthogonal step inside palace, plus diagonal
        # steps along connected diagonal points.
        g = self._g
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if in_palace(nr, nc, side):
                t = g[nr][nc]
                if t is None or t[1] != side:
                    moves.append(Move(r, c, nr, nc, None if t is None else t[0]))
        if on_palace_diagonal(r, c):
            for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                nr, nc = r + dr, c + dc
                if in_palace(nr, nc, side) and on_palace_diagonal(nr, nc):
                    t = g[nr][nc]
                    if t is None or t[1] != side:
                        moves.append(Move(r, c, nr, nc, None if t is None else t[0]))

    def _soldier(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        # Soldiers move forward or sideways, never backward.
        forward = 1 if side == HAN else -1
        self._add(r, c, r + forward, c, side, moves)
        self._add(r, c, r, c - 1, side, moves)
        self._add(r, c, r, c + 1, side, moves)
        if on_palace_diagonal(r, c):
            for dr, dc in ((forward, -1), (forward, 1)):
                if on_palace_diagonal(r + dr, c + dc):
                    self._add(r, c, r + dr, c + dc, side, moves)

    # -------------------------------------------------------------- legality
    def generals_face(self) -> bool:
        """True if the two generals share a file with nothing between (빅장)."""
        gh = self._king[HAN]
        gc = self._king[CHO]
        if gh is None or gc is None or gh[1] != gc[1]:
            return False
        col = gh[1]
        lo, hi = sorted((gh[0], gc[0]))
        g = self._g
        for r in range(lo + 1, hi):
            if g[r][col] is not None:
                return False
        return True

    def is_attacked(self, r: int, c: int, by_side: int) -> bool:
        """Can a `by_side` piece legally MOVE onto (r, c)?

        Generated from the move list, so a square occupied by a `by_side` piece
        is never "attacked": you cannot move onto your own piece.

        This is NOT the same question as :meth:`fast_is_attacked`, which asks
        whether `by_side` *bears on* the square (defence included). The two
        agree exactly on empty and enemy-occupied squares and deliberately
        disagree on `by_side`-occupied ones -- verified across 110k queries in
        ``tests/test_parity.py``. Use :meth:`controls` for the union oracle.
        """
        g = self._g
        for sr in range(ROWS):
            row = g[sr]
            for sc in range(COLS):
                p = row[sc]
                if p is None or p[1] != by_side:
                    continue
                buf: list[Move] = []
                self._piece_moves(sr, sc, p[0], by_side, buf)
                for mv in buf:
                    if mv.tr == r and mv.tc == c:
                        return True
        return False

    def controls(self, r: int, c: int, by_side: int) -> bool:
        """Reference implementation of "does `by_side` bear on (r, c)?".

        Same question as :meth:`fast_is_attacked`, answered the slow way: a
        `by_side` occupant is temporarily flipped to the other side (keeping its
        kind, so ray blocking and the cannon-cannot-take-a-cannon rule are
        preserved) and the move generator is asked whether anything can land
        there. Used as the test oracle; never used in the search.
        """
        p = self._g[r][c]
        if p is None or p[1] != by_side:
            return self.is_attacked(r, c, by_side)
        self._set_cell(r, c, (p[0], -by_side))
        try:
            return self.is_attacked(r, c, by_side)
        finally:
            self._set_cell(r, c, p)

    def fast_is_attacked(self, r: int, c: int, by_side: int) -> bool:
        """Does `by_side` bear on (r, c)? Defence counts.

        Unlike :meth:`is_attacked`, a square holding a `by_side` piece counts as
        attacked when another `by_side` piece covers it -- that is precisely the
        "is my piece defended?" question the evaluator asks. :meth:`controls` is
        the slow oracle for this semantics.

        Uses the Cython accelerator when available; the pure-Python fallback is
        bit-for-bit identical (verified in ``tests/test_parity.py``).
        """
        if not _HAVE_CATTACK:
            return self._py_fast_is_attacked(r, c, by_side)
        bs = 1 if by_side == HAN else 2
        return _c_fast_is_attacked(self._pc, self._sd, r, c, bs)

    def _py_fast_is_attacked(self, r: int, c: int, by_side: int) -> bool:
        """Fast attack test: look OUTWARD from (r, c) for each attacker type,
        instead of generating every enemy move. Differentially verified to
        match is_attacked() exactly across thousands of positions.
        """
        g = self._g

        # --- Chariot (orthogonal rays): first piece met on a ray -----------
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            while 0 <= nr < ROWS and 0 <= nc < COLS and g[nr][nc] is None:
                nr += dr
                nc += dc
            if 0 <= nr < ROWS and 0 <= nc < COLS:
                p = g[nr][nc]
                if p[1] == by_side and p[0] == "C":
                    return True

        # --- Chariot palace-diagonal slide ---------------------------------
        if on_palace_diagonal(r, c):
            for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                nr, nc = r + dr, c + dc
                while (
                    0 <= nr < ROWS and 0 <= nc < COLS
                    and on_palace_diagonal(nr, nc)
                    and ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7))
                    and g[nr][nc] is None
                ):
                    nr += dr
                    nc += dc
                if (
                    0 <= nr < ROWS and 0 <= nc < COLS
                    and on_palace_diagonal(nr, nc)
                    and ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7))
                ):
                    p = g[nr][nc]
                    if p[1] == by_side and p[0] == "C":
                        return True

        target = g[r][c]
        target_is_cannon = target is not None and target[0] == "P"
        if not target_is_cannon:
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nr, nc = r + dr, c + dc
                while 0 <= nr < ROWS and 0 <= nc < COLS and g[nr][nc] is None:
                    nr += dr
                    nc += dc
                if not (0 <= nr < ROWS and 0 <= nc < COLS):
                    continue
                if g[nr][nc][0] == "P":
                    continue  # a cannon cannot be the screen
                nr += dr
                nc += dc
                while 0 <= nr < ROWS and 0 <= nc < COLS and g[nr][nc] is None:
                    nr += dr
                    nc += dc
                if 0 <= nr < ROWS and 0 <= nc < COLS:
                    p = g[nr][nc]
                    if p[1] == by_side and p[0] == "P":
                        return True

            # Cannon palace-diagonal jump: only relevant on palace diagonal
            # points. Walk diagonals staying on connected diagonal points within
            # one palace; exactly one non-cannon screen, then an enemy cannon.
            if on_palace_diagonal(r, c):
                for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    nr, nc = r + dr, c + dc
                    # advance to first occupied diagonal point
                    while (
                        0 <= nr < ROWS and 0 <= nc < COLS
                        and on_palace_diagonal(nr, nc)
                        and ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7))
                        and g[nr][nc] is None
                    ):
                        nr += dr
                        nc += dc
                    if not (
                        0 <= nr < ROWS and 0 <= nc < COLS
                        and on_palace_diagonal(nr, nc)
                        and ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7))
                    ):
                        continue
                    if g[nr][nc][0] == "P":
                        continue  # screen can't be a cannon
                    nr += dr
                    nc += dc
                    while (
                        0 <= nr < ROWS and 0 <= nc < COLS
                        and on_palace_diagonal(nr, nc)
                        and ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7))
                        and g[nr][nc] is None
                    ):
                        nr += dr
                        nc += dc
                    if (
                        0 <= nr < ROWS and 0 <= nc < COLS
                        and on_palace_diagonal(nr, nc)
                        and ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7))
                    ):
                        p = g[nr][nc]
                        if p[1] == by_side and p[0] == "P":
                            return True

        # --- Horse: attacker a horse-move away, leg (adjacent to horse) empty
        for sr, sc, lr, lc in (
            (r - 2, c - 1, r - 1, c - 1), (r - 2, c + 1, r - 1, c + 1),
            (r + 2, c - 1, r + 1, c - 1), (r + 2, c + 1, r + 1, c + 1),
            (r - 1, c - 2, r - 1, c - 1), (r + 1, c - 2, r + 1, c - 1),
            (r - 1, c + 2, r - 1, c + 1), (r + 1, c + 2, r + 1, c + 1),
        ):
            if 0 <= sr < ROWS and 0 <= sc < COLS:
                p = g[sr][sc]
                if p is not None and p[1] == by_side and p[0] == "M":
                    if g[lr][lc] is None:
                        return True

        # --- Elephant: 1 orthogonal + 2 diagonal. The two intermediate squares
        # must be empty, measured FROM THE ELEPHANT (like the move generator):
        # leg1 = one orthogonal step out of the elephant toward the target,
        # leg2 = the first diagonal step. We enumerate the 8 squares an elephant
        # could attack (r,c) from, and for each, recompute its legs forward.
        for sr, sc, dr_, dc_ in (
            (r - 3, c - 2, 1, 0), (r - 3, c + 2, 1, 0),   # elephant above, lands going down
            (r + 3, c - 2, -1, 0), (r + 3, c + 2, -1, 0), # elephant below, going up
            (r - 2, c - 3, 0, 1), (r + 2, c - 3, 0, 1),   # elephant left, going right
            (r - 2, c + 3, 0, -1), (r + 2, c + 3, 0, -1), # elephant right, going left
        ):
            if not (0 <= sr < ROWS and 0 <= sc < COLS):
                continue
            p = g[sr][sc]
            if p is None or p[1] != by_side or p[0] != "S":
                continue
            # leg1: orthogonal step out of the elephant; leg2: first diagonal.
            l1r, l1c = sr + dr_, sc + dc_
            # diagonal direction is toward the target on the other axis
            if dr_ != 0:  # moving vertically; diagonal shifts column toward c
                ddc = 1 if c > sc else -1
                l2r, l2c = l1r + dr_, l1c + ddc
            else:         # moving horizontally; diagonal shifts row toward r
                ddr = 1 if r > sr else -1
                l2r, l2c = l1r + ddr, l1c + dc_
            if g[l1r][l1c] is None and g[l2r][l2c] is None:
                return True

        # --- Soldier: attacks forward (toward enemy) and sideways ----------
        # A by_side soldier moving forward attacks the square ahead of it, so it
        # threatens (r,c) if it sits one step "behind" (r,c) in its forward dir,
        # or directly beside (r,c). Plus palace forward-diagonal steps.
        sfwd = 1 if by_side == HAN else -1
        for sr, sc in ((r - sfwd, c), (r, c - 1), (r, c + 1)):
            if 0 <= sr < ROWS and 0 <= sc < COLS:
                p = g[sr][sc]
                if p is not None and p[1] == by_side and p[0] == "J":
                    return True
        # Soldier palace-diagonal attack: a soldier on a palace diagonal point
        # one forward-diagonal step away from (r,c), with (r,c) also a diagonal
        # point, attacks it.
        if on_palace_diagonal(r, c):
            for sr, sc in ((r - sfwd, c - 1), (r - sfwd, c + 1)):
                if 0 <= sr < ROWS and 0 <= sc < COLS and on_palace_diagonal(sr, sc):
                    p = g[sr][sc]
                    if p is not None and p[1] == by_side and p[0] == "J":
                        return True

        # --- General / Guard: must be inside the enemy(by_side) palace and an
        # adjacent legal palace step (orthogonal, or diagonal along palace
        # diagonal connections) onto (r, c).
        if in_palace(r, c, by_side):
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                sr, sc = r + dr, c + dc
                if in_palace(sr, sc, by_side):
                    p = g[sr][sc]
                    if p is not None and p[1] == by_side and p[0] in ("K", "G"):
                        return True
            if on_palace_diagonal(r, c):
                for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    sr, sc = r + dr, c + dc
                    if in_palace(sr, sc, by_side) and on_palace_diagonal(sr, sc):
                        p = g[sr][sc]
                        if p is not None and p[1] == by_side and p[0] in ("K", "G"):
                            return True

        return False

    def in_check(self, side: int) -> bool:
        # Note: In Korean janggi, two generals facing on an open file ("대궁")
        # is NOT illegal — it is "빅장", a legal move that, if not resolved by
        # the opponent next turn, results in a draw / score decision. So we do
        # NOT treat facing generals as check or as an illegal position here.
        g = self._king[side]
        if g is None:
            return True
        return self.fast_is_attacked(g[0], g[1], -side)

    def legal_moves(self, side: int | None = None) -> list[Move]:
        if side is None:
            side = self._stm
        legal: list[Move] = []
        for mv in self.generate_pseudo(side):
            self.make(mv)
            # A move is illegal only if it leaves one's own general in check.
            # Facing generals (빅장) is allowed in Korean janggi.
            ok = not self.in_check(side)
            self.unmake()
            if ok:
                legal.append(mv)
        return legal

    # ---------------------------------------------------------------- output
    def __str__(self) -> str:
        symbols_han = {"K": "漢", "C": "車", "P": "包", "M": "馬", "S": "象", "G": "士", "J": "兵"}
        symbols_cho = {"K": "楚", "C": "車", "P": "砲", "M": "馬", "S": "象", "G": "士", "J": "卒"}
        lines = []
        for r in range(ROWS):
            cells = []
            for c in range(COLS):
                p = self._g[r][c]
                if p is None:
                    cells.append(" · ")
                else:
                    table = symbols_han if p[1] == HAN else symbols_cho
                    cells.append(f" {table[p[0]]} ")
            lines.append("".join(cells))
        return "\n".join(lines)
