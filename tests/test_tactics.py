"""Tactical regression suite.

Every position here was mined from random play and then CERTIFIED by an
exhaustive prover in tests/_mate_prover.py that never consults the engine, so
these tests measure the search rather than agreeing with it.

They are the safety net for search pruning: null-move, futility, late-move
pruning and reductions all trade completeness for depth, and the way that goes
wrong is a forced win quietly getting pruned away.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi.board import Board, Move, HAN, CHO, ROWS, COLS  # noqa: E402
from janggi.search import Engine, SearchOptions, MATE, _HAVE_CORE  # noqa: E402

from _mate_prover import mating_moves  # noqa: E402


def build(grid, side):
    board = Board()
    for r in range(ROWS):
        for c in range(COLS):
            cell = grid[r][c]
            if cell:
                board.grid[r][c] = (cell[1], HAN if cell[0] == "h" else CHO)
    board.side_to_move = HAN if side == "h" else CHO
    return board


MATE_IN_1 = [
    dict(
        name="mate-in-1-1",
        side="c",
        grid=[
            [None, None, None, 'hG', None, None, 'cC', None, None],
            [None, None, None, None, None, None, None, None, None],
            [None, None, None, 'hK', 'hG', None, None, None, None],
            [None, None, None, 'hJ', None, None, None, None, 'hJ'],
            [None, None, None, None, None, None, None, 'hJ', None],
            ['hJ', 'cJ', None, None, None, None, None, None, 'cJ'],
            [None, None, None, None, None, 'cJ', None, None, None],
            [None, 'cP', None, 'cG', None, 'cP', 'hJ', None, 'hC'],
            ['cM', None, None, None, None, 'cK', None, 'cC', None],
            [None, None, None, 'cG', None, None, 'cS', 'hP', 'hC'],
        ],
        solutions={(0, 6, 0, 3)},
    ),
    dict(
        name="mate-in-1-2",
        side="h",
        grid=[
            [None, None, None, None, None, 'hK', 'hM', 'hS', 'cM'],
            [None, None, None, 'hG', None, 'hG', None, None, None],
            [None, None, None, None, None, None, 'hP', None, None],
            [None, 'hJ', None, None, 'cC', None, None, None, None],
            [None, 'hJ', None, None, None, None, None, 'hJ', None],
            [None, None, None, None, None, None, 'hS', None, 'hC'],
            [None, None, None, None, None, None, 'hJ', None, None],
            [None, 'hC', None, None, None, 'cG', 'cP', None, None],
            [None, None, None, None, None, 'cK', None, None, None],
            ['cC', 'cS', None, 'cG', 'cP', None, 'cM', 'hP', None],
        ],
        solutions={(5, 8, 8, 8)},
    ),
    dict(
        name="mate-in-1-3",
        side="c",
        grid=[
            [None, 'hM', 'hS', 'hG', None, 'hK', None, 'hS', None],
            [None, None, None, 'hG', None, None, None, None, 'cC'],
            [None, None, None, None, None, None, None, None, None],
            [None, None, None, None, 'hC', None, None, 'cC', None],
            [None, None, None, None, None, None, None, 'hJ', None],
            [None, None, None, 'hC', None, 'cP', None, None, None],
            [None, None, 'hP', 'cM', None, None, None, None, None],
            [None, None, None, None, None, None, None, 'hM', None],
            ['cM', None, None, 'cK', 'cG', None, None, None, None],
            [None, None, 'cS', 'cG', None, None, None, 'cS', None],
        ],
        solutions={(3, 7, 0, 7)},
    ),
    dict(
        name="mate-in-1-4",
        side="c",
        grid=[
            ['hC', None, None, None, 'hG', 'hG', 'hS', 'hM', None],
            [None, None, None, None, 'hM', None, None, None, None],
            [None, 'hP', None, 'hK', 'hS', None, None, 'hP', 'hC'],
            ['hJ', None, None, 'hJ', None, 'cP', None, 'cP', 'hJ'],
            [None, None, None, None, None, None, 'hJ', None, None],
            ['cJ', None, None, None, None, 'cJ', None, None, 'cJ'],
            [None, 'cJ', None, None, 'cJ', None, None, None, None],
            [None, None, None, None, None, None, None, None, None],
            ['cS', None, None, 'cC', 'cG', 'cK', None, None, None],
            ['cC', None, 'cM', 'cG', None, None, 'cM', 'cS', None],
        ],
        solutions={(8, 3, 3, 3)},
    ),
]

MATE_IN_2 = [
    dict(
        name="mate-in-2-1",
        side="h",
        grid=[
            [None, None, 'cP', 'hG', 'hP', 'hK', None, 'hM', None],
            [None, None, None, None, None, None, None, None, None],
            [None, None, None, 'hG', None, None, None, None, None],
            [None, None, None, 'hS', 'hS', 'hC', None, None, None],
            [None, None, None, None, None, 'hJ', None, None, None],
            [None, None, 'cJ', None, None, None, None, None, None],
            ['cJ', None, 'cJ', None, None, None, None, 'cJ', None],
            [None, None, None, None, None, 'cK', None, 'cP', None],
            [None, None, 'cM', None, None, 'cG', None, None, None],
            [None, 'cC', 'cS', 'cG', None, None, 'cM', 'cC', None],
        ],
        solutions={(4, 5, 4, 6)},
    ),
]


@pytest.mark.parametrize("case", MATE_IN_1, ids=lambda c: c["name"])
def test_finds_mate_in_one(case):
    board = build(case["grid"], case["side"])
    side = board.side_to_move
    # The certified answer must still be the answer on this build of the rules.
    proven = {m.as_tuple() for m in mating_moves(board, side, 1)}
    assert proven == case["solutions"], "prover disagrees with the frozen solution"

    move, score = Engine(max_depth=4).search(board, side)
    assert move is not None
    assert move.as_tuple() in case["solutions"], f"played {move}, expected {case['solutions']}"
    assert score > MATE - 4096, f"mate not scored as mate: {score}"


@pytest.mark.parametrize("case", MATE_IN_2, ids=lambda c: c["name"])
def test_finds_mate_in_two(case):
    board = build(case["grid"], case["side"])
    side = board.side_to_move
    proven = {m.as_tuple() for m in mating_moves(board, side, 2)}
    assert proven == case["solutions"], "prover disagrees with the frozen solution"

    move, score = Engine(max_depth=6).search(board, side)
    assert move is not None
    assert move.as_tuple() in case["solutions"], f"played {move}, expected {case['solutions']}"
    assert score > MATE - 4096, f"mate not scored as mate: {score}"


@pytest.mark.parametrize(
    "spec",
    ["", "nmp=0", "lmr=0", "fut=0", "lmp=0", "asp=0", "tt=0",
     "nmp=0,lmr=0,fut=0,lmp=0,asp=0"],
)
def test_pruning_never_hides_a_forced_mate(spec):
    """Each pruning technique, on its own and all off, must still find mate.

    A pruning bug shows up here rather than as a mysteriously lost game.
    """
    if not _HAVE_CORE:
        pytest.skip("SearchOptions only steer the compiled core")
    options = SearchOptions.parse(spec)
    for case in MATE_IN_1:
        board = build(case["grid"], case["side"])
        move, _ = Engine(max_depth=4, options=options).search(board, board.side_to_move)
        assert move is not None and move.as_tuple() in case["solutions"], (
            f"{spec or 'default'} missed {case['name']}"
        )


# ------------------------------------------------------- material tactics
def test_takes_the_free_chariot():
    board = Board()
    board.grid[9][4] = ("K", CHO)
    board.grid[0][4] = ("K", HAN)
    board.grid[5][2] = ("C", CHO)
    board.grid[5][6] = ("C", HAN)      # undefended, on the same rank
    board.grid[3][4] = ("J", HAN)      # keep the generals off one open file
    move, score = Engine(max_depth=6).search(board, CHO)
    assert move is not None and move.as_tuple() == (5, 2, 5, 6)
    assert score > 0


def test_declines_the_poisoned_capture():
    """Chariot takes cannon looks like +700 but a chariot recaptures: -600."""
    board = Board()
    board.grid[9][4] = ("K", CHO)
    board.grid[0][4] = ("K", HAN)
    board.grid[3][4] = ("J", HAN)
    board.grid[5][2] = ("C", CHO)
    board.grid[5][5] = ("P", HAN)      # the bait
    board.grid[5][8] = ("C", HAN)      # recaptures along the rank
    move, _ = Engine(max_depth=6).search(board, CHO)
    assert move is not None
    assert move.as_tuple() != (5, 2, 5, 5), "walked into the recapture"


def test_reports_a_principal_variation():
    board = Board.standard()
    engine = Engine(max_depth=6)
    move, _ = engine.search(board, CHO)
    pv = engine.stats.pv
    assert move is not None
    assert pv and pv[0] == move.as_tuple()
    # Every PV move must be legal in sequence.
    replay = Board.standard()
    side = CHO
    for step in pv:
        legal = {m.as_tuple() for m in replay.legal_moves(side)}
        assert step in legal, f"illegal PV move {step}"
        target = replay.grid[step[2]][step[3]]
        replay.make(Move(step[0], step[1], step[2], step[3],
                         target[0] if target else None))
        side = -side


def test_search_leaves_the_board_untouched():
    board = Board.standard()
    before = [list(row) for row in board.grid]
    before_hash = board.zobrist()
    Engine(max_depth=8).search(board, CHO)
    assert [list(row) for row in board.grid] == before
    assert board.zobrist() == before_hash
    assert board.side_to_move == CHO
    assert len(board._history) == 0
