"""Static evaluation for Janggi positions.

Returns a score in centipawn-like units from HAN's perspective: positive favors
HAN, negative favors CHO. The search negates appropriately.

Beyond raw material this evaluator adds Janggi-specific terms:
  * soldier advancement and central-file soldiers,
  * mobility of line pieces and the horse,
  * general safety,
  * cannon screen bonus,
  * loose-piece safety for valuable material.
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

W_SOLDIER_ADVANCE = 8
W_SOLDIER_CENTRAL = 10
W_CENTER_FILE = 3
W_MOBILITY = 2
W_GENERAL_EXPOSED = 12
W_GUARD_COVER = 6
W_CANNON_SCREEN = 15
W_KING_PRESSURE = 18

# In official Janggi scoring, the late game is decided by remaining material
# points after the move limit. The normal evaluator already uses the same
# material scale, but positional terms can still distract the engine in long
# games. From the late middlegame onward, lock harder onto the actual score.
W_ENDGAME_SCORE_LOCK = 2

W_HANGING_UNDEFENDED = {"C": 360, "P": 260, "M": 190, "S": 130, "G": 90, "J": 25}
W_HANGING_DEFENDED = {"C": 120, "P": 90, "M": 65, "S": 40, "G": 25, "J": 0}


def _general_exposure(board: Board, side: int) -> int:
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
    g = board.find_general(side)
    if g is None:
        return 0
    gr, gc = g
    enemy = -side
    danger = 0
    if board.fast_is_attacked(gr, gc, enemy):
        danger += 12

    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1)):
        nr, nc = gr + dr, gc + dc
        if not in_palace(nr, nc, side):
            continue
        if dr != 0 and dc != 0:
            if not (on_palace_diagonal(gr, gc) and on_palace_diagonal(nr, nc)):
                continue
        occ = board.grid[nr][nc]
        if occ is not None and occ[1] == side:
            continue
        if board.fast_is_attacked(nr, nc, enemy):
            danger += 3
    return danger


def _loose_piece_risk(board: Board, side: int) -> int:
    enemy = -side
    risk = 0
    for r in range(ROWS):
        for c in range(COLS):
            p = board.grid[r][c]
            if p is None or p[1] != side or p[0] == "K":
                continue
            kind = p[0]
            if not board.fast_is_attacked(r, c, enemy):
                continue
            if board.fast_is_attacked(r, c, side):
                risk += W_HANGING_DEFENDED.get(kind, 0)
            else:
                risk += W_HANGING_UNDEFENDED.get(kind, 0)
    return risk


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
                return t[0] != "P"
            nr += dr
            nc += dc
    return False


def evaluate(board: Board, include_mobility: bool = True) -> int:
    score = 0
    material_score = 0
    g = board.grid
    for r in range(ROWS):
        for c in range(COLS):
            p = g[r][c]
            if p is None:
                continue
            kind, side = p
            base = PIECE_VALUE[kind]
            material_score += base if side == HAN else -base
            v = base

            if kind == "J":
                adv = (r if side == HAN else (ROWS - 1 - r))
                v += adv * W_SOLDIER_ADVANCE
                if 3 <= c <= 5:
                    v += W_SOLDIER_CENTRAL
            elif kind in ("C", "P", "M"):
                v += (4 - abs(c - 4)) * W_CENTER_FILE
                if kind == "P" and _cannon_has_screen(board, r, c):
                    v += W_CANNON_SCREEN

            score += v if side == HAN else -v

    # Score-lock mode for long games. Board._history length is the played ply
    # count in real games and also works inside search because make/unmake keeps
    # it consistent. After 120 ply, preserving official material score matters
    # more than activity or shape.
    ply = len(getattr(board, "_history", ()))
    if ply >= 120:
        score += material_score * W_ENDGAME_SCORE_LOCK
    elif ply >= 80:
        score += material_score

    if include_mobility:
        mob = len(board.generate_pseudo(HAN)) - len(board.generate_pseudo(CHO))
        score += mob * W_MOBILITY

    score -= _general_exposure(board, HAN) * W_GENERAL_EXPOSED
    score += _general_exposure(board, CHO) * W_GENERAL_EXPOSED
    score += _guards_alive(board, HAN) * W_GUARD_COVER
    score -= _guards_alive(board, CHO) * W_GUARD_COVER

    score -= _king_attack_pressure(board, HAN) * W_KING_PRESSURE
    score += _king_attack_pressure(board, CHO) * W_KING_PRESSURE

    score -= _loose_piece_risk(board, HAN)
    score += _loose_piece_risk(board, CHO)

    return score
