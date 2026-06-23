"""Official Janggi scoring (점수제) and game-result judgment.

Standard Korean rules assign each piece a point value, and when a game ends by
move limit or agreement the side with more points wins. Han (한) receives a
1.5-point handicap bonus (덤) to offset Cho moving first.

Point values (official):
    차 (C) 13, 포 (P) 7, 마 (M) 5, 상 (S) 3, 사 (G) 3, 졸/병 (J) 2.
The general (궁) carries no point value (losing it ends the game outright).
"""

from __future__ import annotations

from .board import Board, HAN, CHO, ROWS, COLS

# Official point values used for end-of-game scoring (separate from the
# engine's internal search values in PIECE_VALUE).
SCORE_POINTS = {
    "C": 13,   # 차
    "P": 7,    # 포
    "M": 5,    # 마
    "S": 3,    # 상
    "G": 3,    # 사
    "J": 2,    # 졸 / 병
    "K": 0,    # 궁 (no point value)
}

# Han's handicap bonus for moving second.
HAN_BONUS = 1.5


def side_score(board: Board, side: int) -> float:
    """Sum of point values of a side's remaining pieces (plus Han's bonus)."""
    total = 0.0
    for r in range(ROWS):
        for c in range(COLS):
            p = board.grid[r][c]
            if p is not None and p[1] == side:
                total += SCORE_POINTS[p[0]]
    if side == HAN:
        total += HAN_BONUS
    return total


def judge(board: Board) -> dict:
    """Return both sides' scores and the winner by points.

    Result dict: {"cho": float, "han": float, "winner": "cho"|"han"|"draw",
                  "margin": float}
    Note: ties are essentially impossible because of Han's 1.5 bonus, but the
    draw branch is kept for completeness.
    """
    cho = side_score(board, CHO)
    han = side_score(board, HAN)
    if cho > han:
        winner = "cho"
    elif han > cho:
        winner = "han"
    else:
        winner = "draw"
    return {"cho": cho, "han": han, "winner": winner, "margin": abs(cho - han)}
