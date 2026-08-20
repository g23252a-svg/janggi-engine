"""Command-line front end for the Janggi engine.

Examples
--------
Analyze the opening for Cho with a 3 second budget:
    python -m janggi.cli --analyze cho --time 3

Watch the engine play itself:
    python -m janggi.cli --selfplay --moves 40 --time 1.0

Benchmark the search (useful before and after any change):
    python -m janggi.cli --bench
"""

from __future__ import annotations

import argparse
import time

from ._version import __version__
from .board import Board, Move, HAN, CHO, FORMATIONS
from .repetition import RepetitionTracker
from .score import judge
from .search import Engine, SearchOptions, _HAVE_CORE

PIECE_KO = {"K": "궁", "C": "차", "P": "포", "M": "마", "S": "상", "G": "사", "J": "졸"}


def _fmt(mv) -> str:
    if mv is None:
        return "(no move)"
    cap = f" x{PIECE_KO.get(mv.captured, mv.captured)}" if mv.captured else ""
    return f"({mv.fr},{mv.fc})->({mv.tr},{mv.tc}){cap}"


def _fmt_pv(pv) -> str:
    return " ".join(f"({a},{b})->({c},{d})" for a, b, c, d in pv) or "-"


def _report(engine: Engine) -> str:
    s = engine.stats
    return (
        f"depth {s.depth_reached}, {s.total_nodes:,} nodes "
        f"({s.nps():,.0f}/s), tt hits {s.tt_hits:,}, {s.elapsed:.2f}s"
    )


def cmd_analyze(args) -> None:
    board = Board.standard(args.cho_formation, args.han_formation)
    side = CHO if args.analyze == "cho" else HAN
    engine = Engine(
        max_depth=args.depth, time_limit=args.time,
        options=SearchOptions.parse(args.options),
    )
    move, score = engine.search(board, side)
    print(board)
    print()
    label = "초(CHO)" if side == CHO else "한(HAN)"
    print(f"{label} best move: {_fmt(move)}")
    print(f"score (from {label}): {score}")
    print(f"pv: {_fmt_pv(engine.stats.pv)}")
    print(_report(engine))


def cmd_selfplay(args) -> None:
    board = Board.standard(args.cho_formation, args.han_formation)
    tracker = RepetitionTracker()
    tracker.record(board)
    hashes = [board.zobrist()]
    last_capture = 0
    options = SearchOptions.parse(args.options)

    for ply in range(args.moves):
        side = board.side_to_move
        legal = board.legal_moves(side)
        forbidden = {m.as_tuple() for m in legal if tracker.would_repeat_thrice(board, m)}
        if not legal or len(forbidden) == len(legal):
            winner = "한(HAN)" if side == CHO else "초(CHO)"
            reason = "no legal move" if not legal else "only repeating moves left"
            print(f"\n{'초' if side == CHO else '한'}: {reason}. {winner} wins.")
            break
        engine = Engine(max_depth=args.depth, time_limit=args.time, options=options)
        move, score = engine.search(
            board, side,
            forbidden_moves=forbidden,
            history_hashes=hashes[last_capture:-1],
            game_ply=ply,
        )
        if move is None:
            winner = "한(HAN)" if side == CHO else "초(CHO)"
            print(f"\n{'초' if side == CHO else '한'} has no move. {winner} wins.")
            break
        label = "초" if side == CHO else "한"
        print(
            f"{ply + 1:>3}. {label} {_fmt(move)}  "
            f"(score {score:>6}, d{engine.stats.depth_reached}, "
            f"{engine.stats.total_nodes:,}n)"
        )
        board.make(move)
        tracker.record(board)
        hashes.append(board.zobrist())
        if move.captured:
            last_capture = len(hashes) - 1
    else:
        result = judge(board)
        print(
            f"\nmove limit reached: 초 {result['cho']} / 한 {result['han']} "
            f"-> {result['winner']} by {result['margin']}"
        )
    print()
    print(board)


def cmd_bench(args) -> None:
    """Fixed workload for before/after comparisons. Prints nodes, not just time,
    so a result still means something on a different machine."""
    board = Board.standard()
    print("perft (opening, Cho to move)")
    for depth in (1, 2, 3, 4):
        started = time.time()
        total = _perft(board, CHO, depth)
        print(f"  depth {depth}: {total:>10,} nodes  {time.time() - started:6.2f}s")

    print("\nfixed-depth search from the opening")
    total_nodes = 0
    total_time = 0.0
    for depth in range(4, args.depth + 1):
        engine = Engine(max_depth=depth)
        move, score = engine.search(Board.standard(), CHO)
        total_nodes += engine.stats.total_nodes
        total_time += engine.stats.elapsed
        print(
            f"  depth {depth:>2}: {engine.stats.total_nodes:>12,} nodes "
            f"{engine.stats.elapsed:7.2f}s  {engine.stats.nps():>10,.0f} n/s  "
            f"best {_fmt(move)} ({score})"
        )
    print(f"\ntotal {total_nodes:,} nodes in {total_time:.2f}s "
          f"({total_nodes / total_time if total_time else 0:,.0f} n/s)")


def _perft(board: Board, side: int, depth: int) -> int:
    if depth == 0:
        return 1
    total = 0
    for mv in board.legal_moves(side):
        board.make(mv)
        total += _perft(board, -side, depth - 1)
        board.unmake()
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="Janggi engine")
    # Reports whether the compiled core is live as well as the version: the two
    # together are what determines how strong this install actually plays.
    p.add_argument("--version", action="version",
                   version=f"janggi-engine {__version__} "
                           f"({'compiled core' if _HAVE_CORE else 'pure Python'})")
    p.add_argument("--analyze", choices=["cho", "han"], help="analyze the opening for a side")
    p.add_argument("--selfplay", action="store_true", help="run an engine vs engine game")
    p.add_argument("--bench", action="store_true", help="run the standard benchmark")
    p.add_argument("--moves", type=int, default=20, help="number of plies in self-play")
    p.add_argument("--depth", type=int, default=8, help="max search depth")
    p.add_argument("--time", type=float, default=None, help="time limit per move in seconds")
    p.add_argument("--options", default="", help='search options, e.g. "nmp=0,lmr=0"')
    p.add_argument("--cho-formation", default="msm_s", choices=list(FORMATIONS))
    p.add_argument("--han-formation", default="msm_s", choices=list(FORMATIONS))
    args = p.parse_args()

    if args.bench:
        cmd_bench(args)
    elif args.selfplay:
        cmd_selfplay(args)
    elif args.analyze:
        cmd_analyze(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
