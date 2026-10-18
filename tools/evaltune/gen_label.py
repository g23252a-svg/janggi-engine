"""Generate FSF-labelled positions for evaluator tuning.

usage: gen_label.py --mode fsf|ours --games N --seed S --out X.jsonl
                    [--label-nodes 10000] [--ours-nodes 20000] [--eps 0.1]

Each game starts from janggi.match.random_opening(seed, 4..10 plies) and is
played under our rules (no pass, third repetition illegal, 200 plies). At every
position FSF searches `--label-nodes` nodes; its score (side to move, cp, mate
mapped to +-100000) is the label. The move played is FSF's best (mode fsf) or
our engine's at --ours-nodes (mode ours), replaced by a uniformly random legal
move with probability --eps so the set also holds unbalanced positions.

One JSON line per position: board as 90 chars (row 0 = HAN's back rank,
upper case CHO, lower case HAN, '.' empty; letters are ours: K G S M C P J),
side to move, game ply, label, and the move FSF preferred."""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fsf import FSF, board_to_fen, move_to_uci, score_cp  # noqa: E402
from janggi.board import Board, CHO, HAN  # noqa: E402
from janggi.match import Config, random_opening, _history_hashes, MAX_MOVES  # noqa: E402
from janggi.repetition import RepetitionTracker  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--mode", choices=("fsf", "ours"), default="fsf")
ap.add_argument("--games", type=int, default=100)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--out", required=True)
ap.add_argument("--label-nodes", type=int, default=10000)
ap.add_argument("--ours-nodes", type=int, default=20000)
ap.add_argument("--eps", type=float, default=0.1)
args = ap.parse_args()

done_games = set()
if os.path.exists(args.out):
    for ln in open(args.out):
        try:
            done_games.add(json.loads(ln)["g"])
        except Exception:
            pass

fsf = FSF(threads=1, hash_mb=32)
ours = Config("ours", nodes=args.ours_nodes)


def enc(board):
    out = []
    for r in range(10):
        for c in range(9):
            cell = board.grid[r][c]
            if cell is None:
                out.append(".")
            else:
                k, s = cell
                out.append(k if s == CHO else k.lower())
    return "".join(out)


fh = open(args.out, "a")
t0 = time.time()
npos = 0
for gi in range(args.games):
    gid = f"{args.mode}-{args.seed}-{gi}"
    if gid in done_games:
        continue
    rng = random.Random(f"{args.seed}:{gi}")
    start, opening = random_opening(rng.randrange(1 << 30), rng.randint(4, 10))
    board = Board.standard(*start)
    start_fen = board_to_fen(board, CHO)
    tracker = RepetitionTracker(); tracker.record(board)
    hashes = [board.zobrist()]; last_cap = 0
    ucis = []
    for mv in opening:
        ucis.append(move_to_uci(mv.as_tuple()))
        board.make(mv); tracker.record(board); hashes.append(board.zobrist())
        if mv.captured:
            last_cap = len(hashes) - 1
    eng = ours.build() if args.mode == "ours" else None
    fsf.new_game()
    rows = []
    for ply in range(len(opening), MAX_MOVES):
        side = board.side_to_move
        legal = board.legal_moves(side)
        if not legal:
            break
        forbidden = {m.as_tuple() for m in legal if tracker.would_repeat_thrice(board, m)}
        allowed = [m for m in legal if m.as_tuple() not in forbidden]
        if not allowed:
            break
        best, infos = fsf.analyse(start_fen, moves=ucis, nodes=args.label_nodes,
                                  searchmoves=[move_to_uci(m.as_tuple()) for m in allowed])
        if 1 not in infos:
            break
        lab = score_cp(infos[1])
        rows.append({"g": gid, "ply": ply, "stm": "cho" if side == CHO else "han",
                     "b": enc(board), "fsf": lab, "best": best, "incheck": board.in_check(side)})
        byuci = {move_to_uci(m.as_tuple()): m for m in allowed}
        if rng.random() < args.eps:
            move = rng.choice(allowed)
        elif args.mode == "fsf":
            move = byuci.get(best) or rng.choice(allowed)
        else:
            move, _ = eng.search(board, side, forbidden_moves=forbidden,
                                 history_hashes=_history_hashes(hashes, last_cap), game_ply=ply)
            if move is None:
                break
        ucis.append(move_to_uci(move.as_tuple()))
        board.make(move); tracker.record(board); hashes.append(board.zobrist())
        if move.captured:
            last_cap = len(hashes) - 1
        if abs(lab) >= 99000 and rng.random() < 0.5:
            break      # a found mate: the rest of the game teaches little
    for r in rows:
        fh.write(json.dumps(r) + "\n")
    fh.flush()
    npos += len(rows)
    print(f"  game {gi+1}/{args.games} plies {len(rows)} total {npos} positions {time.time()-t0:.0f}s", flush=True)
fsf.close()
