"""Our engine against Fairy-Stockfish, refereed by our own rules (an outside yardstick).

usage: fsf_match.py --games N --seed S --nodes OURS --spec "..." --fsf-nodes N
                    [--fsf-depth D] [--plies 6] --log X.jsonl [--resume]

Rules are ours (janggi.match.play_game): no pass, a third repetition is
illegal (and having only such moves loses), no legal move loses, 200 plies
then points. FSF is held to them by `go ... searchmoves <our legal moves>`, so
it can neither pass nor repeat a third time. FSF gets the whole move list so
it knows the game's history. Openings are janggi.match.random_opening, each
played twice with colours swapped."""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fsf import FSF, board_to_fen, move_to_uci  # noqa: E402
from janggi.board import Board, CHO, HAN  # noqa: E402
from janggi.match import Config, random_opening, _history_hashes, MAX_MOVES  # noqa: E402
from janggi.repetition import RepetitionTracker  # noqa: E402
from janggi.score import judge  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--games", type=int, default=20)
ap.add_argument("--seed", type=int, default=20261018)
ap.add_argument("--plies", type=int, default=6)
ap.add_argument("--nodes", type=int, default=300_000)
ap.add_argument("--spec", default="")
ap.add_argument("--fsf-nodes", type=int, default=0)
ap.add_argument("--fsf-depth", type=int, default=0)
ap.add_argument("--fsf-movetime", type=int, default=0)
ap.add_argument("--log", required=True)
ap.add_argument("--resume", action="store_true")
args = ap.parse_args()

ours = Config("ours", nodes=args.nodes, spec=args.spec)
budget = {"nodes": args.nodes, "spec": args.spec, "fsf_nodes": args.fsf_nodes,
          "fsf_depth": args.fsf_depth, "fsf_movetime": args.fsf_movetime, "plies": args.plies}

done = {}
if args.resume and os.path.exists(args.log):
    for ln in open(args.log):
        r = json.loads(ln)
        if r["budget"] != budget:
            raise SystemExit(f"log {args.log} holds a different match: {r['budget']} vs {budget}")
        done[(r["seed"], r["ours_cho"])] = r

fsf = FSF(threads=1, hash_mb=64)


def play(start, opening, ours_cho):
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
    eng = ours.build()
    fsf.new_game()
    our_side = CHO if ours_cho else HAN
    nodes_used = 0
    for ply in range(len(opening), MAX_MOVES):
        side = board.side_to_move
        legal = board.legal_moves(side)
        loser = "cho" if side == CHO else "han"
        winner = "han" if side == CHO else "cho"
        if not legal:
            return winner, ply, ("mate" if board.in_check(side) else "stalemate"), nodes_used
        forbidden = {m.as_tuple() for m in legal if tracker.would_repeat_thrice(board, m)}
        allowed = [m for m in legal if m.as_tuple() not in forbidden]
        if not allowed:
            return winner, ply, "repetition", nodes_used
        if side == our_side:
            move, _ = eng.search(board, side, forbidden_moves=forbidden,
                                 history_hashes=_history_hashes(hashes, last_cap), game_ply=ply)
            nodes_used += eng.stats.total_nodes
            if move is None:
                return winner, ply, "no_move", nodes_used
        else:
            best, _ = fsf.analyse(start_fen, moves=ucis, nodes=args.fsf_nodes or None,
                                  depth=args.fsf_depth or None, movetime=args.fsf_movetime or None,
                                  searchmoves=[move_to_uci(m.as_tuple()) for m in allowed])
            byuci = {move_to_uci(m.as_tuple()): m for m in allowed}
            move = byuci.get(best)
            if move is None:
                return winner, ply, f"fsf_illegal:{best}", nodes_used
        ucis.append(move_to_uci(move.as_tuple()))
        board.make(move); tracker.record(board); hashes.append(board.zobrist())
        if move.captured:
            last_cap = len(hashes) - 1
    return judge(board)["winner"], MAX_MOVES, "cap", nodes_used


w = d = l = 0
pairs = []
log = open(args.log, "a")
t0 = time.time()
for p in range(max(1, args.games // 2)):
    seed = args.seed + p
    start, opening = random_opening(seed, args.plies)
    pp = 0.0
    for ours_cho in (True, False):
        rec = done.get((seed, ours_cho))
        if rec is None:
            tg = time.time()
            winner, plies, reason, nodes_used = play(start, opening, ours_cho)
            rec = {"seed": seed, "ours_cho": ours_cho, "winner": winner, "plies": plies,
                   "reason": reason, "budget": budget, "nodes_used": nodes_used,
                   "secs": round(time.time() - tg, 1)}
            log.write(json.dumps(rec) + "\n"); log.flush()
        our = "cho" if ours_cho else "han"
        if rec["winner"] == "draw":
            d += 1; pp += 0.5
        elif rec["winner"] == our:
            w += 1; pp += 1
        else:
            l += 1
        n = w + d + l
        print(f"  game {n}: ours as {our} -> {rec['winner']} ({rec['plies']} plies, {rec['reason']}) "
              f"W/D/L {w}/{d}/{l} {100*(w+0.5*d)/n:.1f}% [{time.time()-t0:.0f}s]", flush=True)
    pairs.append(pp)
fsf.close()
n = w + d + l
s = (w + 0.5 * d) / n
elo = -400 * math.log10(1 / s - 1) if 0 < s < 1 else float("inf") * (1 if s else -1)
print(f"RESULT ours {w}/{d}/{l} of {n}: {100*s:.1f}% ({elo:+.0f} elo vs FSF {budget})")
