"""Search score -> win probability (janggi/winprob.py).

The board UI's "이길 확률" strip shows this number and both APIs compute it
here, so the curve's shape is pinned: the fitted slopes, symmetry between the
two sides, the phase boundaries, and the exact handling of mate scores.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi import winprob  # noqa: E402
from janggi.winprob import MATE, MATE_BOUND, mate_in, win_probability  # noqa: E402

PHASE_PLIES = (0, 40, 79, 80, 100, 119, 120, 160, 199)


def test_the_module_imports_nothing_but_math():
    """It ships to the browser and is imported by both APIs; it must not drag
    in the engine (or anything else)."""
    src = open(winprob.__file__, encoding="utf-8").read()
    imports = [line.strip() for line in src.splitlines()
               if line.startswith(("import ", "from "))]
    assert imports == ["import math"]


def test_the_three_fitted_slopes():
    assert winprob.PHASE_SLOPES == ((0, 0.00264), (80, 0.00186), (120, 0.00194))
    for ply in (0, 1, 79):
        assert winprob.slope_for_ply(ply) == 0.00264
    for ply in (80, 100, 119):
        assert winprob.slope_for_ply(ply) == 0.00186
    for ply in (120, 150, 200, 400):
        assert winprob.slope_for_ply(ply) == 0.00194


def test_it_is_the_logistic_of_the_score():
    for ply in PHASE_PLIES:
        k = winprob.slope_for_ply(ply)
        for score in (-2000, -300, -1, 0, 1, 150, 999):
            assert win_probability(score, ply) == pytest.approx(
                1 / (1 + math.exp(-k * score)), abs=1e-15)


def test_an_even_score_is_an_even_game():
    for ply in PHASE_PLIES:
        assert win_probability(0, ply) == 0.5


@pytest.mark.parametrize("ply", PHASE_PLIES)
def test_monotonic_in_score(ply):
    scores = list(range(-5000, 5001, 25))
    probs = [win_probability(s, ply) for s in scores]
    assert all(a <= b for a, b in zip(probs, probs[1:]))
    # strictly, wherever floating point can still tell the difference
    mid = [win_probability(s, ply) for s in range(-1500, 1501, 25)]
    assert all(a < b for a, b in zip(mid, mid[1:]))


@pytest.mark.parametrize("ply", PHASE_PLIES)
def test_both_sides_add_up_to_one(ply):
    """The score is from the side to move, so the other side's chance is the
    complement -- the UI relies on that to show MY probability on either
    side's turn."""
    for score in (0, 1, 37, 120, 416, 589, 1500, 4000, 20000):
        assert win_probability(score, ply) + win_probability(-score, ply) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("ply, expected", [(0, 416), (79, 416), (80, 589), (119, 589),
                                           (120, 566), (199, 566)])
def test_seventy_five_percent_thresholds(ply, expected):
    """The score worth 3:1 odds in each phase: ln(3) / k."""
    threshold = math.log(3) / winprob.slope_for_ply(ply)
    assert threshold == pytest.approx(expected, abs=3)
    assert win_probability(math.ceil(threshold), ply) >= 0.75
    assert win_probability(math.floor(threshold), ply) < 0.75
    assert win_probability(-math.ceil(threshold), ply) <= 0.25


def test_later_phases_need_a_bigger_score_for_the_same_odds():
    """From ply 80 the evaluation counts material double, so the same score is
    worth less there."""
    assert win_probability(400, 79) > win_probability(400, 80)
    assert win_probability(400, 119) < win_probability(400, 120)


def test_the_phase_switch_is_a_step_not_a_cliff():
    """At the phase boundaries the same score re-read with the next slope may
    move, but only by a few points for scores that occur there."""
    for score in (-600, -200, 0, 200, 600):
        assert abs(win_probability(score, 79) - win_probability(score, 80)) < 0.12
        assert abs(win_probability(score, 119) - win_probability(score, 120)) < 0.02


def test_mate_scores_are_certain():
    for ply in PHASE_PLIES:
        for plies in (0, 1, 2, 5, 6, 40, 4095):
            assert win_probability(MATE - plies, ply) == 1.0
            assert win_probability(-(MATE - plies), ply) == 0.0
        # beyond MATE (the search's own -2*MATE sentinel) still counts
        assert win_probability(2 * MATE, ply) == 1.0
        assert win_probability(-2 * MATE, ply) == 0.0


def test_the_mate_bound_is_the_searchs():
    assert MATE == 1_000_000 and MATE_BOUND == MATE - 4096
    assert mate_in(MATE_BOUND) is None and mate_in(-MATE_BOUND) is None
    assert mate_in(MATE_BOUND + 1) is not None
    assert mate_in(-MATE_BOUND - 1) is not None


def test_huge_scores_do_not_overflow():
    for score in (10**6 - 5000, -(10**6 - 5000), 10**5, -10**5):
        p = win_probability(score, 0)
        assert 0.0 <= p <= 1.0


@pytest.mark.parametrize("score, expected", [
    (999_999, 1),        # mate in 1 ply: my move mates
    (999_998, 1),        # ceil(2 / 2)
    (999_997, 2),
    (999_995, 3),
    (-1_000_000, -1),    # already mated (no legal move)
    (-999_998, -1),      # mated after my 1 move and their 1
    (-999_996, -2),
    (-999_994, -3),      # the fixture: at ply 68 the opponent mates on 69, 71, 73
    (-999_993, -4),      # ceil: 7 plies is the attacker's 4th move
])
def test_mate_in_counts_the_attackers_moves(score, expected):
    assert mate_in(score) == expected


def test_mate_in_sign_follows_the_side_to_move():
    for plies in range(0, 40):
        assert mate_in(MATE - plies) > 0
        assert mate_in(-(MATE - plies)) < 0
        assert mate_in(MATE - plies) == max(1, math.ceil(plies / 2))
        assert mate_in(-(MATE - plies)) == -max(1, math.ceil(plies / 2))


def test_no_mate_no_count():
    for score in (0, 1, -1, 500, -2500, 90_000, -MATE_BOUND, MATE_BOUND):
        assert mate_in(score) is None
