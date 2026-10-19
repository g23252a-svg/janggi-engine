"""Tuned evaluator weights: table 1 of the compiled evaluator (evalw=1, the default since 1.5.0).

The hand-set weights (table 0, still selectable with ``evalw=0``) were fitted,
together with a piece-square table, to Fairy-Stockfish's judgement: about
135,000 positions from 1,200 Fairy-Stockfish self-play games (random openings
and formations, one move in ten replaced by a random legal move so unbalanced
positions are in the set too), each labelled with Fairy-Stockfish 14's search
score at 10,000 nodes under the janggimodern rules (no bikjang restriction,
which matches janggi/board.py). Positions in check, positions where the best
move is a capture and mates were left out, leaving 100,470 quiet ones; a tenth
of the games were held out.

The fit is Texel-style, in win-probability space: the features are measured
from the compiled evaluator itself (janggi._core.core_eval2_probe), the
prediction is sigma(0.00264 * eval) and the target sigma(kf * score) with
Fairy-Stockfish's scale kf fitted (0.00325); the chariot is pinned at 1300
so the evaluator keeps its units, and the left and right halves of the
piece-square table are tied. Held-out squared error: hand-set 0.008908,
re-weighted 0.006970, with the piece-square table 0.006102.

``TUNED_SCALARS`` follows the EW layout of janggi/_core.pyx (DEF EW_*);
``TUNED_PST`` is EWP: piece code * 90 + square seen from the owner's side, row
0 being the owner's own back rank.
"""

TUNED_SCALARS = [
    0,      #  0 (unused)
    1300,   #  1 chariot
    644,    #  2 cannon
    508,    #  3 horse
    288,    #  4 elephant
    156,    #  5 soldier
    10000,  #  6 general (never tuned)
    243,    #  7 guard
    236,    #  8 cannon loses x(256-phase)/256
    35,     #  9 soldier gains
    36,     # 10 elephant loses
    7,      # 11 soldier per row advanced
    11,     # 12 soldier on a centre file
    9,      # 13 connected soldiers
    31,     # 14 soldier in the enemy palace
    0,      # 15 chariot centre-file step
    27,     # 16 chariot alone on its file
    11,     # 17 chariot without own soldier ahead
    20,     # 18 chariot on the enemy soldier rank
    3,      # 19 cannon centre-file step
    15,     # 20 cannon with a screen
    41,     # 21 cannon in its own palace (면포)
    4,      # 22 horse centre-file step
    9,      # 23 horse per free leg (4 is par)
    7,      # 24 elephant per free leg (4 is par)
    12,     # 25 guard in the palace
    90,     # 26 king danger, % of danger^2/900
    11,     # 27 general exposure per open palace square
    0,      # 28 (unused)
    151,    # 29 attacked, undefended: chariot
    148,    # 30 cannon
    119,    # 31 horse
    91,     # 32 elephant
    29,     # 33 soldier
    0,      # 34 (general)
    84,     # 35 guard
    0,      # 36 (unused)
    140,    # 37 attacked, defended: chariot
    55,     # 38 cannon
    51,     # 39 horse
    33,     # 40 elephant
    1,      # 41 soldier
    0,      # 42 (general)
    22,     # 43 guard
    1,      # 44 coverage mobility per square
    60,     # 45 tempo (side to move)
    -1,     # 46 mobility: chariot
    4,      # 47 mobility: cannon
    2,      # 48 mobility: horse
    2,      # 49 mobility: elephant
    0,      # 50 threat: (unused)
    0,      # 51 threat: chariot attacked by a cheaper piece
    0,      # 52 threat: cannon
    0,      # 53 threat: horse
    0,      # 54 threat: elephant
    0,      # 55 threat: soldier
    0,      # 56 threat: (general)
    0,      # 57 threat: guard
]

TUNED_PST = [
    # 0 (unused)
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
    # 1 chariot
    -44, -23,  -3,  -9,   0,  -9,  -3, -23, -44,
    -14, -13,   1,  -9,  -7,  -9,   1, -13, -14,
    -30,  -8,  -1,  -2,  -1,  -2,  -1,  -8, -30,
    -16,  -4,  -4,  -4, -11,  -4,  -4,  -4, -16,
      6,   0,   4, -12,  -1, -12,   4,   0,   6,
      4,  -1,   2,  -2,   0,  -2,   2,  -1,   4,
     -6,   9,   0,  -5, -11,  -5,   0,   9,  -6,
      0,  -3,   3,   1,   0,   1,   3,  -3,   0,
     -1,   2,   5,  -2,   0,  -2,   5,   2,  -1,
     -6,  -8,   3,  -3,   0,  -3,   3,  -8,  -6,
    # 2 cannon
      6,   0,  15,   3,  -4,   3,  15,   0,   6,
     11,   9,  10,   4,  -1,   4,  10,   9,  11,
     17,   4,  12,  -4,   5,  -4,  12,   4,  17,
      1,  -2,  -4,   2,   9,   2,  -4,  -2,   1,
      7,   3,   0,   0,   0,   0,   0,   3,   7,
     -5, -10,   3,   1,   3,   1,   3, -10,  -5,
     -3,  -8,   2,   7,   6,   7,   2,  -8,  -3,
     -4, -15, -18,   0,  -1,   0, -18, -15,  -4,
     -5, -11,  -3,   2,   0,   2,  -3, -11,  -5,
     -3, -14, -19,  -5,   0,  -5, -19, -14,  -3,
    # 3 horse
     -4, -28, -30,  -4,  -9,  -4, -30, -28,  -4,
     -3,   0,  -4,  -5,  -5,  -5,  -4,   0,  -3,
    -20,   2,   7,  10,   6,  10,   7,   2, -20,
      3,   2,   6,  -1,   1,  -1,   6,   2,   3,
      1,   7,  10,  11,   8,  11,  10,   7,   1,
      1,   4,   4,   5,   3,   5,   4,   4,   1,
     -1,  -4,  11,  11,   7,  11,  11,  -4,  -1,
      0,  -1,  -1,   0,   0,   0,  -1,  -1,   0,
      0,  -2,   2,   0,   0,   0,   2,  -2,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
    # 4 elephant
      0, -32, -16,   0,   0,   0, -16, -32,   0,
     -2,  -2,   0,  -2,   0,  -2,   0,  -2,  -2,
     -2,   0,  -1,   5,   7,   5,  -1,   0,  -2,
      6,   3,   1,  12,  11,  12,   1,   3,   6,
      1,   1,   4,   3,  -1,   3,   4,   1,   1,
     -5,   3,   4,   0,   0,   0,   4,   3,  -5,
     -2,   7,  11,   2,   3,   2,  11,   7,  -2,
      2,  -2,  -1,  -3,   0,  -3,  -1,  -2,   2,
      1,   0,  -1,  -3,   0,  -3,  -1,   0,   1,
     -8,   0,   0,   0,   0,   0,   0,   0,  -8,
    # 5 soldier
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
    -28,   5,  13,   7,   9,   7,  13,   5, -28,
    -31,  -5,   3,   3,  15,   3,   3,  -5, -31,
    -24,  -6,  -1,   5,  10,   5,  -1,  -6, -24,
     -6,  -1,   5,  20,  14,  20,   5,  -1,  -6,
      0,  -5,  -4,   5,   2,   5,  -4,  -5,   0,
      0,  -4,  -2,   0,   0,   0,  -2,  -4,   0,
      0,  -2,  -2,   0,   0,   0,  -2,  -2,   0,
    # 6 general
      0,   0,   0,  10,  34,  10,   0,   0,   0,
      0,   0,   0,   2,  29,   2,   0,   0,   0,
      0,   0,   0, -47, -28, -47,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
    # 7 guard
      0,   0,   0, -17, -11, -17,   0,   0,   0,
      0,   0,   0,  -4,  27,  -4,   0,   0,   0,
      0,   0,   0,  -2,   1,  -2,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
]
