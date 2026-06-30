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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

HAN = 1
CHO = -1

ROWS = 10
COLS = 9

# --- Cython fast-attack acceleration (optional, falls back to pure Python) ---
try:
    from janggi._attack import fast_is_attacked_c as _c_fast_is_attacked
    import array as _array
    _HAVE_CATTACK = True
except Exception:
    _HAVE_CATTACK = False

_PIECE_CODE = {"C": 1, "P": 2, "M": 3, "S": 4, "J": 5, "K": 6, "G": 7}

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
    if c not in PALACE_COLS:
        return False
    return r in (HAN_PALACE_ROWS if side == HAN else CHO_PALACE_ROWS)


def on_palace_diagonal(r: int, c: int) -> bool:
    return (r, c) in PALACE_DIAGONAL_POINTS


# Standard formations: order of pieces on the inner back-row files [c1, c2, c6, c7].
# Outer files (0, 8) are always chariots; files 3, 5 guards; file 4 general row.
FORMATIONS = {
    "msm_s": ["M", "S", "S", "M"],   # 마상상마
    "smsm": ["S", "M", "S", "M"],    # 상마상마
    "msms": ["M", "S", "M", "S"],    # 마상마상
    "smms": ["S", "M", "M", "S"],    # 상마마상
}


class Board:
    """Mutable board with make/unmake for efficient search."""

    __slots__ = ("grid", "side_to_move", "_history", "_pc", "_sd")

    def __init__(self) -> None:
        # grid[r][c] is None or a (type, side) tuple.
        self.grid: list[list[tuple[str, int] | None]] = [
            [None] * COLS for _ in range(ROWS)
        ]
        self.side_to_move = CHO  # Cho always moves first.
        self._history: list[tuple[Move, tuple[str, int] | None]] = []
        # Parallel integer board for the Cython attack accelerator.
        # _pc[r*COLS+c]: piece code (0 empty), _sd: side code (0/1/2).
        if _HAVE_CATTACK:
            self._pc = _array.array('i', bytes(4 * ROWS * COLS))
            self._sd = _array.array('i', bytes(4 * ROWS * COLS))

    # ------------------------------------------------------------------ setup
    @classmethod
    def standard(cls, cho_formation: str = "msm_s", han_formation: str = "msm_s") -> "Board":
        b = cls()
        b._place_side(HAN, FORMATIONS[han_formation])
        b._place_side(CHO, FORMATIONS[cho_formation])
        return b

    def _place_side(self, side: int, formation: list[str]) -> None:
        if side == HAN:
            back, pal, po, jol = 0, 1, 2, 3
        else:
            back, pal, po, jol = 9, 8, 7, 6
        g = self.grid
        g[back][0] = ("C", side)
        g[back][8] = ("C", side)
        g[back][3] = ("G", side)
        g[back][5] = ("G", side)
        g[back][1] = (formation[0], side)
        g[back][2] = (formation[1], side)
        g[back][6] = (formation[2], side)
        g[back][7] = (formation[3], side)
        g[pal][4] = ("K", side)
        g[po][1] = ("P", side)
        g[po][7] = ("P", side)
        for c in (0, 2, 4, 6, 8):
            g[jol][c] = ("J", side)
        if _HAVE_CATTACK:
            # Re-sync the whole board after placement (covers all pieces).
            for _r in range(ROWS):
                for _c in range(COLS):
                    self._sync_cell(_r, _c, g[_r][_c])

    # -------------------------------------------------------------- accessors
    def piece_at(self, r: int, c: int) -> tuple[str, int] | None:
        return self.grid[r][c]

    def find_general(self, side: int) -> tuple[int, int] | None:
        for r in range(ROWS):
            row = self.grid[r]
            for c in range(COLS):
                p = row[c]
                if p is not None and p[0] == "K" and p[1] == side:
                    return (r, c)
        return None

    # ----------------------------------------------------------- make/unmake
    def _sync_cell(self, r: int, c: int, p) -> None:
        """Mirror one square into the parallel int arrays."""
        idx = r * COLS + c
        if p is None:
            self._pc[idx] = 0
            self._sd[idx] = 0
        else:
            self._pc[idx] = _PIECE_CODE[p[0]]
            self._sd[idx] = 1 if p[1] == HAN else 2

    def make(self, mv: Move) -> None:
        g = self.grid
        moving = g[mv.fr][mv.fc]
        captured = g[mv.tr][mv.tc]
        self._history.append((mv, captured))
        g[mv.tr][mv.tc] = moving
        g[mv.fr][mv.fc] = None
        if _HAVE_CATTACK:
            ti = mv.tr * COLS + mv.tc
            fi = mv.fr * COLS + mv.fc
            self._pc[ti] = self._pc[fi]; self._sd[ti] = self._sd[fi]
            self._pc[fi] = 0; self._sd[fi] = 0
        self.side_to_move = -self.side_to_move

    def unmake(self) -> None:
        mv, captured = self._history.pop()
        g = self.grid
        moving = g[mv.tr][mv.tc]
        g[mv.fr][mv.fc] = moving
        g[mv.tr][mv.tc] = captured
        if _HAVE_CATTACK:
            ti = mv.tr * COLS + mv.tc
            fi = mv.fr * COLS + mv.fc
            self._pc[fi] = self._pc[ti]; self._sd[fi] = self._sd[ti]
            if captured is None:
                self._pc[ti] = 0; self._sd[ti] = 0
            else:
                self._pc[ti] = _PIECE_CODE[captured[0]]
                self._sd[ti] = 1 if captured[1] == HAN else 2
        self.side_to_move = -self.side_to_move

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
        """All moves ignoring self-check and the facing-generals rule."""
        moves: list[Move] = []
        g = self.grid
        for r in range(ROWS):
            for c in range(COLS):
                p = g[r][c]
                if p is None or p[1] != side:
                    continue
                self._piece_moves(r, c, p[0], side, moves)
        return moves

    def _add(self, r: int, c: int, nr: int, nc: int, side: int, moves: list[Move]) -> bool:
        """Append a move to (nr, nc) if legal landing; return True if empty (slide can continue)."""
        if not in_board(nr, nc):
            return False
        target = self.grid[nr][nc]
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
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            jumped = False
            while in_board(nr, nc):
                t = self.grid[nr][nc]
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
                    t = self.grid[nr][nc]
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
        for br, bc, dr, dc in legs:
            if in_board(r + br, c + bc) and self.grid[r + br][c + bc] is None:
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
        for b1r, b1c, b2r, b2c, dr, dc in legs:
            if (
                in_board(r + b1r, c + b1c) and self.grid[r + b1r][c + b1c] is None
                and in_board(r + b2r, c + b2c) and self.grid[r + b2r][c + b2c] is None
            ):
                self._add(r, c, r + dr, c + dc, side, moves)

    def _palace_piece(self, r: int, c: int, side: int, moves: list[Move]) -> None:
        # General and guard: one orthogonal step inside palace, plus diagonal
        # steps along connected diagonal points.
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if in_palace(nr, nc, side):
                t = self.grid[nr][nc]
                if t is None or t[1] != side:
                    moves.append(Move(r, c, nr, nc, None if t is None else t[0]))
        if on_palace_diagonal(r, c):
            for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                nr, nc = r + dr, c + dc
                if in_palace(nr, nc, side) and on_palace_diagonal(nr, nc):
                    t = self.grid[nr][nc]
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
        """True if the two generals share a file with nothing between (illegal)."""
        gh = self.find_general(HAN)
        gc = self.find_general(CHO)
        if gh is None or gc is None or gh[1] != gc[1]:
            return False
        col = gh[1]
        lo, hi = sorted((gh[0], gc[0]))
        for r in range(lo + 1, hi):
            if self.grid[r][col] is not None:
                return False
        return True

    def is_attacked(self, r: int, c: int, by_side: int) -> bool:
        """ORACLE implementation (correctness reference). Is square (r, c)
        attacked by any `by_side` piece? Generates each piece's moves and bails
        as soon as the target square appears. Slower but verified; fast_is_
        attacked is differentially tested against this.
        """
        g = self.grid
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

    def fast_is_attacked(self, r: int, c: int, by_side: int) -> bool:
        """Attack test. Uses the Cython accelerator when available; otherwise
        falls back to the pure-Python implementation (identical results)."""
        if not _HAVE_CATTACK:
            return self._py_fast_is_attacked(r, c, by_side)
        bs = 1 if by_side == HAN else 2
        return _c_fast_is_attacked(self._pc, self._sd, r, c, bs)

    def _py_fast_is_attacked(self, r: int, c: int, by_side: int) -> bool:
        """Fast attack test: look OUTWARD from (r, c) for each attacker type,
        instead of generating every enemy move. Differentially verified to
        match is_attacked() exactly across thousands of positions.
        """
        g = self.grid

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
        g = self.find_general(side)
        if g is None:
            return True
        return self.fast_is_attacked(g[0], g[1], -side)

    def legal_moves(self, side: int | None = None) -> list[Move]:
        if side is None:
            side = self.side_to_move
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
                p = self.grid[r][c]
                if p is None:
                    cells.append(" · ")
                else:
                    table = symbols_han if p[1] == HAN else symbols_cho
                    cells.append(f" {table[p[0]]} ")
            lines.append("".join(cells))
        return "\n".join(lines)
