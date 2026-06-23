"""Static Exchange Evaluation (SEE).

Given a capture move, SEE computes the net material outcome assuming both sides
keep capturing on the destination square with their least valuable attacker
until neither wants to continue. This is what tells the engine that "chariot
takes cannon" is actually a loss if a chariot recaptures: +7 (cannon) then
-13 (our chariot) = -6.

This directly addresses the recurring mistake seen in recorded games where a
capture looked good but lost more material to the recapture.

It is a *static* estimate (no search), so it is cheap enough to run on every
capture during move ordering and quiescence.
"""

from __future__ import annotations

from .board import Board, Move, HAN, CHO, ROWS, COLS, PIECE_VALUE


def _attackers_of(board: Board, r: int, c: int, side: int) -> list[tuple[int, int, int]]:
    """Return (value, fr, fc) for each `side` piece that can capture on (r, c)."""
    attackers = []
    for mv in board.generate_pseudo(side):
        if mv.tr == r and mv.tc == c:
            piece = board.grid[mv.fr][mv.fc]
            if piece is not None:
                attackers.append((PIECE_VALUE[piece[0]], mv.fr, mv.fc))
    return attackers


def see(board: Board, mv: Move) -> int:
    """Net material gain (in PIECE_VALUE units) of the capture `mv` for the
    side that moves, after all favorable recaptures on the target square.

    Positive = good for the mover. Non-captures return 0.
    """
    target = board.grid[mv.tr][mv.tc]
    if target is None:
        return 0  # not a capture

    mover = board.grid[mv.fr][mv.fc]
    if mover is None:
        return 0
    side = mover[1]

    # gain[d] holds the material balance if the exchange stops after d captures.
    gain = [PIECE_VALUE[target[0]]]
    board.make(mv)
    captured_count = 1
    try:
        on_square_value = PIECE_VALUE[mover[0]]  # piece now on the target square
        attacker_side = -side
        while True:
            attackers = _attackers_of(board, mv.tr, mv.tc, attacker_side)
            if not attackers:
                break
            attackers.sort(key=lambda a: a[0])  # cheapest attacker first
            _, fr, fc = attackers[0]
            # Recapturing wins the piece currently on the square.
            gain.append(on_square_value - gain[-1])
            on_square_value = attackers[0][0]
            board.make(Move(fr, fc, mv.tr, mv.tc, board.grid[mv.tr][mv.tc][0]))
            captured_count += 1
            attacker_side = -attacker_side
        for _ in range(captured_count):
            board.unmake()
    finally:
        pass

    # Standard SEE backward pass: each side will only continue capturing if it
    # improves its result, otherwise it stands pat.
    for i in range(len(gain) - 1, 0, -1):
        gain[i - 1] = -max(-gain[i - 1], gain[i])
    return gain[0]
