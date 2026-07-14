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
import random

import torch
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, Subset

from janggi.nn_model import (
    JanggiNet,
    move_to_index,
    N_PLANES,
    BOARD_H,
    BOARD_W,
    POLICY_SIZE,
)


class SelfPlayData(Dataset):
    def __init__(self, paths: str | list[str]):
        self.boards: list[list[int]] = []
        self.policies: list[list[tuple[int, float]]] = []
        self.values: list[float] = []
        self.groups: list[str] = []
        if isinstance(paths, str):
            paths = [paths]
        for path_index, path in enumerate(paths):
            with open(path, encoding="utf-8") as f:
                for line_index, line in enumerate(f):
                    r = json.loads(line)
                    self.boards.append(r["b"])
                    # New self-play files carry a game id so validation holds
                    # out entire games. Legacy records fall back to one group
                    # per position instead of pretending unrelated files match.
                    game_id = r.get("g", f"legacy-{line_index}")
                    self.groups.append(f"{path_index}:{game_id}")
                    if r.get("p"):
                        self.policies.append(
                            [(int(index), float(probability)) for index, probability in r["p"]]
                        )
                    else:
                        # Backward compatibility with iteration-zero one-hot data.
                        fr, fc, tr, tc = r["m"]
                        self.policies.append([(move_to_index(fr, fc, tr, tc), 1.0)])
                    self.values.append(r["v"])

    def __len__(self):
        return len(self.boards)

    def __getitem__(self, i):
        b = torch.tensor(self.boards[i], dtype=torch.float32).view(N_PLANES, BOARD_H, BOARD_W)
        policy = torch.zeros(POLICY_SIZE, dtype=torch.float32)
        for index, probability in self.policies[i]:
            policy[index] = probability
        total = policy.sum()
        if total > 0:
            policy /= total
        return b, policy, self.values[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data", required=True, nargs="+",
        help="one or more JSONL files (recent iterations form a replay window)",
    )
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--out", default="data/net.pt")
    ap.add_argument("--init", default=None, help="warm-start from an existing .pt")
    ap.add_argument("--val-fraction", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=20260714)
    args = ap.parse_args()

    if not 0.0 <= args.val_fraction < 0.5:
        raise SystemExit("--val-fraction must be in [0, 0.5)")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("device:", device)

    ds = SelfPlayData(args.data)
    print("training positions:", len(ds))
    if not ds:
        raise SystemExit("no training positions found")
    groups = sorted(set(ds.groups))
    val_group_count = int(len(groups) * args.val_fraction)
    if args.val_fraction > 0.0 and len(groups) > 1:
        val_group_count = max(1, val_group_count)
    if val_group_count > 0:
        random.Random(args.seed).shuffle(groups)
        val_groups = set(groups[:val_group_count])
        train_indices = [i for i, group in enumerate(ds.groups) if group not in val_groups]
        val_indices = [i for i, group in enumerate(ds.groups) if group in val_groups]
        train_ds, val_ds = Subset(ds, train_indices), Subset(ds, val_indices)
    else:
        train_ds, val_ds = ds, None
    dl = DataLoader(train_ds, batch_size=args.batch, shuffle=True, drop_last=False)
    val_dl = (
        DataLoader(val_ds, batch_size=args.batch, shuffle=False)
        if val_ds is not None else None
    )
    val_size = len(val_ds) if val_ds is not None else 0
    print(f"train/validation positions: {len(train_ds)}/{val_size}")

    net = JanggiNet().to(device)
    if args.init:
        net.load_state_dict(torch.load(args.init, map_location=device))
        print("warm-started from", args.init)
    opt = torch.optim.Adam(net.parameters(), lr=args.lr, weight_decay=1e-4)

    best_val = float("inf")
    for epoch in range(args.epochs):
        net.train()
        vtot = ptot = n = 0
        for b, policy_target, val in dl:
            b = b.to(device)
            policy_target = policy_target.to(device)
            val = val.to(device).float()
            policy, value = net(b)
            v_loss = F.mse_loss(value, val)
            # Cross-entropy against MCTS visit probabilities. One-hot bootstrap
            # records remain mathematically identical to the former loss.
            p_loss = -(policy_target * F.log_softmax(policy, dim=1)).sum(dim=1).mean()
            loss = v_loss + p_loss
            opt.zero_grad()
            loss.backward()
            opt.step()
            vtot += v_loss.item(); ptot += p_loss.item(); n += 1
        message = (
            f"epoch {epoch+1}/{args.epochs}  "
            f"value_loss {vtot/n:.4f}  policy_loss {ptot/n:.4f}"
        )

        if val_dl is not None:
            net.eval()
            vval = pval = vn = 0
            with torch.no_grad():
                for b, policy_target, val in val_dl:
                    b = b.to(device)
                    policy_target = policy_target.to(device)
                    val = val.to(device).float()
                    policy, value = net(b)
                    v_loss = F.mse_loss(value, val)
                    p_loss = -(
                        policy_target * F.log_softmax(policy, dim=1)
                    ).sum(dim=1).mean()
                    vval += v_loss.item(); pval += p_loss.item(); vn += 1
            validation_loss = (vval + pval) / vn
            message += f"  val_value {vval/vn:.4f}  val_policy {pval/vn:.4f}"
            if validation_loss < best_val:
                best_val = validation_loss
                torch.save(net.state_dict(), args.out)
                message += "  [best saved]"
        print(message)

    if val_dl is None:
        torch.save(net.state_dict(), args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
