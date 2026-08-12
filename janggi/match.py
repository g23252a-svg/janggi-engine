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
import math
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


def run_match(a: Config, b: Config, games: int, seed: int, opening_plies: int) -> dict:
    wins = draws = losses = 0
    pairs = max(1, games // 2)
    for pair in range(pairs):
        start, opening = random_opening(seed + pair, opening_plies)
        for a_is_cho in (True, False):
            cho_cfg, han_cfg = (a, b) if a_is_cho else (b, a)
            winner, plies = play_game(cho_cfg, han_cfg, opening, start)
            a_side = "cho" if a_is_cho else "han"
            if winner == "draw":
                draws += 1
            elif winner == a_side:
                wins += 1
            else:
                losses += 1
            played = wins + draws + losses
            score = (wins + 0.5 * draws) / played
            print(
                f"  game {played}/{pairs * 2}: {a.name} as {a_side} -> {winner} "
                f"({plies} plies) | W/D/L {wins}/{draws}/{losses} "
                f"score {score * 100:.1f}%",
                flush=True,
            )
    return summarize(a.name, b.name, wins, draws, losses)


def summarize(a_name: str, b_name: str, wins: int, draws: int, losses: int) -> dict:
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
    lo, hi = score - 1.96 * stderr, score + 1.96 * stderr
    if score in (0.0, 1.0):
        elo = float("inf") if score == 1.0 else float("-inf")
    else:
        elo = -400.0 * math.log10(1.0 / score - 1.0)
    verdict = (
        "A is stronger" if lo > 0.5
        else "B is stronger" if hi < 0.5
        else "not distinguishable from noise"
    )
    print()
    print(f"{a_name} vs {b_name}: +{wins} ={draws} -{losses} of {played}")
    print(f"  score {score * 100:.1f}%  (95% CI {lo * 100:.1f}%..{hi * 100:.1f}%)")
    print(f"  elo   {elo:+.0f}")
    print(f"  {verdict}")
    return {
        "wins": wins, "draws": draws, "losses": losses, "games": played,
        "score": score, "ci": (lo, hi), "elo": elo, "verdict": verdict,
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
    args = ap.parse_args()

    a = Config("A", args.depth_a or args.depth, args.nodes_a or args.nodes, args.time, args.a)
    b = Config("B", args.depth_b or args.depth, args.nodes_b or args.nodes, args.time, args.b)
    budget = f"{args.time}s/move" if args.time else f"{args.nodes} nodes/move"
    print(f"A: depth<={a.depth} {budget} opts={a.spec or 'default'}")
    print(f"B: depth<={b.depth} {budget} opts={b.spec or 'default'}")
    run_match(a, b, args.games, args.seed, args.opening_plies)


if __name__ == "__main__":
    main()
