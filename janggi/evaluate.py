"""Static evaluation for Janggi positions.

Returns a score in centipawn-like units from HAN's perspective: positive favors
HAN, negative favors CHO. The search negates appropriately.

Beyond raw material this evaluator adds Janggi-specific terms:
  * soldier advancement and central-file soldiers (they gain palace-attacking
    diagonals once inside the enemy palace),
  * mobility of line pieces (chariot / cannon) and the horse,
  * general safety (penalize an exposed general, reward guards/elephant cover),
  * a small bonus for cannons that have a usable screen.

These are deliberately simple, transparent terms. They are the single place
where positional knowledge lives; tuning happens here, not in the search.
"""

from __future__ import annotations

from .board import (
    Board,
    HAN,
    CHO,
    ROWS,
    COLS,
    PIECE_VALUE,
    in_board,
    in_palace,
    on_palace_diagonal,
)

# Weights for positional terms (kept small relative to material).
W_SOLDIER_ADVANCE = 8
W_SOLDIER_CENTRAL = 10
W_CENTER_FILE = 3
W_MOBILITY = 2
W_GENERAL_EXPOSED = 12
W_GUARD_COVER = 6
W_CANNON_SCREEN = 15
# Penalty per attacked palace square near one's own general. Tuned to be large
# enough that an opponent's attack on the palace outweighs small material gains,
# but not so large it panics over a single harmless attacked square.
W_KING_PRESSURE = 18


def _general_exposure(board: Board, side: int) -> int:
    """Count empty orthogonal palace squares around the general (more = worse)."""
    g = board.find_general(side)
    if g is None:
        return 0
    r, c = g
    exposed = 0
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if in_palace(nr, nc, side) and board.grid[nr][nc] is None:
            exposed += 1
    return exposed


def _king_attack_pressure(board: Board, side: int) -> int:
    """Danger to `side`'s general, kept affordable for per-leaf evaluation.

    Combines:
      * In check now (general's square attacked): heaviest single signal.
      * Mate-net pressure: how many of the general's own palace escape squares
        (the empty palace squares it could flee to) are attacked by the enemy.
        A general whose escape squares are being taken away is being mated even
        before the actual check lands — the plain in-check test fires a move too
        late, which is exactly how a palace mate slips past a shallow search.

    Only the general's palace neighbourhood is scanned, so cost stays bounded.
    """
    g = board.find_general(side)
    if g is None:
        return 0
    gr, gc = g
    enemy = -side
    danger = 0
    if board.fast_is_attacked(gr, gc, enemy):
        danger += 12

    # Count attacked squares the general could otherwise step to (orthogonal +
    # palace diagonals), within its own palace. Each one the enemy covers is a
    # closing escape route.
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1)):
        nr, nc = gr + dr, gc + dc
        if not in_palace(nr, nc, side):
            continue
        # Diagonal steps are only legal along palace diagonal points.
        if dr != 0 and dc != 0:
            if not (on_palace_diagonal(gr, gc) and on_palace_diagonal(nr, nc)):
                continue
        occ = board.grid[nr][nc]
        if occ is not None and occ[1] == side:
            continue  # blocked by own piece (not an escape square)
        if board.fast_is_attacked(nr, nc, enemy):
            danger += 3
    return danger



def _guards_alive(board: Board, side: int) -> int:
    count = 0
    for r in range(ROWS):
        for c in range(COLS):
            p = board.grid[r][c]
            if p is not None and p[1] == side and p[0] in ("G", "S"):
                count += 1
    return count


def _cannon_has_screen(board: Board, r: int, c: int) -> bool:
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        while in_board(nr, nc):
            t = board.grid[nr][nc]
            if t is not None:
                # A non-cannon piece directly in line can act as a screen.
                return t[0] != "P"
            nr += dr
            nc += dc
    return False


def evaluate(board: Board, include_mobility: bool = True) -> int:
    score = 0
    g = board.grid
    for r in range(ROWS):
        for c in range(COLS):
            p = g[r][c]
            if p is None:
                continue
            kind, side = p
            v = PIECE_VALUE[kind]

            if kind == "J":
                # Reward advancement toward the far edge.
                adv = (r if side == HAN else (ROWS - 1 - r))
                v += adv * W_SOLDIER_ADVANCE
                if 3 <= c <= 5:
                    v += W_SOLDIER_CENTRAL
            elif kind in ("C", "P", "M"):
                v += (4 - abs(c - 4)) * W_CENTER_FILE
                if kind == "P" and _cannon_has_screen(board, r, c):
                    v += W_CANNON_SCREEN

            score += v if side == HAN else -v

    # Mobility differential (cheap proxy: pseudo-legal move counts). This is the
    # single most expensive term, so it can be disabled for leaf/quiescence
    # evaluation where speed matters more than the small positional signal.
    if include_mobility:
        mob = len(board.generate_pseudo(HAN)) - len(board.generate_pseudo(CHO))
        score += mob * W_MOBILITY

    # General safety.
    score -= _general_exposure(board, HAN) * W_GENERAL_EXPOSED
    score += _general_exposure(board, CHO) * W_GENERAL_EXPOSED
    score += _guards_alive(board, HAN) * W_GUARD_COVER
    score -= _guards_alive(board, CHO) * W_GUARD_COVER

    # King-attack pressure: penalize having one's own palace attacked. This is
    # what stops the engine from drifting into positions where the opponent is
    # massing an attack on the general even though material is still even.
    score -= _king_attack_pressure(board, HAN) * W_KING_PRESSURE
    score += _king_attack_pressure(board, CHO) * W_KING_PRESSURE

    return score
