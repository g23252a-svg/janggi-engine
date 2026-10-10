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
# The moves that hold at this budget: 차 (2,1)->(8,1) at about -3200 and 사
# (0,5)->(1,5) at about -3650. Giving CHO one million nodes against each of the
# 27 legal replies refutes the other 25 by mate -- in 3 to 15 plies, 23 of them
# in under 300k nodes -- including 차 (2,1)->(3,1), which a weaker version of
# this test (asserting only "not FATAL") let through. At 1.5M nodes CHO finds a
# mate in 13 after (0,5,1,5) as well, so the position may be lost outright and
# (2,1,8,1) is the most resistant move; what the test asks for is a move the
# opponent cannot refute within the budget the engine itself had.
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
@needs_core
def test_the_fail_low_extension_fires_once_and_at_most_doubles_the_budget():
    """rootguard=4: when the clock runs out with the PV move's fail-low
    unresolved, the search may carry on up to double its budget, once. At the
    UI's budget that is what turns the fatal move into the move that holds;
    the extension must fire exactly once, never spend more than double, and
    cost nothing when the budget is a depth."""
    opts = SearchOptions.parse("rootguard=4")
    engine = Engine(max_depth=30, options=SearchOptions(**{**opts.__dict__, "node_limit": SHIPPING_BUDGET}))
    move, score = engine.search(build(LOST_GAME_PLY_54), HAN, game_ply=53)
    assert move.as_tuple() in HOLDS
    assert engine.stats.guard_verified == 1, "the extension should fire exactly once here"
    assert SHIPPING_BUDGET < engine.stats.total_nodes <= 2 * SHIPPING_BUDGET
    assert score > -MATE_BOUND
    # A depth-limited search never times out, so it never extends: the search
    # is node-identical to the deployed engine.
    engine = Engine(max_depth=12, options=opts)
    engine.search(Board.standard(), CHO)
    assert engine.stats.guard_verified == 0
    assert engine.stats.total_nodes == DEPLOYED_1_0_0_DEPTH12_NODES


DEPLOYED_1_0_0 = ("asp=1,rootguard=0,extbudget=3,histmalus=0,mthreat=0,"
                  "chkprune=0,lmrcap=0,soltab=0,mob=0,qguard=0")
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
# milliseconds -- including mthreat=1, which demonstrably hides a 15-ply mate.
# Those positions are too shallow to exercise these flags. This is the sweep
# that can fail: the CHO-side proof of the mate from a real game, under every
# configuration that could ship. The budget is 400k against a proof that
# costs ~210k with the shipped defaults, because the proof's cost moves by
# tens of thousands of nodes with any change to the search, and a sweep that
# flips at the margin reports noise as regressions.
#
# Only shippable configurations belong here: the defaults, the exact deployed
# form, and flags whose own measurement did not reject them. Flags that were
# rejected (lmrcap, mob, soltab, chkprune, mthreat=1) are not listed -- some of
# them hide this proof at some budgets and defaults, which is one more reason
# they stay off. Re-enabling any of them means adding it here first.
SHIPPABLE = ["", DEPLOYED_1_0_0, "histmalus=1", "mthreat=2", "extbudget=4",
             "asp=0", "asp=1", "rootguard=0", "rootguard=1", "rootguard=4",
             "qguard=3",
             "rootguard=1,extbudget=4", "asp=0,rootguard=1"]
PROOF_SWEEP_BUDGET = 400_000


@needs_core
@pytest.mark.parametrize("spec", SHIPPABLE)
def test_no_shippable_configuration_hides_the_fifteen_ply_mate(spec):
    from janggi.board import Move
    board = build(LOST_GAME_PLY_54)
    board.make(Move(*FATAL))
    opts = SearchOptions.parse(spec)
    opts = SearchOptions(**{**opts.__dict__, "node_limit": PROOF_SWEEP_BUDGET})
    engine = Engine(max_depth=30, options=opts)
    _, score = engine.search(board, CHO, game_ply=54)
    assert score > MATE_BOUND, (
        f"{spec or 'defaults'}: CHO scored {score} instead of proving the mate "
        f"within {PROOF_SWEEP_BUDGET:,} nodes"
    )


# Game of 2026-10-10 (5a2f96e5), HAN to move after 41 plies. HAN mates in four:
# 차 (4,8)->(1,8), then (1,8)->(1,5), then takes on the file twice. The user
# played CHO and had followed the engine for 20 of 24 moves; the opponent found
# the mate. As HAN, the 1.1.1 engine never saw it at any budget -- not at 10M
# nodes -- because it never completed depth 12 here: one quiescence call
# followed a cycle of evasions that give check back, with no repetition check
# and no depth cap, and grew past 3.4 million nodes before the clock or the
# node limit stopped it. The search then fell back to its depth-11 move.
LOST_GAME_2026_10_10_PLY_42 = [
    [("C", "han"), None, None, ("G", "han"), None, ("G", "han"), ("P", "cho"), None, None],
    [None, None, None, None, ("K", "han"), None, None, None, None],
    [None, ("P", "han"), None, None, ("P", "han"), None, None, None, None],
    [None, ("J", "han"), None, None, None, None, None, ("J", "han"), None],
    [None, None, None, ("M", "han"), None, None, None, None, ("C", "han")],
    [None, None, None, None, None, None, None, None, None],
    [("J", "cho"), None, ("M", "cho"), ("M", "han"), None, None, ("M", "cho"), None, ("J", "cho")],
    [None, None, None, None, None, None, None, None, None],
    [None, None, None, None, None, ("K", "cho"), None, None, None],
    [("C", "cho"), None, None, ("G", "cho"), ("P", "cho"), ("G", "cho"), ("S", "cho"), None, ("C", "cho")],
]
MATE_IN_FOUR = (4, 8, 1, 8)


@needs_core
def test_quiescence_cannot_run_away_on_a_cycle_of_checks():
    """Depth 12 here costs about 0.7M nodes with the guard. Without it the
    iteration never finishes: 3M nodes in, 2.7M of them are quiescence."""
    board = build(LOST_GAME_2026_10_10_PLY_42)
    engine = Engine(max_depth=12, options=SearchOptions(node_limit=SHIPPING_BUDGET))
    engine.search(board, HAN, game_ply=41)
    assert engine.stats.depth_reached == 12, (
        f"depth {engine.stats.depth_reached} after {engine.stats.total_nodes:,} nodes "
        f"({engine.stats.qnodes:,} in quiescence): a quiescence call is running away"
    )


@needs_core
def test_finds_the_mate_in_four_that_won_a_real_game():
    board = build(LOST_GAME_2026_10_10_PLY_42)
    engine = Engine(max_depth=30, options=SearchOptions(node_limit=SHIPPING_BUDGET))
    move, score = engine.search(board, HAN, game_ply=41)
    assert move is not None and move.as_tuple() == MATE_IN_FOUR, (
        f"played {move.as_tuple() if move else None} with score {score}; "
        f"{MATE_IN_FOUR} mates in four"
    )
    assert score > MATE_BOUND
