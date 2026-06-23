"""Neural network model for janggi (run on your PC with PyTorch + GPU).

This file is NOT imported by the live engine unless torch is installed; it's the
training-side model. A small residual CNN that takes the 16-plane board encoding
and outputs:
  * value  : scalar in [-1, 1], + = HAN winning, - = CHO winning
  * policy : a distribution over moves (used later for MCTS / move ordering)

Design notes
------------
- Sized for an RTX 4060: ~6 residual blocks of 64 channels. Trains fast, fits
  easily in 8GB, and is plenty for janggi's complexity. You can scale channels/
  blocks up later if self-play plateaus.
- The policy head predicts a move as (from_square, to_square). With 90 squares
  that's 90*90 = 8100 logits; illegal moves are masked out before softmax.
- Pure-PyTorch, no external deps beyond torch. CPU works too (just slower).

Usage is in train.py / selfplay.py; this module only defines the architecture
and the encode->tensor bridge.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from janggi.nn_encode import N_PLANES, encode_board

BOARD_H, BOARD_W = 10, 9
N_SQUARES = BOARD_H * BOARD_W          # 90
POLICY_SIZE = N_SQUARES * N_SQUARES    # 8100  (from_sq * 90 + to_sq)


def move_to_index(fr: int, fc: int, tr: int, tc: int) -> int:
    return (fr * BOARD_W + fc) * N_SQUARES + (tr * BOARD_W + tc)


def index_to_move(idx: int) -> tuple[int, int, int, int]:
    frm, to = divmod(idx, N_SQUARES)
    fr, fc = divmod(frm, BOARD_W)
    tr, tc = divmod(to, BOARD_W)
    return fr, fc, tr, tc


class ResBlock(nn.Module):
    def __init__(self, ch: int) -> None:
        super().__init__()
        self.c1 = nn.Conv2d(ch, ch, 3, padding=1, bias=False)
        self.b1 = nn.BatchNorm2d(ch)
        self.c2 = nn.Conv2d(ch, ch, 3, padding=1, bias=False)
        self.b2 = nn.BatchNorm2d(ch)

    def forward(self, x):
        y = F.relu(self.b1(self.c1(x)))
        y = self.b2(self.c2(y))
        return F.relu(x + y)


class JanggiNet(nn.Module):
    def __init__(self, channels: int = 64, blocks: int = 6) -> None:
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(N_PLANES, channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(channels),
            nn.ReLU(inplace=True),
        )
        self.res = nn.Sequential(*[ResBlock(channels) for _ in range(blocks)])
        # Value head
        self.v_conv = nn.Sequential(
            nn.Conv2d(channels, 1, 1, bias=False), nn.BatchNorm2d(1), nn.ReLU(inplace=True)
        )
        self.v_fc = nn.Sequential(
            nn.Linear(N_SQUARES, 64), nn.ReLU(inplace=True), nn.Linear(64, 1), nn.Tanh()
        )
        # Policy head
        self.p_conv = nn.Sequential(
            nn.Conv2d(channels, 2, 1, bias=False), nn.BatchNorm2d(2), nn.ReLU(inplace=True)
        )
        self.p_fc = nn.Linear(2 * N_SQUARES, POLICY_SIZE)

    def forward(self, x):
        x = self.stem(x)
        x = self.res(x)
        v = self.v_conv(x).flatten(1)
        value = self.v_fc(v).squeeze(-1)
        p = self.p_conv(x).flatten(1)
        policy = self.p_fc(p)  # raw logits; mask + softmax done by caller
        return policy, value


def board_to_tensor(board, device="cpu") -> torch.Tensor:
    """Single board -> [1, 16, 10, 9] float tensor."""
    planes = encode_board(board)
    return torch.tensor(planes, dtype=torch.float32, device=device).unsqueeze(0)
