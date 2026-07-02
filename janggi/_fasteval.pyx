# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Cython port of evaluate.py on flat int arrays.

Encodings match _attack/_movegen: piece 0..7 (0 empty,1C,2P,3M,4S,5J,6K,7G),
side 0/1/2 (none/HAN/CHO). Attack logic is an inlined cdef copy of
_attack.pyx (verified byte-for-byte vs Python); mobility is passed in by the
wrapper (counted from _movegen raw output, no Move objects).
Mirrors evaluate.py exactly; differentially verified.
"""

cdef int ROWS = 10
cdef int COLS = 9

cdef inline int _is_pdiag(int r, int c):
    if c == 4 and (r == 1 or r == 8):
        return 1
    if (c == 3 or c == 5) and (r == 0 or r == 2 or r == 7 or r == 9):
        return 1
    return 0

cdef inline int _in_palace(int r, int c, int s):
    if c < 3 or c > 5:
        return 0
    if s == 1:
        return 1 if (0 <= r <= 2) else 0
    return 1 if (7 <= r <= 9) else 0

cdef inline int _same_half(int r, int nr):
    return 1 if ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7)) else 0

cdef int _attacked(int[::1] piece, int[::1] side, int r, int c, int by_side):
    """cdef copy of _attack.fast_is_attacked_c (same logic, int return)."""
    cdef int dr, dc, nr, nc, idx, d, i
    cdef int sr, sc, lr, lc, l1r, l1c, l2r, l2c, ddr, ddc, dr_, dc_, sfwd
    cdef int orth[4][2]
    orth[0][0]=1; orth[0][1]=0; orth[1][0]=-1; orth[1][1]=0
    orth[2][0]=0; orth[2][1]=1; orth[3][0]=0; orth[3][1]=-1
    cdef int diag[4][2]
    diag[0][0]=1; diag[0][1]=1; diag[1][0]=1; diag[1][1]=-1
    diag[2][0]=-1; diag[2][1]=1; diag[3][0]=-1; diag[3][1]=-1

    for d in range(4):
        dr = orth[d][0]; dc = orth[d][1]
        nr = r + dr; nc = c + dc
        while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
            nr += dr; nc += dc
        if 0 <= nr < ROWS and 0 <= nc < COLS:
            idx = nr*COLS+nc
            if side[idx] == by_side and piece[idx] == 1:
                return 1

    if _is_pdiag(r, c):
        for d in range(4):
            dr = diag[d][0]; dc = diag[d][1]
            nr = r + dr; nc = c + dc
            while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                   and _same_half(r,nr) and piece[nr*COLS+nc] == 0):
                nr += dr; nc += dc
            if (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                    and _same_half(r,nr)):
                idx = nr*COLS+nc
                if side[idx] == by_side and piece[idx] == 1:
                    return 1

    if piece[r*COLS+c] != 2:
        for d in range(4):
            dr = orth[d][0]; dc = orth[d][1]
            nr = r + dr; nc = c + dc
            while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
                nr += dr; nc += dc
            if not (0 <= nr < ROWS and 0 <= nc < COLS):
                continue
            if piece[nr*COLS+nc] == 2:
                continue
            nr += dr; nc += dc
            while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
                nr += dr; nc += dc
            if 0 <= nr < ROWS and 0 <= nc < COLS:
                idx = nr*COLS+nc
                if side[idx] == by_side and piece[idx] == 2:
                    return 1

        if _is_pdiag(r, c):
            for d in range(4):
                dr = diag[d][0]; dc = diag[d][1]
                nr = r + dr; nc = c + dc
                jumped = 0
                while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                       and _same_half(r,nr) and piece[nr*COLS+nc] == 0):
                    nr += dr; nc += dc
                if not (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                        and _same_half(r,nr)):
                    continue
                if piece[nr*COLS+nc] == 2:
                    continue
                nr += dr; nc += dc
                while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                       and _same_half(r,nr) and piece[nr*COLS+nc] == 0):
                    nr += dr; nc += dc
                if (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                        and _same_half(r,nr)):
                    idx = nr*COLS+nc
                    if side[idx] == by_side and piece[idx] == 2:
                        return 1

    cdef int horse[8][4]
    horse[0][0]=r-2; horse[0][1]=c-1; horse[0][2]=r-1; horse[0][3]=c-1
    horse[1][0]=r-2; horse[1][1]=c+1; horse[1][2]=r-1; horse[1][3]=c+1
    horse[2][0]=r+2; horse[2][1]=c-1; horse[2][2]=r+1; horse[2][3]=c-1
    horse[3][0]=r+2; horse[3][1]=c+1; horse[3][2]=r+1; horse[3][3]=c+1
    horse[4][0]=r-1; horse[4][1]=c-2; horse[4][2]=r-1; horse[4][3]=c-1
    horse[5][0]=r+1; horse[5][1]=c-2; horse[5][2]=r+1; horse[5][3]=c-1
    horse[6][0]=r-1; horse[6][1]=c+2; horse[6][2]=r-1; horse[6][3]=c+1
    horse[7][0]=r+1; horse[7][1]=c+2; horse[7][2]=r+1; horse[7][3]=c+1
    for i in range(8):
        sr=horse[i][0]; sc=horse[i][1]; lr=horse[i][2]; lc=horse[i][3]
        if 0 <= sr < ROWS and 0 <= sc < COLS:
            idx = sr*COLS+sc
            if piece[idx] == 3 and side[idx] == by_side:
                if piece[lr*COLS+lc] == 0:
                    return 1

    cdef int ele[8][4]
    ele[0][0]=r-3; ele[0][1]=c-2; ele[0][2]=1;  ele[0][3]=0
    ele[1][0]=r-3; ele[1][1]=c+2; ele[1][2]=1;  ele[1][3]=0
    ele[2][0]=r+3; ele[2][1]=c-2; ele[2][2]=-1; ele[2][3]=0
    ele[3][0]=r+3; ele[3][1]=c+2; ele[3][2]=-1; ele[3][3]=0
    ele[4][0]=r-2; ele[4][1]=c-3; ele[4][2]=0;  ele[4][3]=1
    ele[5][0]=r+2; ele[5][1]=c-3; ele[5][2]=0;  ele[5][3]=1
    ele[6][0]=r-2; ele[6][1]=c+3; ele[6][2]=0;  ele[6][3]=-1
    ele[7][0]=r+2; ele[7][1]=c+3; ele[7][2]=0;  ele[7][3]=-1
    for i in range(8):
        sr=ele[i][0]; sc=ele[i][1]; dr_=ele[i][2]; dc_=ele[i][3]
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
            return 1

    sfwd = 1 if by_side == 1 else -1
    cdef int sol[3][2]
    sol[0][0]=r-sfwd; sol[0][1]=c
    sol[1][0]=r;      sol[1][1]=c-1
    sol[2][0]=r;      sol[2][1]=c+1
    for i in range(3):
        sr=sol[i][0]; sc=sol[i][1]
        if 0 <= sr < ROWS and 0 <= sc < COLS:
            idx = sr*COLS+sc
            if piece[idx] == 5 and side[idx] == by_side:
                return 1
    if _is_pdiag(r, c):
        for i in range(2):
            sr = r - sfwd; sc = c - 1 if i == 0 else c + 1
            if 0 <= sr < ROWS and 0 <= sc < COLS and _is_pdiag(sr, sc):
                idx = sr*COLS+sc
                if piece[idx] == 5 and side[idx] == by_side:
                    return 1

    if _in_palace(r, c, by_side):
        for d in range(4):
            sr = r + orth[d][0]; sc = c + orth[d][1]
            if _in_palace(sr, sc, by_side):
                idx = sr*COLS+sc
                if side[idx] == by_side and (piece[idx] == 6 or piece[idx] == 7):
                    return 1
        if _is_pdiag(r, c):
            for d in range(4):
                sr = r + diag[d][0]; sc = c + diag[d][1]
                if _in_palace(sr, sc, by_side) and _is_pdiag(sr, sc):
                    idx = sr*COLS+sc
                    if side[idx] == by_side and (piece[idx] == 6 or piece[idx] == 7):
                        return 1
    return 0


cdef int _cannon_screen(int[::1] piece, int r, int c):
    cdef int d, dr, dc, nr, nc, t
    cdef int orth[4][2]
    orth[0][0]=1; orth[0][1]=0; orth[1][0]=-1; orth[1][1]=0
    orth[2][0]=0; orth[2][1]=1; orth[3][0]=0; orth[3][1]=-1
    for d in range(4):
        dr = orth[d][0]; dc = orth[d][1]
        nr = r + dr; nc = c + dc
        while 0 <= nr < ROWS and 0 <= nc < COLS:
            t = piece[nr*COLS+nc]
            if t != 0:
                return 1 if t != 2 else 0
            nr += dr; nc += dc
    return 0


def evaluate_c(int[::1] piece, int[::1] side, int ply, int mob):
    cdef int pval[8]
    pval[0]=0; pval[1]=1300; pval[2]=700; pval[3]=500
    pval[4]=300; pval[5]=200; pval[6]=10000; pval[7]=300
    cdef int undef_w[8]
    undef_w[0]=0; undef_w[1]=360; undef_w[2]=260; undef_w[3]=190
    undef_w[4]=130; undef_w[5]=25; undef_w[6]=0; undef_w[7]=90
    cdef int def_w[8]
    def_w[0]=0; def_w[1]=120; def_w[2]=90; def_w[3]=65
    def_w[4]=40; def_w[5]=0; def_w[6]=0; def_w[7]=25

    cdef int score = 0, material = 0
    cdef int r, c, idx, pc, s, base, v, adv
    cdef int ghr = -1, ghc = -1, gcr = -1, gcc = -1

    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS+c
            pc = piece[idx]
            if pc == 0:
                continue
            s = side[idx]
            base = pval[pc]
            if s == 1: material += base
            else:      material -= base
            v = base
            if pc == 5:
                adv = r if s == 1 else (ROWS - 1 - r)
                v += adv * 8
                if 3 <= c <= 5:
                    v += 10
            elif pc == 1 or pc == 2 or pc == 3:
                v += (4 - (c-4 if c >= 4 else 4-c)) * 3
                if pc == 2 and _cannon_screen(piece, r, c):
                    v += 15
            if pc == 6:
                if s == 1: ghr = r; ghc = c
                else:      gcr = r; gcc = c
            if s == 1: score += v
            else:      score -= v

    if ply >= 120:
        score += material * 2
    elif ply >= 80:
        score += material

    score += mob * 2

    # general exposure
    cdef int d, nr, nc, expo
    cdef int orth[4][2]
    orth[0][0]=1; orth[0][1]=0; orth[1][0]=-1; orth[1][1]=0
    orth[2][0]=0; orth[2][1]=1; orth[3][0]=0; orth[3][1]=-1
    if ghr >= 0:
        expo = 0
        for d in range(4):
            nr = ghr + orth[d][0]; nc = ghc + orth[d][1]
            if _in_palace(nr, nc, 1) and piece[nr*COLS+nc] == 0:
                expo += 1
        score -= expo * 12
    if gcr >= 0:
        expo = 0
        for d in range(4):
            nr = gcr + orth[d][0]; nc = gcc + orth[d][1]
            if _in_palace(nr, nc, 2) and piece[nr*COLS+nc] == 0:
                expo += 1
        score += expo * 12

    # guards alive (codes 4 S, 7 G)
    cdef int gh = 0, gc2 = 0
    for idx in range(90):
        pc = piece[idx]
        if pc == 4 or pc == 7:
            if side[idx] == 1: gh += 1
            elif side[idx] == 2: gc2 += 1
    score += gh * 6
    score -= gc2 * 6

    # king attack pressure
    cdef int danger, dr2, dc2
    cdef int dirs8[8][2]
    dirs8[0][0]=1; dirs8[0][1]=0;  dirs8[1][0]=-1; dirs8[1][1]=0
    dirs8[2][0]=0; dirs8[2][1]=1;  dirs8[3][0]=0;  dirs8[3][1]=-1
    dirs8[4][0]=1; dirs8[4][1]=1;  dirs8[5][0]=1;  dirs8[5][1]=-1
    dirs8[6][0]=-1; dirs8[6][1]=1; dirs8[7][0]=-1; dirs8[7][1]=-1
    if ghr >= 0:
        danger = 0
        if _attacked(piece, side, ghr, ghc, 2):
            danger += 12
        for d in range(8):
            dr2 = dirs8[d][0]; dc2 = dirs8[d][1]
            nr = ghr + dr2; nc = ghc + dc2
            if not _in_palace(nr, nc, 1):
                continue
            if dr2 != 0 and dc2 != 0:
                if not (_is_pdiag(ghr, ghc) and _is_pdiag(nr, nc)):
                    continue
            idx = nr*COLS+nc
            if piece[idx] != 0 and side[idx] == 1:
                continue
            if _attacked(piece, side, nr, nc, 2):
                danger += 3
        score -= danger * 18
    if gcr >= 0:
        danger = 0
        if _attacked(piece, side, gcr, gcc, 1):
            danger += 12
        for d in range(8):
            dr2 = dirs8[d][0]; dc2 = dirs8[d][1]
            nr = gcr + dr2; nc = gcc + dc2
            if not _in_palace(nr, nc, 2):
                continue
            if dr2 != 0 and dc2 != 0:
                if not (_is_pdiag(gcr, gcc) and _is_pdiag(nr, nc)):
                    continue
            idx = nr*COLS+nc
            if piece[idx] != 0 and side[idx] == 2:
                continue
            if _attacked(piece, side, nr, nc, 1):
                danger += 3
        score += danger * 18

    # loose piece risk
    cdef int risk_h = 0, risk_c = 0
    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS+c
            pc = piece[idx]
            if pc == 0 or pc == 6:
                continue
            s = side[idx]
            if s == 1:
                if _attacked(piece, side, r, c, 2):
                    if _attacked(piece, side, r, c, 1):
                        risk_h += def_w[pc]
                    else:
                        risk_h += undef_w[pc]
            else:
                if _attacked(piece, side, r, c, 1):
                    if _attacked(piece, side, r, c, 2):
                        risk_c += def_w[pc]
                    else:
                        risk_c += undef_w[pc]
    score -= risk_h
    score += risk_c

    return score
