"""Head-to-head A/B testing for engine changes.

Any change to search or evaluation is a guess until two versions of the engine
have actually played each other. This runs that match and reports whether the
result is distinguishable from noise.

    # is null-move pruning earning its keep?
    python -m janggi.match --games 100 --nodes 150000 --a "" --b "nmp=0"

    # same search, different depth budget
    python -m janggi.match --games 40 --depth-a 8 --depth-b 6

Design notes that matter for the numbers being meaningful:

* Games are played in PAIRS. The same opening is played once with A as Cho and
  once with A as Han, so an opening that simply favours one colour cannot
  flatter either engine.
* Openings are diversified by playing a few seeded random plies before the
  engines take over, otherwise every game is the same game.
* Budgets default to NODES, not seconds, so a result does not depend on what
  else the machine was doing.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from dataclasses import dataclass

from .board import Board, Move, CHO, HAN, FORMATIONS
from .repetition import RepetitionTracker
from .score import judge
from .search import Engine, SearchOptions

MAX_MOVES = 200


@dataclass
class Config:
    """One side of the match."""

    name: str
    depth: int = 64
    nodes: int = 150_000
    time_limit: float | None = None
    spec: str = ""

    def build(self) -> Engine:
        options = SearchOptions.parse(self.spec)
        if self.time_limit is None and self.nodes:
            options = SearchOptions(
                **{**options.__dict__, "node_limit": options.node_limit or self.nodes}
            )
        return Engine(max_depth=self.depth, time_limit=self.time_limit, options=options)


def _history_hashes(hashes: list[int], last_capture_at: int) -> list[int]:
    """Position keys since the last capture, excluding the current one."""
    return hashes[last_capture_at:-1]


def play_game(
    cho: Config, han: Config, opening_plies: list[Move], start: tuple[str, str]
) -> tuple[str, int]:
    """Play one game; return (winner, plies_played)."""
    board = Board.standard(*start)
    tracker = RepetitionTracker()
    tracker.record(board)
    hashes = [board.zobrist()]
    last_capture_at = 0

    for mv in opening_plies:
        board.make(mv)
        tracker.record(board)
        hashes.append(board.zobrist())
        if mv.captured:
            last_capture_at = len(hashes) - 1

    engines = {CHO: cho.build(), HAN: han.build()}
    for ply in range(len(opening_plies), MAX_MOVES):
        side = board.side_to_move
        legal = board.legal_moves(side)
        if not legal:
            return ("han" if -side == HAN else "cho"), ply
        forbidden = {
            m.as_tuple() for m in legal if tracker.would_repeat_thrice(board, m)
        }
        if len(forbidden) == len(legal):
            # Every continuation would be an illegal third repetition, which
            # under these rules loses for the side that has to move.
            return ("han" if -side == HAN else "cho"), ply
        move, _score = engines[side].search(
            board,
            side,
            forbidden_moves=forbidden,
            history_hashes=_history_hashes(hashes, last_capture_at),
            game_ply=ply,
        )
        if move is None:
            return ("han" if -side == HAN else "cho"), ply
        board.make(move)
        tracker.record(board)
        hashes.append(board.zobrist())
        if move.captured:
            last_capture_at = len(hashes) - 1
    return judge(board)["winner"], MAX_MOVES


def random_opening(seed: int, plies: int) -> tuple[tuple[str, str], list[Move]]:
    """A seeded opening both pairings will share."""
    rng = random.Random(seed)
    forms = sorted(FORMATIONS)
    start = (rng.choice(forms), rng.choice(forms))
    board = Board.standard(*start)
    moves: list[Move] = []
    for _ in range(plies):
        legal = board.legal_moves(board.side_to_move)
        if not legal:
            break
        mv = rng.choice(legal)
        moves.append(mv)
        board.make(mv)
    return start, moves


def _load_log(path: str | None) -> dict[tuple[int, bool], dict]:
    """Finished games from an earlier run of the same match, keyed by what
    identifies a game: the opening seed and which side A took."""
    done: dict[tuple[int, bool], dict] = {}
    if not path or not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            done[(rec["seed"], rec["a_is_cho"])] = rec
    return done


def run_match(a: Config, b: Config, games: int, seed: int, opening_plies: int,
              log_path: str | None = None, resume: bool = False) -> dict:
    """Play the match. With ``log_path`` every finished game is appended as one
    JSON line, and with ``resume`` games already in that log are counted and
    skipped -- so a run killed at game 19 of 60 continues from game 20 instead
    of starting over. A game is identified by its opening seed and A's colour,
    so the same command line resumes the same match and nothing else; a
    different ``--seed`` or ``--games`` is a different match and the log is
    simply added to.

    The box that runs these gets reclaimed between sessions; two 60-game runs
    died mid-way with no checkpoint before this existed."""
    wins = draws = losses = 0
    pairs = max(1, games // 2)
    done = _load_log(log_path) if resume else {}
    skipped = 0
    pair_scores: list[float] = []
    log_fh = open(log_path, "a", encoding="utf-8") if log_path else None
    try:
        for pair in range(pairs):
            game_seed = seed + pair
            start, opening = random_opening(game_seed, opening_plies)
            pair_pts = 0.0
            for a_is_cho in (True, False):
                a_side = "cho" if a_is_cho else "han"
                rec = done.get((game_seed, a_is_cho))
                if rec is not None:
                    winner, plies = rec["winner"], rec["plies"]
                    skipped += 1
                else:
                    cho_cfg, han_cfg = (a, b) if a_is_cho else (b, a)
                    winner, plies = play_game(cho_cfg, han_cfg, opening, start)
                    if log_fh is not None:
                        log_fh.write(json.dumps({
                            "seed": game_seed, "a_is_cho": a_is_cho,
                            "winner": winner, "plies": plies,
                            "a": a.spec, "b": b.spec,
                        }) + "\n")
                        log_fh.flush()
                if winner == "draw":
                    draws += 1; pair_pts += 0.5
                elif winner == a_side:
                    wins += 1; pair_pts += 1.0
                else:
                    losses += 1
                played = wins + draws + losses
                score = (wins + 0.5 * draws) / played
                tag = " (from log)" if rec is not None else ""
                print(
                    f"  game {played}/{pairs * 2}: {a.name} as {a_side} -> {winner} "
                    f"({plies} plies) | W/D/L {wins}/{draws}/{losses} "
                    f"score {score * 100:.1f}%{tag}",
                    flush=True,
                )
            pair_scores.append(pair_pts)
    finally:
        if log_fh is not None:
            log_fh.close()
    if skipped:
        print(f"  ({skipped} game(s) taken from {log_path}; {pairs * 2 - skipped} played now)")
    return summarize(a.name, b.name, wins, draws, losses, pair_scores)


def pool_logs(paths: list[str]) -> dict:
    """One summary over several game logs -- shards of one match run in
    parallel with different --seed ranges, or a match resumed across runs."""
    wins = draws = losses = 0
    seen: set[tuple[int, bool]] = set()
    specs: set[tuple[str, str]] = set()
    by_pair: dict[int, dict[bool, float]] = {}
    for path in paths:
        for key, rec in _load_log(path).items():
            if key in seen:
                continue                     # the same game logged twice
            seen.add(key)
            specs.add((rec.get("a", ""), rec.get("b", "")))
            a_side = "cho" if rec["a_is_cho"] else "han"
            if rec["winner"] == "draw":
                draws += 1; pts = 0.5
            elif rec["winner"] == a_side:
                wins += 1; pts = 1.0
            else:
                losses += 1; pts = 0.0
            by_pair.setdefault(rec["seed"], {})[rec["a_is_cho"]] = pts
    if len(specs) > 1:
        print(f"  WARNING: pooling logs with different configurations: {sorted(specs)}")
    pair_scores = [sum(p.values()) for p in by_pair.values() if len(p) == 2]
    half = sum(1 for p in by_pair.values() if len(p) != 2)
    if half:
        print(f"  ({half} half-played pair(s) count in the score but not in the pair interval)")
    return summarize("A", "B", wins, draws, losses, pair_scores or None)


def summarize(a_name: str, b_name: str, wins: int, draws: int, losses: int,
              pair_scores: list[float] | None = None) -> dict:
    """Score, interval, elo and verdict.

    The two games of a colour-swapped pair share one opening, so they are not
    independent trials -- a pair that is decided by the opening comes out 2-0
    or 0-2 whichever engine is better, and treating those as two independent
    games made every interval in this repository too narrow. When
    ``pair_scores`` is given (A's points per pair, 0 / 0.5 / 1 / 1.5 / 2) the
    interval is computed over pairs. Without it the per-game formula is used,
    which is what the earlier releases reported; both are printed when the
    pair data exists so the two can be compared."""
    played = wins + draws + losses
    score = (wins + 0.5 * draws) / played if played else 0.0
    # Standard error of the per-game score, then a 95% interval. Draws carry no
    # variance of their own, which the 0.5-weighted variance below accounts for.
    var = (
        wins * (1.0 - score) ** 2
        + draws * (0.5 - score) ** 2
        + losses * (0.0 - score) ** 2
    ) / played if played else 0.0
    stderr = math.sqrt(var / played) if played else 0.0
    lo_game, hi_game = score - 1.96 * stderr, score + 1.96 * stderr
    lo, hi = lo_game, hi_game
    pair_ci = None
    if pair_scores:
        n = len(pair_scores)
        mean = sum(pair_scores) / n            # A's points per pair, out of 2
        pvar = sum((x - mean) ** 2 for x in pair_scores) / n
        pse = math.sqrt(pvar / n)
        lo, hi = (mean - 1.96 * pse) / 2.0, (mean + 1.96 * pse) / 2.0
        pair_ci = (lo, hi)
    lo = max(0.0, lo); hi = min(1.0, hi)
    # Clamp so a 2-0 run reports a finite number that survives json.dumps.
    clamped = min(max(score, 0.5 / max(played, 1)), 1.0 - 0.5 / max(played, 1))
    elo = -400.0 * math.log10(1.0 / clamped - 1.0)
    verdict = (
        "A is stronger" if lo > 0.5
        else "B is stronger" if hi < 0.5
        else "not distinguishable from noise"
    )
    print()
    print(f"{a_name} vs {b_name}: +{wins} ={draws} -{losses} of {played}")
    if pair_ci is not None:
        print(f"  score {score * 100:.1f}%  (95% CI over {len(pair_scores)} pairs "
              f"{lo * 100:.1f}%..{hi * 100:.1f}%; per-game formula would say "
              f"{max(0.0, lo_game) * 100:.1f}%..{min(1.0, hi_game) * 100:.1f}%)")
    else:
        print(f"  score {score * 100:.1f}%  (95% CI {lo * 100:.1f}%..{hi * 100:.1f}%)")
    print(f"  elo   {elo:+.0f}")
    print(f"  {verdict}")
    return {
        "wins": wins, "draws": draws, "losses": losses, "games": played,
        "score": score, "ci": (lo, hi), "elo": elo, "verdict": verdict,
        "pairs": len(pair_scores) if pair_scores else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Engine A/B match")
    ap.add_argument("--games", type=int, default=20, help="rounded down to a whole number of colour-swapped pairs")
    ap.add_argument("--nodes", type=int, default=150_000, help="node budget per move")
    ap.add_argument("--time", type=float, default=None, help="seconds per move instead of a node budget")
    ap.add_argument("--depth", type=int, default=64)
    ap.add_argument("--depth-a", type=int, default=None)
    ap.add_argument("--depth-b", type=int, default=None)
    ap.add_argument("--nodes-a", type=int, default=None)
    ap.add_argument("--nodes-b", type=int, default=None)
    ap.add_argument("--a", default="", help='search options for A, e.g. "nmp=0,lmr=0"')
    ap.add_argument("--b", default="", help="search options for B")
    ap.add_argument("--seed", type=int, default=20260812)
    ap.add_argument("--opening-plies", type=int, default=6)
    ap.add_argument("--log", default=None, help="append one JSON line per finished game here")
    ap.add_argument("--resume", action="store_true", help="count and skip games already in --log")
    ap.add_argument("--pool", nargs="+", metavar="LOG", default=None,
                    help="summarize these game logs together instead of playing")
    args = ap.parse_args()

    if args.pool:
        pool_logs(args.pool)
        return

    a = Config("A", args.depth_a or args.depth, args.nodes_a or args.nodes, args.time, args.a)
    b = Config("B", args.depth_b or args.depth, args.nodes_b or args.nodes, args.time, args.b)
    budget = f"{args.time}s/move" if args.time else f"{args.nodes} nodes/move"
    print(f"A: depth<={a.depth} {budget} opts={a.spec or 'default'}")
    print(f"B: depth<={b.depth} {budget} opts={b.spec or 'default'}")
    run_match(a, b, args.games, args.seed, args.opening_plies, args.log, args.resume)


if __name__ == "__main__":
    main()
