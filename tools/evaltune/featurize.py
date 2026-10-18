"""Turn FSF-labelled positions into a feature matrix for tuning.

usage: featurize.py OUT.npz IN1.jsonl [IN2.jsonl ...]   (run with the evalw core)

Keeps positions that are quiet by FSF's own judgement: side to move not in
check, FSF's best move not a capture, |label| < 2500 (no mates, no runaway
material). Features are measured from the compiled evaluator
(core_eval2_probe at base ply 0, i.e. without the endgame lock) plus the
piece-square counts, HAN-positive. The label is turned HAN-positive too."""
import json
import os
import sys

import numpy as np

from janggi.board import Board, CHO, HAN
import janggi._core as core

out_path, inputs = sys.argv[1], sys.argv[2:]
KIND = {"K": 6, "G": 7, "S": 4, "M": 3, "C": 1, "P": 2, "J": 5}
NEW = len(core.core_get_eval_table(0)[0])

rows, seen = [], set()
for p in inputs:
    for ln in open(p):
        try:
            r = json.loads(ln)
        except ValueError:
            continue          # a line still being written
        key = (r["b"], r["stm"])
        if key in seen:
            continue
        seen.add(key)
        if r["incheck"] or abs(r["fsf"]) >= 2500:
            continue
        b = r["b"]
        to = r["best"]
        # capture? destination square occupied (FSF coords: file a-i, rank 1-10, rank 10 = row 0)
        import re
        m = re.fullmatch(r"([a-i])(10|[1-9])([a-i])(10|[1-9])", to or "")
        if not m:
            continue
        tr, tc = 10 - int(m.group(4)), ord(m.group(3)) - 97
        if b[tr * 9 + tc] != ".":
            continue
        rows.append(r)
print("quiet positions", len(rows), "of", len(seen), "distinct", flush=True)

N = len(rows)
F = np.zeros((N, NEW), dtype=np.float32)
P = np.zeros((N, 8 * 90), dtype=np.int8)     # PST counts, HAN-positive
y = np.zeros(N, dtype=np.float32)            # FSF label, HAN-positive
stm = np.zeros(N, dtype=np.int8)             # +1 HAN to move, -1 CHO
ply = np.zeros(N, dtype=np.int16)
gid = []
for i, r in enumerate(rows):
    grid = [[None] * 9 for _ in range(10)]
    for idx, ch in enumerate(r["b"]):
        if ch == ".":
            continue
        k = ch.upper()
        s = CHO if ch.isupper() else HAN
        grid[idx // 9][idx % 9] = (k, s)
        pc = KIND[k]
        rr, cc = idx // 9, idx % 9
        rel = idx if s == HAN else (9 - rr) * 9 + cc
        P[i, pc * 90 + rel] += 1 if s == HAN else -1
    side = HAN if r["stm"] == "han" else CHO
    board = Board.from_grid(grid, side)
    F[i], _ = core.core_eval2_probe(board._pc, board._sd, 0)   # base ply 0: no endgame lock
    y[i] = r["fsf"] if side == HAN else -r["fsf"]
    stm[i] = 1 if side == HAN else -1
    ply[i] = r["ply"]
    gid.append(r["g"])
    if i % 20000 == 0:
        print("  featurized", i, flush=True)
np.savez_compressed(out_path, F=F, P=P, y=y, stm=stm, ply=ply, gid=np.array(gid))
print("written", out_path, N)
