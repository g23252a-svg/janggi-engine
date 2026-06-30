# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Cython port of Board.fast_is_attacked — the search hot path (~60% of time).

Grid is passed in as a flat C int array (10*9=90), encoding each square as
piece*2 + side_bit, or 0 for empty. Logic mirrors board.py.fast_is_attacked
byte-for-byte; differentially verified against the Python version.
"""

# Piece codes (must match the encoder in board.py wrapper)
#   0 empty, 1 C, 2 P, 3 M, 4 S, 5 J, 6 K, 7 G
# Side stored separately in side[] array: 0 none, 1 HAN(+1), 2 CHO(-1)

cdef int ROWS = 10
cdef int COLS = 9

# palace diagonal points (r*9+c) — HAN palace (rows0-2) + CHO palace (rows7-9)
cdef int _is_pdiag(int r, int c) nogil:
    # centers and corners of each palace where diagonals run
    # HAN: (0,3)(0,5)(2,3)(2,5)(1,4) ; CHO: (7,3)(7,5)(9,3)(9,5)(8,4)
    if c == 4 and (r == 1 or r == 8):
        return 1
    if (c == 3 or c == 5) and (r == 0 or r == 2 or r == 7 or r == 9):
        return 1
    return 0

cdef int _in_palace(int r, int c, int side) nogil:
    # side: 1=HAN(rows0-2), 2=CHO(rows7-9)
    if c < 3 or c > 5:
        return 0
    if side == 1:
        return 1 if (0 <= r <= 2) else 0
    else:
        return 1 if (7 <= r <= 9) else 0

cdef int _same_palace_half(int r, int nr) nogil:
    return 1 if ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7)) else 0


def fast_is_attacked_c(int[::1] piece, int[::1] side, int r, int c, int by_side):
    """by_side: 1=HAN, 2=CHO (encoded). piece/side are length-90 flat arrays."""
    cdef int dr, dc, nr, nc, idx, tidx
    cdef int sr, sc, lr, lc, l1r, l1c, l2r, l2c, ddr, ddc, dr_, dc_
    cdef int sfwd
    cdef int target_is_cannon

    # --- Chariot orthogonal rays ---
    for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
        nr = r + dr; nc = c + dc
        while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
            nr += dr; nc += dc
        if 0 <= nr < ROWS and 0 <= nc < COLS:
            idx = nr*COLS+nc
            if side[idx] == by_side and piece[idx] == 1:
                return True

    # --- Chariot palace-diagonal slide ---
    if _is_pdiag(r, c):
        for dr, dc in ((1,1),(1,-1),(-1,1),(-1,-1)):
            nr = r + dr; nc = c + dc
            while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                   and _same_palace_half(r,nr) and piece[nr*COLS+nc] == 0):
                nr += dr; nc += dc
            if (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                    and _same_palace_half(r,nr)):
                idx = nr*COLS+nc
                if side[idx] == by_side and piece[idx] == 1:
                    return True

    tidx = r*COLS+c
    target_is_cannon = 1 if piece[tidx] == 2 else 0
    if target_is_cannon == 0:
        # --- Cannon orthogonal (one screen then enemy cannon) ---
        for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
            nr = r + dr; nc = c + dc
            while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
                nr += dr; nc += dc
            if not (0 <= nr < ROWS and 0 <= nc < COLS):
                continue
            if piece[nr*COLS+nc] == 2:
                continue  # screen can't be a cannon
            nr += dr; nc += dc
            while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
                nr += dr; nc += dc
            if 0 <= nr < ROWS and 0 <= nc < COLS:
                idx = nr*COLS+nc
                if side[idx] == by_side and piece[idx] == 2:
                    return True

        # --- Cannon palace-diagonal jump ---
        if _is_pdiag(r, c):
            for dr, dc in ((1,1),(1,-1),(-1,1),(-1,-1)):
                nr = r + dr; nc = c + dc
                while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                       and _same_palace_half(r,nr) and piece[nr*COLS+nc] == 0):
                    nr += dr; nc += dc
                if not (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                        and _same_palace_half(r,nr)):
                    continue
                if piece[nr*COLS+nc] == 2:
                    continue
                nr += dr; nc += dc
                while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                       and _same_palace_half(r,nr) and piece[nr*COLS+nc] == 0):
                    nr += dr; nc += dc
                if (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                        and _same_palace_half(r,nr)):
                    idx = nr*COLS+nc
                    if side[idx] == by_side and piece[idx] == 2:
                        return True

    # --- Horse ---
    cdef int horse[8][4]
    horse[:] = [
        [r-2,c-1,r-1,c-1],[r-2,c+1,r-1,c+1],
        [r+2,c-1,r+1,c-1],[r+2,c+1,r+1,c+1],
        [r-1,c-2,r-1,c-1],[r+1,c-2,r+1,c-1],
        [r-1,c+2,r-1,c+1],[r+1,c+2,r+1,c+1],
    ]
    cdef int i
    for i in range(8):
        sr = horse[i][0]; sc = horse[i][1]; lr = horse[i][2]; lc = horse[i][3]
        if 0 <= sr < ROWS and 0 <= sc < COLS:
            idx = sr*COLS+sc
            if piece[idx] == 3 and side[idx] == by_side:
                if piece[lr*COLS+lc] == 0:
                    return True

    # --- Elephant ---
    cdef int ele[8][4]
    ele[:] = [
        [r-3,c-2,1,0],[r-3,c+2,1,0],
        [r+3,c-2,-1,0],[r+3,c+2,-1,0],
        [r-2,c-3,0,1],[r+2,c-3,0,1],
        [r-2,c+3,0,-1],[r+2,c+3,0,-1],
    ]
    for i in range(8):
        sr = ele[i][0]; sc = ele[i][1]; dr_ = ele[i][2]; dc_ = ele[i][3]
        if not (0 <= sr < ROWS and 0 <= sc < COLS):
            continue
        idx = sr*COLS+sc
        if piece[idx] != 4 or side[idx] != by_side:
            continue
        l1r = sr + dr_; l1c = sc + dc_
        if dr_ != 0:
            ddc = 1 if c > sc else -1
            l2r = l1r + dr_; l2c = l1c + ddc
        else:
            ddr = 1 if r > sr else -1
            l2r = l1r + ddr; l2c = l1c + dc_
        if piece[l1r*COLS+l1c] == 0 and piece[l2r*COLS+l2c] == 0:
            return True

    # --- Soldier ---
    sfwd = 1 if by_side == 1 else -1   # HAN=1 forward +1
    cdef int sol[3][2]
    sol[0][0]=r-sfwd; sol[0][1]=c
    sol[1][0]=r;      sol[1][1]=c-1
    sol[2][0]=r;      sol[2][1]=c+1
    for i in range(3):
        sr = sol[i][0]; sc = sol[i][1]
        if 0 <= sr < ROWS and 0 <= sc < COLS:
            idx = sr*COLS+sc
            if piece[idx] == 5 and side[idx] == by_side:
                return True
    if _is_pdiag(r, c):
        for sr, sc in ((r-sfwd,c-1),(r-sfwd,c+1)):
            if 0 <= sr < ROWS and 0 <= sc < COLS and _is_pdiag(sr,sc):
                idx = sr*COLS+sc
                if piece[idx] == 5 and side[idx] == by_side:
                    return True

    # --- General / Guard ---
    if _in_palace(r, c, by_side):
        for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
            sr = r + dr; sc = c + dc
            if _in_palace(sr, sc, by_side):
                idx = sr*COLS+sc
                if side[idx] == by_side and (piece[idx] == 6 or piece[idx] == 7):
                    return True
        if _is_pdiag(r, c):
            for dr, dc in ((1,1),(1,-1),(-1,1),(-1,-1)):
                sr = r + dr; sc = c + dc
                if _in_palace(sr,sc,by_side) and _is_pdiag(sr,sc):
                    idx = sr*COLS+sc
                    if side[idx] == by_side and (piece[idx] == 6 or piece[idx] == 7):
                        return True

    return False
