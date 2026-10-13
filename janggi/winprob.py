"""Search score -> probability that the side to move wins.

This is the only place the board UI's "이길 확률" strip gets its number from.
Both the Flask server (server.py) and the in-browser engine (web/engine_api.py)
attach ``win_probability`` and ``mate_in`` to every searched /api/analyze
answer, so the deployed site and the GitHub Pages build show the same figure
for the same score, and the conversion is unit-tested instead of living as a
JavaScript formula nobody runs.

The model
---------
A logistic curve on the search score, from the point of view of the side to
move::

    p = 1 / (1 + exp(-k * score))

with one slope per game phase, because from ply 80 the evaluation counts
material twice and from ply 120 three times (the endgame score lock in
janggi/evaluate.py), so the same number of centipawns means less there:

    ========================  =========  ======================
    phase                     k          score for p = 75 %
    ========================  =========  ======================
    game_ply < 80             0.00264    ~416
    80 <= game_ply < 120      0.00186    ~590
    game_ply >= 120           0.00194    ~566
    ========================  =========  ======================

``game_ply`` is the number of plies already played, which is what server.py
calls ``game_ply`` (the length of the posted history) and what the page calls
``gibo.length``.

Calibration
-----------
Fitted by maximum likelihood (one slope per phase and no intercept, so
``p(-s) == 1 - p(s)``, as it should be when the score is from the side to
move) on 14,862 positions from 102 self-play games of the 1.3.0 engine at 30k
nodes a move, from seeded 6-ply random openings, each searched position
labelled with the game's eventual winner (mate, the 200-ply points decision or
repetition).

Reliability, binned by predicted probability and re-checked on the recorded
games: within a few points in the first two phases (the worst ten-point bins
are about 8 points off in the opening phase and about 10 in the middle phase;
most are within 4). In the last phase
the tails are good (0-10 % and 90-100 % within a point, and most positions are
there) but the middle bins hold only 60-150 positions each and are far noisier,
so a late-game 40-60 % reads as "unclear" more than as a precise number.

The k switch is continuous enough in practice: for real scores at plies 78-81
and 118-121 of those games, re-reading the same score with the other phase's
slope moves p by a median of 3.5 and 0.3 points.

Mate scores are not on the curve at all: the search reports a forced mate as
``MATE - plies`` (or its negation), and those map to exactly 1.0 or 0.0. The UI
decides "proven" from ``mate_in`` and clamps every other displayed percentage
to 1..99, so 0 % and 100 % on the page always mean a forced mate.
"""

import math

#: The search's mate score (janggi/search.py, janggi/_core.pyx).
MATE = 1_000_000
#: Anything beyond this is a forced mate, the same bound the search uses.
MATE_BOUND = MATE - 4096

#: (first ply of the phase, logistic slope), in increasing ply order.
PHASE_SLOPES = ((0, 0.00264), (80, 0.00186), (120, 0.00194))


def slope_for_ply(game_ply: int) -> float:
    """The logistic slope ``k`` for a position with ``game_ply`` plies played."""
    k = PHASE_SLOPES[0][1]
    for start, slope in PHASE_SLOPES:
        if game_ply >= start:
            k = slope
    return k


def win_probability(score: int, game_ply: int) -> float:
    """Probability in [0, 1] that the side to move wins.

    ``score`` is the search score from the side to move's point of view;
    ``game_ply`` is how many plies had been played when it was searched.
    A mate score gives exactly 1.0 (the side to move mates) or 0.0 (it is
    mated). Any other score is strictly inside in exact arithmetic, but a huge
    material lead still rounds to 1.0 in floating point (and at the API's four
    decimals from about 3,750 cp in the opening), so "proven" is a question
    for ``mate_in``, never ``p == 1``.
    """
    if score > MATE_BOUND:
        return 1.0
    if score < -MATE_BOUND:
        return 0.0
    x = slope_for_ply(game_ply) * score
    # One branch per sign, so exp() only ever sees a non-positive argument.
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def mate_in(score: int):
    """Moves to mate for a mate score, else ``None``.

    Counts the attacking side's own moves: ``plies = MATE - |score|`` and
    ``N = max(1, ceil(plies / 2))``. Positive when the side to move mates,
    negative when it is mated -- e.g. ``-999994`` (mated in 6 plies) is ``-3``:
    the opponent's 3 moves.
    """
    if abs(score) <= MATE_BOUND:
        return None
    plies = MATE - abs(score)
    n = max(1, math.ceil(plies / 2))
    return n if score > 0 else -n
