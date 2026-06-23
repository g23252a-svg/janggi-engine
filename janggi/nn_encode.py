"""Board <-> tensor encoding for the neural network.

The network sees the board as a stack of binary "planes" (channels), each a
10x9 grid. This is the standard representation for board-game nets (the same
idea AlphaZero uses): one plane per (piece-type, side), plus a plane encoding
whose turn it is. Keeping the encoding here — separate from the torch model —
means it can be unit-tested without torch installed, and the exact same
function is used both when generating self-play data and when evaluating live.

Planes (16 total):
   0..6   : HAN pieces  (C, P, M, S, G, J, K)  -> 1.0 where that piece sits
   7..13  : CHO pieces  (C, P, M, S, G, J, K)
   14     : side-to-move (all 1.0 if HAN to move, else all 0.0)
   15     : all 1.0  (bias/'ones' plane, helps the net learn board edges)

The board is always encoded from a FIXED orientation (row 0 = HAN top, row 9 =
CHO bottom). The side-to-move plane tells the net who is about to move, so it
can learn perspective without us flipping the board.
"""
from __future__ import annotations

from .board import Board, ROWS, COLS, HAN, CHO

PIECE_ORDER = ("C", "P", "M", "S", "G", "J", "K")
N_PLANES = 16


def encode_board(board: Board) -> list[list[list[float]]]:
    """Return a [16][10][9] nested list of floats. (Kept as plain lists so this
    module needs no numpy/torch; the training script converts to a tensor.)"""
    planes = [[[0.0] * COLS for _ in range(ROWS)] for _ in range(N_PLANES)]
    g = board.grid
    for r in range(ROWS):
        for c in range(COLS):
            p = g[r][c]
            if p is None:
                continue
            kind, side = p
            idx = PIECE_ORDER.index(kind)
            plane = idx if side == HAN else 7 + idx
            planes[plane][r][c] = 1.0
    # Side-to-move plane.
    if board.side_to_move == HAN:
        planes[14] = [[1.0] * COLS for _ in range(ROWS)]
    # Ones plane.
    planes[15] = [[1.0] * COLS for _ in range(ROWS)]
    return planes


def encode_flat(board: Board) -> list[float]:
    """Flattened 16*10*9 = 1440-length vector, for quick sanity checks / MLPs."""
    planes = encode_board(board)
    flat: list[float] = []
    for plane in planes:
        for row in plane:
            flat.extend(row)
    return flat


# Value target convention: +1.0 = HAN wins, -1.0 = CHO wins, 0.0 = draw.
# The network outputs value from HAN's perspective; callers flip sign for CHO.
def result_to_value(winner: str) -> float:
    if winner == "han":
        return 1.0
    if winner == "cho":
        return -1.0
    return 0.0
