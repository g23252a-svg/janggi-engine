"""Opening book: learn common opening moves from recorded games (기보).

Given one or more gibo JSON files, this builds a book mapping each position
(by Zobrist hash) to the moves played from it, with frequencies. Even a single
game seeds the book; as more games accumulate, the most-played reply in a known
position can be served instantly instead of searching.

Usage:
    python -m janggi.book build gibo1.json gibo2.json -o book.json
    python -m janggi.book show book.json

The engine can later load the book and, when the current position is known,
prefer the most frequent recorded reply (optionally only when it agrees with a
shallow search, to avoid memorizing a blunder from a weak game).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from .board import Board, Move, CHO, HAN
from .search import zobrist_hash
from .gibo import Gibo


def build_book(gibo_paths: list[str]) -> dict:
    """Return {position_hash: {move_str: count}} from the given gibo files."""
    book: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    games = 0
    for path in gibo_paths:
        with open(path, encoding="utf-8") as f:
            gibo = Gibo.from_json(f.read())
        ok, msg = gibo.validate()
        if not ok:
            print(f"skip {path}: {msg}")
            continue
        games += 1
        board = gibo.starting_board()
        # Record the opening and early middlegame (first ~30 plies). Book theory
        # mostly lives in the opening, but extending coverage to ~30 plies lets a
        # known winning line guide the player deeper, past the point where the
        # engine alone tended to drift in close games.
        for m in gibo.moves[:30]:
            h = str(zobrist_hash(board))
            key = f"{m['fr']},{m['fc']},{m['tr']},{m['tc']}"
            book[h][key] += 1
            board.make(Move(m["fr"], m["fc"], m["tr"], m["tc"], m.get("captured")))
    # Convert to plain dict for JSON.
    out = {h: dict(moves) for h, moves in book.items()}
    return {"games": games, "positions": len(out), "book": out}


def load_book(path: str) -> dict:
    """Load a book file produced by build_book; returns the inner book dict
    mapping position-hash strings to {move_str: count}. Returns {} on failure."""
    import os
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("book", {})
    except Exception:
        return {}


def book_move(book: dict, board: "Board", min_count: int = 1) -> "Move | None":
    """Return the most-frequently-played book move for the current position,
    or None if the position is not in the book. Ties broken by frequency only.

    The move is validated against the board's legal moves before being returned,
    so a stale or wrong book entry can never produce an illegal move.
    """
    from .board import Move
    from .search import zobrist_hash

    key = str(zobrist_hash(board))
    replies = book.get(key)
    if not replies:
        return None
    # Pick the most-played reply that is currently legal.
    legal = {m.as_tuple(): m for m in board.legal_moves(board.side_to_move)}
    best_mv = None
    best_n = 0
    for move_str, count in replies.items():
        if count < min_count:
            continue
        fr, fc, tr, tc = (int(x) for x in move_str.split(","))
        cand = legal.get((fr, fc, tr, tc))
        if cand is not None and count > best_n:
            best_mv = cand
            best_n = count
    return best_mv


def cmd_build(args) -> None:
    result = build_book(args.files)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"built book from {result['games']} game(s): "
          f"{result['positions']} positions -> {args.output}")


def cmd_show(args) -> None:
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)
    print(f"games: {data['games']}, positions: {data['positions']}")
    shown = 0
    for h, moves in data["book"].items():
        best = max(moves.items(), key=lambda kv: kv[1])
        print(f"  pos {h[:10]}...: {len(moves)} reply/replies, top {best[0]} x{best[1]}")
        shown += 1
        if shown >= 15:
            print("  ...")
            break


def main() -> None:
    p = argparse.ArgumentParser(description="Janggi opening book tool")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a book from gibo files")
    b.add_argument("files", nargs="+")
    b.add_argument("-o", "--output", default="book.json")
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("show", help="show a book summary")
    s.add_argument("file")
    s.set_defaults(func=cmd_show)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
