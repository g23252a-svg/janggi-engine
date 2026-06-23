"""Repetition handling (반복수 / 빅장 금지).

Korean chess forbids endlessly repeating the same position. The common rule of
thumb implemented here: the same board position (including side to move) may
not occur for a 3rd time. The side that would create the 3rd repetition must
play a different move; if it has no alternative, it is considered to have lost
(in casual play it is often ruled a loss for the side forcing the repetition).

This module is pure bookkeeping over Zobrist hashes so it stays cheap and
agrees with the search's notion of "same position".
"""

from __future__ import annotations

from collections import Counter

from .board import Board, Move
from .search import zobrist_hash


class RepetitionTracker:
    """Counts how many times each position has occurred in a game."""

    def __init__(self) -> None:
        self._counts: Counter[int] = Counter()

    def record(self, board: Board) -> int:
        """Record the current position; return how many times it has now occurred."""
        h = zobrist_hash(board)
        self._counts[h] += 1
        return self._counts[h]

    def count(self, board: Board) -> int:
        return self._counts[zobrist_hash(board)]

    def would_repeat_thrice(self, board: Board, mv: Move) -> bool:
        """True if playing mv would make the resulting position occur a 3rd time."""
        board.make(mv)
        try:
            n = self._counts[zobrist_hash(board)]
        finally:
            board.unmake()
        return n >= 2  # already seen twice -> this move makes it the 3rd

    def would_repeat_twice(self, board: Board, mv: Move) -> bool:
        """True if playing mv returns to a position already seen at least once.

        Used to discourage aimless shuffling (e.g. a general stepping back and
        forth) the moment it would re-create an earlier position, well before a
        hard 3-fold. The caller treats this as a soft ban: avoid such moves when
        any non-repeating move exists, but never get stuck if they're all that's
        left.
        """
        board.make(mv)
        try:
            n = self._counts[zobrist_hash(board)]
        finally:
            board.unmake()
        return n >= 1  # already seen once -> this move makes it the 2nd

    def legal_nonrepeating(self, board: Board, side: int) -> list[Move]:
        """Legal moves excluding any that would cause a 3rd repetition.

        Unlike a draw rule, this *prevents* the repeating move outright so the
        game keeps going with a different move. We do NOT fall back to allowing
        the repeating move even if it is the only one; the caller decides what
        happens in the rare case the filtered list is empty (which in practice
        means the side must have already had other options earlier).
        """
        moves = board.legal_moves(side)
        return [m for m in moves if not self.would_repeat_thrice(board, m)]

    def reset(self) -> None:
        self._counts.clear()
