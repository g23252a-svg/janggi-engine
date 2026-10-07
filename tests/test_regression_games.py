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
# The moves that actually hold: 차 (2,1)->(8,1) at about -3200 and 사 (0,5)->(1,5)
# at about -3650. Everything else is mate -- including 차 (2,1)->(3,1), which a
# weaker version of this test (asserting only "not FATAL") let through.
HOLDS = {(2, 1, 8, 1), (0, 5, 1, 5)}
# The '표준' tier of the web UI searches for 3.5 s, which is about one million
# nodes on the deployment. A test that passes at three million nodes while the
# UI still plays the mate is not testing the failure that happened.
SHIPPING_BUDGET = 1_000_000

needs_core = pytest.mark.skipif(
    os.environ.get("JANGGI_NO_ACCEL") == "1",
    reason="needs the compiled core to reach this depth in a bounded node count",
)


@needs_core
def test_does_not_walk_into_the_mate_that_lost_a_real_game():
    board = build(LOST_GAME_PLY_54)
    engine = Engine(max_depth=30, options=SearchOptions(node_limit=SHIPPING_BUDGET))
    move, score = engine.search(board, HAN, game_ply=53)
    assert move is not None
    assert move.as_tuple() in HOLDS, (
        f"played {move.as_tuple()} at the UI's budget; the only moves that hold "
        f"are {sorted(HOLDS)} and this one loses"
    )
    assert score > -MATE_BOUND, "HAN is worse here but should not be evaluated as mated"


@needs_core
def test_the_mate_after_the_fatal_move_is_seen_quickly():
    """The other half of the contract: once the fatal move is on the board the
    engine (as CHO) must prove the mate fast. Today it does in ~200k nodes; a
    future extension or pruning change that hides it should fail here loudly
    rather than reappear as a lost game."""
    from janggi.board import Move
    board = build(LOST_GAME_PLY_54)
    board.make(Move(*FATAL))
    engine = Engine(max_depth=30, options=SearchOptions(node_limit=300_000))
    _, score = engine.search(board, CHO, game_ply=54)
    assert score > MATE_BOUND, f"CHO should see the forced mate; scored {score}"


# The exact search the 1.0.0 deployment runs, spelled out flag by flag so that
# it keeps meaning the same thing after any default flips. Every A/B in the
# 1.1.0 campaign used "" to mean this engine; if this number moves, "" has
# silently stopped meaning that and every verdict in CHANGELOG is suspect.
DEPLOYED_1_0_0 = ("asp=1,rootguard=0,extbudget=3,histmalus=0,mthreat=0,"
                  "chkprune=0,lmrcap=0,soltab=0,mob=0")
DEPLOYED_1_0_0_DEPTH12_NODES = 2_822_961


@needs_core
def test_the_deployed_search_is_node_identical():
    engine = Engine(max_depth=12, options=SearchOptions.parse(DEPLOYED_1_0_0))
    engine.search(Board.standard(), CHO)
    assert engine.stats.depth_reached == 12
    assert engine.stats.total_nodes == DEPLOYED_1_0_0_DEPTH12_NODES, (
        f"{engine.stats.total_nodes:,} nodes: the deployed-form search changed; "
        "a flag is not inert when off, or a default moved without its flag"
    )


# The mate-in-1/2 prover sweep in test_tactics.py passes every ON form in
# milliseconds -- including mthreat=1, which demonstrably hides a mate. Those
# positions are too shallow to exercise these flags. This is the sweep that
# can fail: the CHO-side proof of a 15-ply mate from a real game, under each
# flag that could ever ship on.
ON_FORMS = ["histmalus=1", "rootguard=1", "mthreat=2", "chkprune=1", "lmrcap=1",
            "mob=2", "extbudget=4", "asp=0",
            "asp=0,rootguard=1,extbudget=4",
            "asp=0,rootguard=1,extbudget=4,mthreat=2"]
# Flags this sweep has caught hiding the proof. mthreat=1 by the extension it
# adds on the defending side; soltab=1 by re-valuing the soldiers that take
# part in the mating net (CHO scores +2888 at 300k instead of mate). Both are
# off, and this list is why they stay off until the proof survives them.
HIDES_THE_PROOF = ["mthreat=1", "soltab=1"]


@needs_core
@pytest.mark.parametrize("spec", ON_FORMS)
def test_no_shippable_flag_hides_the_fifteen_ply_mate(spec):
    from janggi.board import Move
    board = build(LOST_GAME_PLY_54)
    board.make(Move(*FATAL))
    engine = Engine(max_depth=30, options=SearchOptions.parse(spec + ",nodes=300000"))
    _, score = engine.search(board, CHO, game_ply=54)
    assert score > MATE_BOUND, f"{spec}: CHO scored {score} instead of proving the mate within 300k nodes"


@needs_core
@pytest.mark.parametrize("spec", HIDES_THE_PROOF)
def test_the_flag_known_to_hide_the_proof_still_does(spec):
    """If this ever passes, mthreat=1 has changed and must be re-measured
    before anyone is tempted to turn it on."""
    from janggi.board import Move
    board = build(LOST_GAME_PLY_54)
    board.make(Move(*FATAL))
    engine = Engine(max_depth=30, options=SearchOptions.parse(spec + ",nodes=300000"))
    _, score = engine.search(board, CHO, game_ply=54)
    assert score <= MATE_BOUND, f"{spec} now proves the mate ({score}); re-measure it"
