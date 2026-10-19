"""Tuned evaluator weights: table 1 of the compiled evaluator (evalw=1, the default since 1.5.0).

The hand-set weights (table 0, still selectable with ``evalw=0``) fitted,
together with a piece-square table, to Fairy-Stockfish's judgement
(``tools/evaltune/``). 1.5.1's fit uses 274,180 distinct positions, each
scored by Fairy-Stockfish 14 under the janggimodern rules (no bikjang
restriction, which matches janggi/board.py):

- 1,800 Fairy-Stockfish self-play games from random openings and formations,
  one move in ten replaced by a random legal move so unbalanced positions are
  in the set too (1,200 labelled at 10,000 nodes, 600 at 20,000);
- 730 games in which our own engine chose the moves (600 by 1.5.0 at 30,000
  nodes labelled at 20,000, 130 by 1.4.0 at 20,000 labelled at 10,000), so
  the positions our search steers into -- the cannon raid that lost the
  2026-10-19 game among them -- are represented, not only Fairy-Stockfish's
  own.

Positions in check, positions whose best move is a capture and mates were left
out, leaving 205,033 quiet ones; a tenth of the games were held out.

The fit is Texel-style, in win-probability space: the features are measured
from the compiled evaluator itself (janggi._core.core_eval2_probe, at base ply
0, so without the endgame lock), the prediction is sigma(0.00264 * eval) and
the target sigma(kf * score) with Fairy-Stockfish's scale kf fitted
(0.00316); the chariot is pinned at 1300 so the evaluator keeps its
units, the left and right halves of the piece-square table are tied, and the
pull toward the hand-set weights is light (1e-6; 1.5.0 used 1e-4, which held
the fit measurably short). Held-out squared error, all with a fitted bias,
tempo and scale: hand-set 0.008906, re-weighted
0.006792, with the piece-square table 0.005534
(1.5.0's table on the same held-out games: 0.006003).

``TUNED_SCALARS`` follows the EW layout of janggi/_core.pyx (DEF EW_*);
``TUNED_PST`` is EWP: piece code * 90 + square seen from the owner's side, row
0 being the owner's own back rank.
"""

TUNED_SCALARS = [
    0,      #  0 (unused)
    1300,   #  1 chariot
    638,    #  2 cannon
    486,    #  3 horse
    276,    #  4 elephant
    168,    #  5 soldier
    10000,  #  6 general (never tuned)
    237,    #  7 guard
    340,    #  8 cannon loses x(256-phase)/256
    -10,    #  9 soldier gains
    29,     # 10 elephant loses
    7,      # 11 soldier per row advanced
    36,     # 12 soldier on a centre file
    19,     # 13 connected soldiers
    38,     # 14 soldier in the enemy palace
    -12,    # 15 chariot centre-file step
    22,     # 16 chariot alone on its file
    4,      # 17 chariot without own soldier ahead
    5,      # 18 chariot on the enemy soldier rank
    8,      # 19 cannon centre-file step
    32,     # 20 cannon with a screen
    43,     # 21 cannon in its own palace (면포)
    12,     # 22 horse centre-file step
    9,      # 23 horse per free leg (4 is par)
    8,      # 24 elephant per free leg (4 is par)
    12,     # 25 guard in the palace
    88,     # 26 king danger, % of danger^2/900
    5,      # 27 general exposure per open palace square
    0,      # 28 (unused)
    127,    # 29 attacked, undefended: chariot
    112,    # 30 cannon
    96,     # 31 horse
    74,     # 32 elephant
    42,     # 33 soldier
    0,      # 34 (general)
    51,     # 35 guard
    0,      # 36 (unused)
    153,    # 37 attacked, defended: chariot
    27,     # 38 cannon
    40,     # 39 horse
    21,     # 40 elephant
    5,      # 41 soldier
    0,      # 42 (general)
    9,      # 43 guard
    -3,     # 44 coverage mobility per square
    60,     # 45 tempo (side to move)
    1,      # 46 mobility: chariot
    7,      # 47 mobility: cannon
    3,      # 48 mobility: horse
    5,      # 49 mobility: elephant
    0,      # 50 threat: (unused)
    1,      # 51 threat: chariot attacked by a cheaper piece
    24,     # 52 threat: cannon
    13,     # 53 threat: horse
    16,     # 54 threat: elephant
    0,      # 55 threat: soldier
    0,      # 56 threat: (general)
    4,      # 57 threat: guard
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
    -59, -30,  -5,  -5,  -4,  -5,  -5, -30, -59,
    -28, -18,   4,   0,  -1,   0,   4, -18, -28,
    -42, -14,   0,   2,   1,   2,   0, -14, -42,
    -21,  -5,  -1,   2,  -2,   2,  -1,  -5, -21,
     -5,  -4,   7,  -3,   6,  -3,   7,  -4,  -5,
     -6,  -1,   7,   0,   4,   0,   7,  -1,  -6,
     -8,   3,   7,  -3,  -2,  -3,   7,   3,  -8,
      0,  -4,   5,   0,   1,   0,   5,  -4,   0,
     -8,   2,   8,   1,   0,   1,   8,   2,  -8,
     -9, -10,   2,   0,   0,   0,   2, -10,  -9,
    # 2 cannon
      7,   3,  14,   4,   0,   4,  14,   3,   7,
      8,   7,   8,   2,  -1,   2,   8,   7,   8,
     23,   8,  14,  -6,   0,  -6,  14,   8,  23,
      1,   1,  -1,   5,   8,   5,  -1,   1,   1,
      7,   0,   0,   3,   4,   3,   0,   0,   7,
     -3,  -9,  -3,  -1,   1,  -1,  -3,  -9,  -3,
     -2,  -9,  -5,   7,   1,   7,  -5,  -9,  -2,
     -4, -14, -18,  -2,   1,  -2, -18, -14,  -4,
     -4,  -7,  -6,   0,   0,   0,  -6,  -7,  -4,
     -3, -11, -21,  -4,  -2,  -4, -21, -11,  -3,
    # 3 horse
     -5, -24, -26,  -5,  -9,  -5, -26, -24,  -5,
     -1,   0,  -2,  -2, -18,  -2,  -2,   0,  -1,
    -11,   3,   3,   6,   3,   6,   3,   3, -11,
      4,   6,   5,   0,  -1,   0,   5,   6,   4,
      1,   0,  11,   6,   0,   6,  11,   0,   1,
      2,   6,   7,   4,   3,   4,   7,   6,   2,
      1,   3,   6,  12,   5,  12,   6,   3,   1,
      1,   2,   2,   1,   0,   1,   2,   2,   1,
     -1,  -1,   1,   0,   0,   0,   1,  -1,  -1,
      0,   0,   1,   0,   0,   0,   1,   0,   0,
    # 4 elephant
      3, -27,  -8,   0,   0,   0,  -8, -27,   3,
     -2,  -3,  -2,  -1,   0,  -1,  -2,  -3,  -2,
     -1,   0,  -1,   8,  11,   8,  -1,   0,  -1,
      1,   1,  -1,   9,  11,   9,  -1,   1,   1,
     -2,   1,   1,   0,   2,   0,   1,   1,  -2,
     -4,   9,   2,   2,  -1,   2,   2,   9,  -4,
     -1,  11,   4,  -3,   2,  -3,   4,  11,  -1,
      2,  -1,  -1,  -1,   0,  -1,  -1,  -1,   2,
     -2,  -1,  -3,  -2,   0,  -2,  -3,  -1,  -2,
     -4,   0,   0,  -1,  -2,  -1,   0,   0,  -4,
    # 5 soldier
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
    -22,  10,  17,  -9,  -1,  -9,  17,  10, -22,
    -22,   3,  15,  -8,   6,  -8,  15,   3, -22,
    -17,  -6,   5,   3,   7,   3,   5,  -6, -17,
     -5,  -2,   8,  17,  11,  17,   8,  -2,  -5,
     -1,  -2,   1,   4,   1,   4,   1,  -2,  -1,
     -1,  -1,   0,  -1,   0,  -1,   0,  -1,  -1,
     -3,  -2,  -3,   0,   0,   0,  -3,  -2,  -3,
    # 6 general
      0,   0,   0,  13,  35,  13,   0,   0,   0,
      0,   0,   0,   5,  24,   5,   0,   0,   0,
      0,   0,   0, -51, -27, -51,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
    # 7 guard
      0,   0,   0, -17, -10, -17,   0,   0,   0,
      0,   0,   0,  -1,  32,  -1,   0,   0,   0,
      0,   0,   0,  -4,  -1,  -4,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,   0,
]
