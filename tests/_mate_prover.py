"""Exhaustive forced-mate prover, used only by the tests.

Deliberately independent of janggi.search: it enumerates legal moves and
recurses, so a tactical test built on it checks the engine rather than agreeing
with it. Exponential, so keep it to two or three of its own moves.
"""

from janggi.board import Board, Move


def mating_moves(board: Board, side: int, n: int) -> list[Move]:
    """Moves by `side` that force a win within `n` of its own moves.

    "Win" here means the opponent ends up with no legal move, which in Janggi
    loses whether it arose as checkmate or stalemate.
    """
    forcing: list[Move] = []
    for mv in board.legal_moves(side):
        board.make(mv)
        try:
            replies = board.legal_moves(-side)
            if not replies:
                forcing.append(mv)
                continue
            if n <= 1:
                continue
            if all(_opponent_is_lost(board, side, rep, n) for rep in replies):
                forcing.append(mv)
        finally:
            board.unmake()
    return forcing


def _opponent_is_lost(board: Board, side: int, reply: Move, n: int) -> bool:
    board.make(reply)
    try:
        return bool(mating_moves(board, side, n - 1))
    finally:
        board.unmake()
