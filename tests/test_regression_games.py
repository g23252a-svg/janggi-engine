"""Positions from real games where the engine played a losing move.

Every position here was reached in a game a user actually played through the
web UI, following the engine's recommendation, and lost. A unit test that comes
from a lost game is worth ten invented ones: it encodes a failure the engine
really has rather than one someone imagined it might have.

These are bounded by node count, not by seconds, so they mean the same thing on
a busy CI runner as on a workstation.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi import Board, Engine, CHO, HAN, SearchOptions  # noqa: E402

MATE_BOUND = 1_000_000 - 4096


def build(rows):
    board = Board()
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell:
                piece, who = cell
                board.grid[r][c] = (piece, CHO if who == "cho" else HAN)
    return board


# Game of 2026-08-17 (4715745b), HAN to move after 53 plies. HAN is much worse
# -- about -3200 -- but not lost: 차 (2,1)->(8,1) and 사 (0,5)->(1,5) both hold
# on. The deployed engine recommended 사 (0,5)->(1,4), which is mate, and kept
# recommending it at 10 seconds; it needed 30 s to see the refutation. Turning
# null-move pruning off found the right move immediately, which is what put NMP
# under review.
LOST_GAME_PLY_54 = [
    [None, None, ("C", "cho"), ("G", "han"), ("K", "han"), ("G", "han"), None, None, None],
    [None, None, None, None, None, None, None, None, ("C", "cho")],
    [None, ("C", "han"), None, None, ("P", "han"), ("P", "han"), None, None, None],
    [("J", "han"), None, ("M", "cho"), ("C", "han"), None, None, None, None, None],
    [None] * 9,
    [None] * 9,
    [None, None, ("J", "cho"), ("J", "cho"), ("S", "cho"), None, ("J", "cho"), ("J", "cho"), None],
    [None, None, ("M", "cho"), None, ("P", "cho"), None, None, None, None],
    [None, None, None, None, ("K", "cho"), None, None, None, None],
    [None, None, None, ("G", "cho"), None, ("G", "cho"), None, None, None],
]

FATAL = (0, 5, 1, 4)


@pytest.mark.skipif(
    os.environ.get("JANGGI_NO_ACCEL") == "1",
    reason="needs the compiled core to reach this depth in a bounded node count",
)
def test_does_not_walk_into_the_mate_that_lost_a_real_game():
    board = build(LOST_GAME_PLY_54)
    engine = Engine(max_depth=30, options=SearchOptions(node_limit=3_000_000))
    move, score = engine.search(board, HAN, game_ply=53)
    assert move is not None
    assert move.as_tuple() != FATAL, (
        "played the move that lost the game: this position is bad for HAN but "
        "not lost, and this move is mate"
    )
    assert score > -MATE_BOUND, "HAN is worse here but should not be evaluated as mated"
