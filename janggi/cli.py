"""Command-line demo for the Janggi engine.

Examples
--------
Let the engine analyze the opening position for Cho:
    python -m janggi.cli --analyze cho --depth 4

Play a quick self-play game (engine vs engine) to watch it move:
    python -m janggi.cli --selfplay --moves 20 --time 1.0
"""

from __future__ import annotations

import argparse

from .board import Board, HAN, CHO, FORMATIONS
from .search import Engine


def _fmt(mv) -> str:
    if mv is None:
        return "(no move)"
    cap = f" x{mv.captured}" if mv.captured else ""
    return f"({mv.fr},{mv.fc})->({mv.tr},{mv.tc}){cap}"


def cmd_analyze(args) -> None:
    board = Board.standard(args.cho_formation, args.han_formation)
    side = CHO if args.analyze == "cho" else HAN
    engine = Engine(max_depth=args.depth, time_limit=args.time)
    move, score = engine.search(board, side)
    print(board)
    print()
    label = "초(CHO)" if side == CHO else "한(HAN)"
    print(f"{label} best move: {_fmt(move)}")
    print(f"score (from {label}): {score}")
    print(
        f"depth reached {engine.stats.depth_reached}, "
        f"nodes {engine.stats.nodes}, q {engine.stats.qnodes}, tt hits {engine.stats.tt_hits}"
    )


def cmd_selfplay(args) -> None:
    board = Board.standard(args.cho_formation, args.han_formation)
    side = CHO
    for ply in range(args.moves):
        engine = Engine(max_depth=args.depth, time_limit=args.time)
        move, score = engine.search(board, side)
        if move is None:
            winner = "한(HAN)" if side == CHO else "초(CHO)"
            print(f"\n{('초' if side==CHO else '한')} has no legal move. {winner} wins.")
            break
        label = "초" if side == CHO else "한"
        print(f"{ply+1:>3}. {label} {_fmt(move)}  (score {score}, d{engine.stats.depth_reached})")
        board.make(move)
        side = -side
    print()
    print(board)


def main() -> None:
    p = argparse.ArgumentParser(description="Janggi engine demo")
    p.add_argument("--analyze", choices=["cho", "han"], help="analyze opening for a side")
    p.add_argument("--selfplay", action="store_true", help="run an engine vs engine game")
    p.add_argument("--moves", type=int, default=20, help="number of plies in self-play")
    p.add_argument("--depth", type=int, default=4, help="max search depth")
    p.add_argument("--time", type=float, default=None, help="time limit per move in seconds")
    p.add_argument("--cho-formation", default="msm_s", choices=list(FORMATIONS))
    p.add_argument("--han-formation", default="msm_s", choices=list(FORMATIONS))
    args = p.parse_args()

    if args.selfplay:
        cmd_selfplay(args)
    elif args.analyze:
        cmd_analyze(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
