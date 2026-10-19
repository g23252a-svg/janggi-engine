"""Fit the evaluator weights to FSF labels (Texel-style, win-probability space).

usage: tune.py DATA.npz OUT.json [--pst] [--lam-s 1e-6] [--lam-p 1e-4] [--iters 3000]

z = F.w + Pm.p + bias + tempo*stm            (HAN-positive, our eval units)
loss = mean (sigma(K z) - sigma(Kf y))^2 + lam_s sum((w-w0)/s)^2 + lam_p sum(p^2)/1e4
K = 0.00264 (janggi/winprob.py's opening slope) is fixed; Kf, FSF's scale, is
fitted. The chariot weight is pinned at 1300, which pins our units. Games are
split 90/10 into fit and validation by game id."""
import argparse
import json
import zlib

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("data")
ap.add_argument("out")
ap.add_argument("--pst", action="store_true")
ap.add_argument("--lam-s", type=float, default=1e-6)   # 1.5.0 used 1e-4, which held the fit short
ap.add_argument("--lam-p", type=float, default=1e-4)
ap.add_argument("--iters", type=int, default=3000)
ap.add_argument("--lr", type=float, default=2.0)
ap.add_argument("--maxply", type=int, default=400)
args = ap.parse_args()

d = np.load(args.data)
F, P, y, stm, ply, gid = d["F"].astype(np.float64), d["P"], d["y"].astype(np.float64), d["stm"].astype(np.float64), d["ply"], d["gid"]
keep = ply < args.maxply
F, P, y, stm, gid = F[keep], P[keep], y[keep], stm[keep], gid[keep]
val = np.array([zlib.crc32(g.encode()) % 10 == 0 for g in gid])
NEW = F.shape[1]

W0 = np.array([0, 1300, 700, 500, 300, 200, 10000, 300, 150, 60, 40, 8, 10, 8, 30,
               3, 25, 12, 20, 3, 15, 40, 3, 7, 5, 12, 100, 12,
               0, 360, 260, 190, 130, 25, 0, 90, 0, 120, 90, 65, 40, 0, 0, 25, 2] + [0] * (NEW - 45), dtype=np.float64)
assert len(W0) == NEW
# which scalar weights are free: everything with a non-zero feature, minus slots 0/6 and the anchor
var = F.std(axis=0)
free = (var > 1e-9)
free[[0, 6, 1]] = False
SCALE = np.maximum(np.abs(W0), 10.0)

# mirrored piece-square columns: (pc, r, min(c, 8-c))
if args.pst:
    cols = {}
    for pc in range(8):
        for r in range(10):
            for c in range(9):
                cols.setdefault((pc, r, min(c, 8 - c)), []).append(pc * 90 + r * 9 + c)
    keys = sorted(cols)
    Pm = np.zeros((len(y), len(keys)), dtype=np.float64)
    Pf = P.astype(np.float64)
    for j, k in enumerate(keys):
        Pm[:, j] = Pf[:, cols[k]].sum(axis=1)
    used = np.abs(Pm[~val]).sum(axis=0) >= 200        # enough occurrences to learn from
    Pm = Pm[:, used]
    keys = [k for k, u in zip(keys, used) if u]
else:
    Pm = np.zeros((len(y), 0)); keys = []
print(f"positions fit {int((~val).sum())} val {int(val.sum())}; free scalars {int(free.sum())}; pst params {len(keys)}", flush=True)

K = 0.00264


def sig(x):
    return 1.0 / (1.0 + np.exp(-x))


def loss_of(w, p, b, t, kf, mask):
    z = F[mask] @ w + (Pm[mask] @ p if len(keys) else 0) + b + t * stm[mask]
    return float(np.mean((sig(K * z) - sig(kf * y[mask])) ** 2))


def fit(free_mask, use_pst, iters):
    w = W0.copy(); p = np.zeros(len(keys)); b = 0.0; t = 0.0; lkf = np.log(K)
    m = {}; v = {}
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    fitm = ~val
    Ff, Pf_, yf, sf = F[fitm], Pm[fitm], y[fitm], stm[fitm]
    n = len(yf)
    for it in range(1, iters + 1):
        kf = np.exp(lkf)
        z = Ff @ w + (Pf_ @ p if use_pst and len(keys) else 0) + b + t * sf
        po, pt = sig(K * z), sig(kf * yf)
        e = po - pt
        gz = 2 * e * po * (1 - po) * K / n
        grads = {
            "w": Ff.T @ gz + 2 * args.lam_s * (w - W0) / SCALE ** 2,
            "b": gz.sum(), "t": (gz * sf).sum(),
            "lkf": float((-2 * e * pt * (1 - pt) * yf * kf).sum() / n),
        }
        grads["w"][~free_mask] = 0
        if use_pst and len(keys):
            grads["p"] = Pf_.T @ gz + 2 * args.lam_p * p / 1e4
        params = {"w": w, "b": b, "t": t, "lkf": lkf, "p": p}
        lrs = {"w": args.lr, "b": args.lr, "t": args.lr, "lkf": 0.002, "p": args.lr}
        for k, g in grads.items():
            m[k] = beta1 * m.get(k, 0) + (1 - beta1) * g
            v[k] = beta2 * v.get(k, 0) + (1 - beta2) * g * g
            step = lrs[k] * (m[k] / (1 - beta1 ** it)) / (np.sqrt(v[k] / (1 - beta2 ** it)) + eps)
            params[k] = params[k] - step
        w, b, t, lkf, p = params["w"], params["b"], params["t"], params["lkf"], params["p"]
        if it % 500 == 0 or it == iters:
            print(f"    it {it} fit {loss_of(w, p if use_pst else np.zeros(len(keys)), b, t, np.exp(lkf), ~val):.6f} "
                  f"val {loss_of(w, p if use_pst else np.zeros(len(keys)), b, t, np.exp(lkf), val):.6f} kf {np.exp(lkf):.5f}", flush=True)
    return w, (p if use_pst else np.zeros(len(keys))), b, t, float(np.exp(lkf))


res = {}
print("baseline: hand-set weights, only bias/tempo/Kf fitted")
nofree = np.zeros(NEW, dtype=bool)
wb, pb, bb, tb, kb = fit(nofree, False, 1500)
res["baseline"] = {"val": loss_of(wb, pb, bb, tb, kb, val), "fit": loss_of(wb, pb, bb, tb, kb, ~val), "kf": kb}
print("scalars tuned")
ws, ps, bs, ts, ks = fit(free, False, args.iters)
res["scalars"] = {"val": loss_of(ws, ps, bs, ts, ks, val), "fit": loss_of(ws, ps, bs, ts, ks, ~val), "kf": ks,
                  "w": [round(x) for x in ws]}
if args.pst:
    print("scalars + piece-square tables tuned")
    wp, pp, bp, tp, kp = fit(free, True, args.iters)
    res["pst"] = {"val": loss_of(wp, pp, bp, tp, kp, val), "fit": loss_of(wp, pp, bp, tp, kp, ~val), "kf": kp,
                  "w": [round(x) for x in wp]}
    full = [0] * 720
    for (pc, r, c), val_ in zip(keys, pp):
        for cc in {c, 8 - c}:
            full[pc * 90 + r * 9 + cc] = int(round(val_))
    res["pst"]["pst"] = full
    res["pst"]["bias"] = bp; res["pst"]["tempo"] = tp
    if NEW > 45:
        res["pst"]["w"][45] = int(round(tp))
res["scalars"]["bias"] = bs; res["scalars"]["tempo"] = ts
if NEW > 45:
    res["scalars"]["w"][45] = int(round(ts))
res["baseline"]["bias"] = bb; res["baseline"]["tempo"] = tb
json.dump(res, open(args.out, "w"))
names = ["-", "PV_C", "PV_P", "PV_M", "PV_S", "PV_J", "PV_K", "PV_G", "PH_CAN", "PH_SOL", "PH_ELE",
         "SOL_ADV", "SOL_CEN", "SOL_CONN", "SOL_PAL", "CHA_CEN", "CHA_OPEN", "CHA_HALF", "CHA_RANK",
         "CAN_CEN", "CAN_SCR", "CAN_PAL", "HOR_CEN", "HOR_LEG", "ELE_LEG", "GUA_PAL", "KD", "GEN_EXP",
         "U-", "U_C", "U_P", "U_M", "U_S", "U_J", "U_K", "U_G", "D-", "D_C", "D_P", "D_M", "D_S", "D_J", "D_K", "D_G", "MOB",
         "TEMPO", "MOB_C", "MOB_P", "MOB_M", "MOB_S",
         "T-", "THR_C", "THR_P", "THR_M", "THR_S", "THR_J", "THR_K", "THR_G"]
names += [f"slot{i}" for i in range(len(names), NEW)]
print("\nweights: name w0 -> scalars" + (" -> pst" if args.pst else ""))
for i in range(NEW):
    if free[i] or i == 1:
        line = f"  {names[i]:9s} {W0[i]:7.0f} -> {ws[i]:7.0f}"
        if args.pst:
            line += f" -> {wp[i]:7.0f}"
        print(line)
print({k: {kk: (round(vv, 6) if isinstance(vv, float) else None) for kk, vv in v.items() if kk in ("val", "fit", "kf", "bias", "tempo")} for k, v in res.items()})
