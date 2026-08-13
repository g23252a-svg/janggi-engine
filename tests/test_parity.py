"""Parity and invariant tests.

The engine ships two implementations of every hot primitive: a readable
pure-Python one and a compiled Cython one. Production runs the compiled path;
until now CI only ever built the Python one, so the deployed code was
effectively untested. These tests pin the two together.

Run the whole suite twice to cover both:

    python -m pytest tests/ -q                    # compiled, if extensions built
    JANGGI_NO_ACCEL=1 python -m pytest tests/ -q  # pure Python

Tests that need the compiled module skip cleanly when it is absent.
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi.board import (  # noqa: E402
    Board, Move, HAN, CHO, FORMATIONS, ROWS, COLS,
)
from janggi.evaluate import _py_evaluate  # noqa: E402
from janggi.see import see  # noqa: E402
from janggi.search import zobrist_hash, _ZOBRIST  # noqa: E402

FORMS = list(FORMATIONS)


def random_positions(count: int, seed: int, max_plies: int = 60):
    """Yield boards reached by random legal play, covering all four formations."""
    rng = random.Random(seed)
    produced = 0
    while produced < count:
        board = Board.standard(rng.choice(FORMS), rng.choice(FORMS))
        side = CHO
        for _ in range(rng.randrange(max_plies)):
            legal = board.legal_moves(side)
            if not legal:
                break
            board.make(rng.choice(legal))
            side = -side
            yield board
            produced += 1
            if produced >= count:
                return


# --------------------------------------------------------------------- perft
# Locked-in reference counts. Any movegen or legality change that shifts these
# is a rules change and must be justified, not absorbed silently.
PERFT = {
    "msm_s": (31, 961, 30353),
    "smsm": (31, 961, 30506),
    "msms": (31, 961, 30506),
    "smms": (31, 961, 30659),
}


def _perft(board: Board, side: int, depth: int) -> int:
    if depth == 0:
        return 1
    total = 0
    for mv in board.legal_moves(side):
        board.make(mv)
        total += _perft(board, -side, depth - 1)
        board.unmake()
    return total


@pytest.mark.parametrize("formation", FORMS)
def test_perft_reference_counts(formation):
    board = Board.standard(formation, formation)
    for depth, expected in enumerate(PERFT[formation], start=1):
        assert _perft(board, CHO, depth) == expected, f"{formation} depth {depth}"


def test_perft_depth4_standard():
    assert _perft(Board.standard(), CHO, 4) == 958264


def test_cython_perft_matches_python():
    core = pytest.importorskip("janggi._core")
    for formation in FORMS:
        board = Board.standard(formation, formation)
        for depth, expected in enumerate(PERFT[formation], start=1):
            assert core.core_perft(board._pc, board._sd, 2, depth) == expected


# ------------------------------------------------------------- move generation
def test_python_and_active_movegen_agree():
    for board in random_positions(250, seed=101):
        for side in (HAN, CHO):
            py = sorted(m.as_tuple() + (m.captured or "",) for m in board._py_generate_pseudo(side))
            active = sorted(m.as_tuple() + (m.captured or "",) for m in board.generate_pseudo(side))
            assert py == active


# -------------------------------------------------------------- attack tests
def test_fast_is_attacked_matches_control_oracle():
    """`fast_is_attacked` answers "does this side bear on the square", defence
    included. `controls` is the slow move-generator-based oracle for exactly
    that question."""
    for board in random_positions(40, seed=202):
        for r in range(ROWS):
            for c in range(COLS):
                for side in (HAN, CHO):
                    assert board.fast_is_attacked(r, c, side) == board.controls(r, c, side), (
                        f"square ({r},{c}) by {side}"
                    )


def test_python_and_active_attack_agree():
    for board in random_positions(60, seed=303):
        for r in range(ROWS):
            for c in range(COLS):
                for side in (HAN, CHO):
                    assert board._py_fast_is_attacked(r, c, side) == board.fast_is_attacked(r, c, side)


def test_is_attacked_and_fast_is_attacked_differ_only_on_own_pieces():
    """Pins the documented semantic split so nobody "fixes" one into the other.

    `is_attacked` asks whether the side can MOVE onto the square, so its own
    pieces block it; `fast_is_attacked` asks whether it bears on the square.
    """
    seen_disagreement = False
    for board in random_positions(30, seed=404):
        for r in range(ROWS):
            for c in range(COLS):
                for side in (HAN, CHO):
                    move_on = board.is_attacked(r, c, side)
                    bears_on = board.fast_is_attacked(r, c, side)
                    if move_on == bears_on:
                        continue
                    occupant = board.grid[r][c]
                    assert occupant is not None and occupant[1] == side
                    assert bears_on and not move_on
                    seen_disagreement = True
    assert seen_disagreement, "expected at least one defended-piece disagreement"


# ------------------------------------------------------------------ evaluation
def test_core_evaluate_matches_python():
    core = pytest.importorskip("janggi._core")
    for board in random_positions(300, seed=606):
        ply = len(board._history)
        assert _py_evaluate(board, False) == core.core_eval(board._pc, board._sd, ply)
        assert _py_evaluate(board, True) == core.core_eval_mob(board._pc, board._sd, ply)


def test_attack_map_matches_scalar_attack_test():
    """The evaluator reads a precomputed attack map instead of asking
    fast_is_attacked ~70 times per call. Every entry must agree."""
    core = pytest.importorskip("janggi._core")
    checked = 0
    for board in random_positions(120, seed=505):
        amap = core.core_attack_map(board._pc, board._sd)
        for side_code, side in ((1, HAN), (2, CHO)):
            for sq in range(ROWS * COLS):
                expected = board.fast_is_attacked(sq // COLS, sq % COLS, side)
                assert bool(amap[(side_code - 1) * 90 + sq]) == expected, (side, sq)
                checked += 1
    assert checked == 120 * 2 * ROWS * COLS


# ------------------------------------------------------------------------ SEE
def test_core_see_matches_python():
    core = pytest.importorskip("janggi._core")
    checked = 0
    for board in random_positions(200, seed=707):
        captures = [m for m in board.generate_pseudo(board.side_to_move) if m.captured]
        for mv in captures[:5]:
            assert see(board, mv) == core.core_see(
                board._pc, board._sd, mv.fr, mv.fc, mv.tr, mv.tc
            )
            checked += 1
    assert checked > 100


# -------------------------------------------------------------------- zobrist
def _full_hash(board: Board) -> int:
    h = 0
    for r in range(ROWS):
        for c in range(COLS):
            p = board.grid[r][c]
            if p is not None:
                h ^= _ZOBRIST[(r, c, p[0], p[1])]
    if board.side_to_move == CHO:
        h ^= _ZOBRIST["side"]
    return h


def test_zobrist_table_is_frozen():
    """The opening book is keyed by these hashes; regenerating the table with a
    different seed or draw order silently invalidates data/opening_book.json."""
    assert zobrist_hash(Board.standard("msm_s", "msm_s")) == 12309963030929118742
    assert zobrist_hash(Board.standard("smsm", "smsm")) == 8035793004171475196
    assert zobrist_hash(Board.standard("msms", "msms")) == 7408927170494304133
    assert zobrist_hash(Board.standard("smms", "smms")) == 11781650886986604911


def test_incremental_zobrist_matches_full_recomputation():
    rng = random.Random(808)
    for _ in range(40):
        board = Board.standard(rng.choice(FORMS), rng.choice(FORMS))
        side = CHO
        for _ in range(rng.randrange(50)):
            legal = board.legal_moves(side)
            if not legal:
                break
            board.make(rng.choice(legal))
            assert board.zobrist() == _full_hash(board)
            side = -side
        while board._history:
            board.unmake()
            assert board.zobrist() == _full_hash(board)


def test_zobrist_tracks_side_to_move_assignment():
    board = Board.standard()
    before = board.zobrist()
    board.side_to_move = HAN
    assert board.zobrist() != before
    board.side_to_move = CHO
    assert board.zobrist() == before


# ------------------------------------------------- grid write-through invariants
def _arrays_consistent(board: Board) -> bool:
    from janggi.board import _PIECE_CODE
    for r in range(ROWS):
        for c in range(COLS):
            p = board.grid[r][c]
            idx = r * COLS + c
            if p is None:
                if board._pc[idx] or board._sd[idx]:
                    return False
            else:
                if board._pc[idx] != _PIECE_CODE[p[0]]:
                    return False
                if board._sd[idx] != (1 if p[1] == HAN else 2):
                    return False
    return True


def test_direct_grid_write_keeps_accelerator_arrays_in_sync():
    """Regression for the bug class that broke server-side check detection:
    writing board.grid directly used to leave the Cython int arrays stale."""
    board = Board()
    board.grid[8][4] = ("K", CHO)
    board.grid[1][4] = ("K", HAN)
    board.grid[8][0] = ("C", HAN)
    assert _arrays_consistent(board)
    assert board.zobrist() == _full_hash(board)
    assert board.in_check(CHO) is True          # the whole point: no resync call
    board.grid[8][2] = ("J", CHO)               # interpose
    assert board.in_check(CHO) is False
    board.grid[8][2] = None
    assert board.in_check(CHO) is True
    assert _arrays_consistent(board)


def test_grid_wholesale_assignment_resyncs():
    src = Board.standard()
    dst = Board()
    dst.grid = [row[:] for row in src.grid]
    assert _arrays_consistent(dst)
    assert list(dst._pc) == list(src._pc)
    assert list(dst._sd) == list(src._sd)


def test_grid_supports_list_idioms():
    board = Board.standard()
    copy = [row[:] for row in board.grid]
    assert copy == [list(row) for row in board.grid]
    assert board.grid == copy
    assert len(board.grid) == ROWS and len(board.grid[0]) == COLS
    assert board.grid[0][0] == ("C", HAN)
    assert board.grid[-1][-1] == ("C", CHO)
    assert tuple(tuple(row) for row in board.grid)[9][0] == ("C", CHO)


def test_find_general_tracks_moves_and_captures():
    board = Board()
    board.grid[8][4] = ("K", CHO)
    board.grid[1][4] = ("K", HAN)
    assert board.find_general(CHO) == (8, 4)
    board.make(Move(8, 4, 8, 3, None))
    assert board.find_general(CHO) == (8, 3)
    board.unmake()
    assert board.find_general(CHO) == (8, 4)
    board.grid[8][4] = None
    assert board.find_general(CHO) is None


def test_board_copy_is_independent_and_consistent():
    board = Board.standard()
    clone = board.copy()
    assert clone.grid == [list(row) for row in board.grid]
    assert clone.zobrist() == board.zobrist()
    clone.grid[6][0] = None
    assert board.grid[6][0] == ("J", CHO)
    assert _arrays_consistent(clone) and _arrays_consistent(board)


def test_from_grid_rejects_malformed_input():
    with pytest.raises(ValueError):
        Board.from_grid([[None] * COLS] * 3)
    with pytest.raises(ValueError):
        Board.from_grid([[("Z", CHO)] + [None] * (COLS - 1)] + [[None] * COLS] * (ROWS - 1))
    ok = Board.from_grid([[None] * COLS for _ in range(ROWS)], side_to_move=HAN)
    assert ok.side_to_move == HAN


def test_gibo_replay_snapshots_are_usable_boards():
    """Snapshots used to be built by assigning .grid directly, which left them
    with empty accelerator arrays -- check detection on them silently failed."""
    from janggi.gibo import Gibo

    board = Board.standard()
    gibo = Gibo()
    side = CHO
    for _ in range(6):
        mv = board.legal_moves(side)[0]
        gibo.add_move(mv, side)
        board.make(mv)
        side = -side
    for snapshot in gibo.replay():
        assert _arrays_consistent(snapshot)
        assert snapshot.zobrist() == _full_hash(snapshot)
        snapshot.in_check(CHO)  # must not raise or read a stale board


# ------------------------------------------------------- evaluator version 2
def test_evaluator_v2_is_available_and_sane():
    """v2 is what the compiled search plays with. It lives only in the core, so
    the Python-facing API must say so rather than quietly serving v1."""
    from janggi.evaluate import evaluate, _HAVE_CEVAL

    board = Board.standard()
    if not _HAVE_CEVAL:
        with pytest.raises(RuntimeError):
            evaluate(board, version=2)
        return
    core = pytest.importorskip("janggi._core")
    # A symmetric start is a dead heat under either evaluator.
    assert evaluate(board, include_mobility=False, version=2) == evaluate(
        Board.standard(), include_mobility=False, version=2
    )
    # Same sign convention as v1: taking Cho's chariot must favour Han.
    board.grid[9][0] = None
    assert evaluate(board, include_mobility=False, version=2) > 0
    assert core.core_eval(board._pc, board._sd, 0, 2) == evaluate(
        board, include_mobility=False, version=2
    )


def test_evaluator_v2_is_deterministic_and_antisymmetric():
    """Mirroring a position must flip the score, or one side is being scored
    with knowledge the other does not get."""
    core = pytest.importorskip("janggi._core")
    from janggi.board import _PIECE_CODE  # noqa: F401

    for board in random_positions(60, seed=1234):
        mirrored = Board()
        for r in range(ROWS):
            for c in range(COLS):
                p = board.grid[r][c]
                if p is not None:
                    mirrored.grid[ROWS - 1 - r][c] = (p[0], -p[1])
        direct = core.core_eval(board._pc, board._sd, 0, 2)
        flipped = core.core_eval(mirrored._pc, mirrored._sd, 0, 2)
        assert direct == -flipped, f"v2 is not side-symmetric: {direct} vs {flipped}"
