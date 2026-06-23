"""Train the value+policy net from self-play data (run on your PC, GPU recommended).

Reads the .jsonl produced by selfplay.py, trains JanggiNet, saves weights to a
.pt file the engine can load.

Run:
    python -m janggi.train --data data/selfplay_iter0.jsonl --epochs 10 --out data/net_iter0.pt

On an RTX 4060 a few hundred games (tens of thousands of positions) train in
minutes per epoch. Watch that BOTH losses go down: value loss (how well it
predicts the winner) and policy loss (how well it predicts the chosen move).
"""
from __future__ import annotations

import argparse
import json

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

from janggi.nn_model import JanggiNet, move_to_index, N_PLANES, BOARD_H, BOARD_W


class SelfPlayData(Dataset):
    def __init__(self, path: str):
        self.boards: list[list[int]] = []
        self.moves: list[int] = []
        self.values: list[float] = []
        with open(path) as f:
            for line in f:
                r = json.loads(line)
                self.boards.append(r["b"])
                fr, fc, tr, tc = r["m"]
                self.moves.append(move_to_index(fr, fc, tr, tc))
                self.values.append(r["v"])

    def __len__(self):
        return len(self.boards)

    def __getitem__(self, i):
        b = torch.tensor(self.boards[i], dtype=torch.float32).view(N_PLANES, BOARD_H, BOARD_W)
        return b, self.moves[i], self.values[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--out", default="data/net.pt")
    ap.add_argument("--init", default=None, help="warm-start from an existing .pt")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("device:", device)

    ds = SelfPlayData(args.data)
    print("training positions:", len(ds))
    dl = DataLoader(ds, batch_size=args.batch, shuffle=True, drop_last=True)

    net = JanggiNet().to(device)
    if args.init:
        net.load_state_dict(torch.load(args.init, map_location=device))
        print("warm-started from", args.init)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)

    for epoch in range(args.epochs):
        net.train()
        vtot = ptot = n = 0
        for b, mv, val in dl:
            b = b.to(device)
            mv = mv.to(device)
            val = val.to(device).float()
            policy, value = net(b)
            v_loss = F.mse_loss(value, val)
            p_loss = F.cross_entropy(policy, mv)
            loss = v_loss + p_loss
            opt.zero_grad()
            loss.backward()
            opt.step()
            vtot += v_loss.item(); ptot += p_loss.item(); n += 1
        print(f"epoch {epoch+1}/{args.epochs}  value_loss {vtot/n:.4f}  policy_loss {ptot/n:.4f}")

    torch.save(net.state_dict(), args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
