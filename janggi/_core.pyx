# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Self-contained Cython search core: negamax subtree on flat int arrays.

Python keeps the root (iterative deepening, ordering carry-over, forbidden
moves, capture credit, mate/blunder guards, time budget). This module runs the
subtree below each root move with zero Python objects in the hot path.

Encodings match _attack/_movegen/_fasteval:
  piece: 0 empty, 1 C, 2 P, 3 M, 4 S, 5 J, 6 K, 7 G
  side : 0 none, 1 HAN, 2 CHO   (enemy of s is 3-s)

All piece logic is transliterated from the verified Python sources
(board.py / evaluate.py / see.py / search.py) and differentially verified:
perft equality, fixed-depth score equality, engine A/B.
"""

import time as _pytime

cdef int ROWS = 10
cdef int COLS = 9
cdef int MATE = 1000000

# ------------------------------------------------------------ small helpers
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

cdef int PVAL[8]
PVAL[0]=0; PVAL[1]=1300; PVAL[2]=700; PVAL[3]=500
PVAL[4]=300; PVAL[5]=200; PVAL[6]=10000; PVAL[7]=300

cdef int UNDEF_W[8]
UNDEF_W[0]=0; UNDEF_W[1]=360; UNDEF_W[2]=260; UNDEF_W[3]=190
UNDEF_W[4]=130; UNDEF_W[5]=25; UNDEF_W[6]=0; UNDEF_W[7]=90
cdef int DEF_W[8]
DEF_W[0]=0; DEF_W[1]=120; DEF_W[2]=90; DEF_W[3]=65
DEF_W[4]=40; DEF_W[5]=0; DEF_W[6]=0; DEF_W[7]=25

# --------------------------------------------------------------- attack test
cdef int _attacked(int* piece, int* side, int r, int c, int by_side):
    cdef int dr, dc, nr, nc, idx, d, i, jumped
    cdef int sr, sc, lr, lc, l1r, l1c, l2r, l2c, ddr, ddc, dr_, dc_, sfwd
    cdef int ORTH[4][2]
    ORTH[0][0]=1; ORTH[0][1]=0; ORTH[1][0]=-1; ORTH[1][1]=0
    ORTH[2][0]=0; ORTH[2][1]=1; ORTH[3][0]=0; ORTH[3][1]=-1
    cdef int DIAG[4][2]
    DIAG[0][0]=1; DIAG[0][1]=1; DIAG[1][0]=1; DIAG[1][1]=-1
    DIAG[2][0]=-1; DIAG[2][1]=1; DIAG[3][0]=-1; DIAG[3][1]=-1

    for d in range(4):
        dr = ORTH[d][0]; dc = ORTH[d][1]
        nr = r + dr; nc = c + dc
        while 0 <= nr < ROWS and 0 <= nc < COLS and piece[nr*COLS+nc] == 0:
            nr += dr; nc += dc
        if 0 <= nr < ROWS and 0 <= nc < COLS:
            idx = nr*COLS+nc
            if side[idx] == by_side and piece[idx] == 1:
                return 1

    if _is_pdiag(r, c):
        for d in range(4):
            dr = DIAG[d][0]; dc = DIAG[d][1]
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
            dr = ORTH[d][0]; dc = ORTH[d][1]
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
                dr = DIAG[d][0]; dc = DIAG[d][1]
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

    cdef int HS[8][4]
    HS[0][0]=r-2; HS[0][1]=c-1; HS[0][2]=r-1; HS[0][3]=c-1
    HS[1][0]=r-2; HS[1][1]=c+1; HS[1][2]=r-1; HS[1][3]=c+1
    HS[2][0]=r+2; HS[2][1]=c-1; HS[2][2]=r+1; HS[2][3]=c-1
    HS[3][0]=r+2; HS[3][1]=c+1; HS[3][2]=r+1; HS[3][3]=c+1
    HS[4][0]=r-1; HS[4][1]=c-2; HS[4][2]=r-1; HS[4][3]=c-1
    HS[5][0]=r+1; HS[5][1]=c-2; HS[5][2]=r+1; HS[5][3]=c-1
    HS[6][0]=r-1; HS[6][1]=c+2; HS[6][2]=r-1; HS[6][3]=c+1
    HS[7][0]=r+1; HS[7][1]=c+2; HS[7][2]=r+1; HS[7][3]=c+1
    for i in range(8):
        sr=HS[i][0]; sc=HS[i][1]; lr=HS[i][2]; lc=HS[i][3]
        if 0 <= sr < ROWS and 0 <= sc < COLS:
            idx = sr*COLS+sc
            if piece[idx] == 3 and side[idx] == by_side:
                if piece[lr*COLS+lc] == 0:
                    return 1

    cdef int ES[8][4]
    ES[0][0]=r-3; ES[0][1]=c-2; ES[0][2]=1;  ES[0][3]=0
    ES[1][0]=r-3; ES[1][1]=c+2; ES[1][2]=1;  ES[1][3]=0
    ES[2][0]=r+3; ES[2][1]=c-2; ES[2][2]=-1; ES[2][3]=0
    ES[3][0]=r+3; ES[3][1]=c+2; ES[3][2]=-1; ES[3][3]=0
    ES[4][0]=r-2; ES[4][1]=c-3; ES[4][2]=0;  ES[4][3]=1
    ES[5][0]=r+2; ES[5][1]=c-3; ES[5][2]=0;  ES[5][3]=1
    ES[6][0]=r-2; ES[6][1]=c+3; ES[6][2]=0;  ES[6][3]=-1
    ES[7][0]=r+2; ES[7][1]=c+3; ES[7][2]=0;  ES[7][3]=-1
    for i in range(8):
        sr=ES[i][0]; sc=ES[i][1]; dr_=ES[i][2]; dc_=ES[i][3]
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
    cdef int SO[3][2]
    SO[0][0]=r-sfwd; SO[0][1]=c
    SO[1][0]=r;      SO[1][1]=c-1
    SO[2][0]=r;      SO[2][1]=c+1
    for i in range(3):
        sr=SO[i][0]; sc=SO[i][1]
        if 0 <= sr < ROWS and 0 <= sc < COLS:
            idx = sr*COLS+sc
            if piece[idx] == 5 and side[idx] == by_side:
                return 1
    if _is_pdiag(r, c):
        for i in range(2):
            sr = r - sfwd
            sc = c - 1 if i == 0 else c + 1
            if 0 <= sr < ROWS and 0 <= sc < COLS and _is_pdiag(sr, sc):
                idx = sr*COLS+sc
                if piece[idx] == 5 and side[idx] == by_side:
                    return 1

    if _in_palace(r, c, by_side):
        for d in range(4):
            sr = r + ORTH[d][0]; sc = c + ORTH[d][1]
            if _in_palace(sr, sc, by_side):
                idx = sr*COLS+sc
                if side[idx] == by_side and (piece[idx] == 6 or piece[idx] == 7):
                    return 1
        if _is_pdiag(r, c):
            for d in range(4):
                sr = r + DIAG[d][0]; sc = c + DIAG[d][1]
                if _in_palace(sr, sc, by_side) and _is_pdiag(sr, sc):
                    idx = sr*COLS+sc
                    if side[idx] == by_side and (piece[idx] == 6 or piece[idx] == 7):
                        return 1
    return 0

# --------------------------------------------------------- general / check
cdef int _find_gen(int* piece, int* side, int who, int* gr, int* gc):
    cdef int idx
    for idx in range(90):
        if piece[idx] == 6 and side[idx] == who:
            gr[0] = idx // COLS
            gc[0] = idx % COLS
            return 1
    return 0

cdef int _in_check(int* piece, int* side, int who):
    cdef int gr, gc
    if not _find_gen(piece, side, who, &gr, &gc):
        return 1
    return _attacked(piece, side, gr, gc, 3 - who)

# ------------------------------------------------------------- move gen
# moves packed as 5 ints each: fr, fc, tr, tc, cap_code
cdef int _gen_pseudo(int* piece, int* side, int who, int* out):
    cdef int n = 0
    cdef int r, c, idx, pc, nr, nc, t, d, i, jumped, fwd
    cdef int sr, sc, b1r, b1c, b2r, b2c, dr, dc
    cdef int ORTH[4][2]
    ORTH[0][0]=1; ORTH[0][1]=0; ORTH[1][0]=-1; ORTH[1][1]=0
    ORTH[2][0]=0; ORTH[2][1]=1; ORTH[3][0]=0; ORTH[3][1]=-1
    cdef int DIAG[4][2]
    DIAG[0][0]=1; DIAG[0][1]=1; DIAG[1][0]=1; DIAG[1][1]=-1
    DIAG[2][0]=-1; DIAG[2][1]=1; DIAG[3][0]=-1; DIAG[3][1]=-1
    cdef int HL[8][4]
    HL[0][0]=-1; HL[0][1]=0; HL[0][2]=-2; HL[0][3]=-1
    HL[1][0]=-1; HL[1][1]=0; HL[1][2]=-2; HL[1][3]=1
    HL[2][0]=1;  HL[2][1]=0; HL[2][2]=2;  HL[2][3]=-1
    HL[3][0]=1;  HL[3][1]=0; HL[3][2]=2;  HL[3][3]=1
    HL[4][0]=0;  HL[4][1]=-1; HL[4][2]=-1; HL[4][3]=-2
    HL[5][0]=0;  HL[5][1]=-1; HL[5][2]=1;  HL[5][3]=-2
    HL[6][0]=0;  HL[6][1]=1;  HL[6][2]=-1; HL[6][3]=2
    HL[7][0]=0;  HL[7][1]=1;  HL[7][2]=1;  HL[7][3]=2
    cdef int EL[8][6]
    EL[0][0]=-1; EL[0][1]=0;  EL[0][2]=-2; EL[0][3]=-1; EL[0][4]=-3; EL[0][5]=-2
    EL[1][0]=-1; EL[1][1]=0;  EL[1][2]=-2; EL[1][3]=1;  EL[1][4]=-3; EL[1][5]=2
    EL[2][0]=1;  EL[2][1]=0;  EL[2][2]=2;  EL[2][3]=-1; EL[2][4]=3;  EL[2][5]=-2
    EL[3][0]=1;  EL[3][1]=0;  EL[3][2]=2;  EL[3][3]=1;  EL[3][4]=3;  EL[3][5]=2
    EL[4][0]=0;  EL[4][1]=-1; EL[4][2]=-1; EL[4][3]=-2; EL[4][4]=-2; EL[4][5]=-3
    EL[5][0]=0;  EL[5][1]=-1; EL[5][2]=1;  EL[5][3]=-2; EL[5][4]=2;  EL[5][5]=-3
    EL[6][0]=0;  EL[6][1]=1;  EL[6][2]=-1; EL[6][3]=2;  EL[6][4]=-2; EL[6][5]=3
    EL[7][0]=0;  EL[7][1]=1;  EL[7][2]=1;  EL[7][3]=2;  EL[7][4]=2;  EL[7][5]=3

    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS + c
            pc = piece[idx]
            if pc == 0 or side[idx] != who:
                continue

            if pc == 1:  # chariot
                for d in range(4):
                    dr = ORTH[d][0]; dc = ORTH[d][1]
                    nr = r+dr; nc = c+dc
                    while 0 <= nr < ROWS and 0 <= nc < COLS:
                        t = piece[nr*COLS+nc]
                        if t == 0:
                            out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                        else:
                            if side[nr*COLS+nc] != who:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                            break
                        nr += dr; nc += dc
                if _is_pdiag(r, c):
                    for d in range(4):
                        dr = DIAG[d][0]; dc = DIAG[d][1]
                        nr = r+dr; nc = c+dc
                        while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                               and _same_half(r,nr)):
                            t = piece[nr*COLS+nc]
                            if t == 0:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                            else:
                                if side[nr*COLS+nc] != who:
                                    out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                                break
                            nr += dr; nc += dc

            elif pc == 2:  # cannon
                for d in range(4):
                    dr = ORTH[d][0]; dc = ORTH[d][1]
                    nr = r+dr; nc = c+dc
                    jumped = 0
                    while 0 <= nr < ROWS and 0 <= nc < COLS:
                        t = piece[nr*COLS+nc]
                        if jumped == 0:
                            if t != 0:
                                if t == 2:
                                    break
                                jumped = 1
                        else:
                            if t == 0:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                            else:
                                if t != 2 and side[nr*COLS+nc] != who:
                                    out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                                break
                        nr += dr; nc += dc
                if _is_pdiag(r, c):
                    for d in range(4):
                        dr = DIAG[d][0]; dc = DIAG[d][1]
                        nr = r+dr; nc = c+dc
                        jumped = 0
                        while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                               and _same_half(r,nr)):
                            t = piece[nr*COLS+nc]
                            if jumped == 0:
                                if t != 0:
                                    if t == 2:
                                        break
                                    jumped = 1
                            else:
                                if t == 0:
                                    out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                                else:
                                    if t != 2 and side[nr*COLS+nc] != who:
                                        out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                                    break
                            nr += dr; nc += dc

            elif pc == 3:  # horse
                for i in range(8):
                    sr = r + HL[i][0]; sc = c + HL[i][1]
                    if 0 <= sr < ROWS and 0 <= sc < COLS and piece[sr*COLS+sc] == 0:
                        nr = r + HL[i][2]; nc = c + HL[i][3]
                        if 0 <= nr < ROWS and 0 <= nc < COLS:
                            t = piece[nr*COLS+nc]
                            if t == 0:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                            elif side[nr*COLS+nc] != who:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1

            elif pc == 4:  # elephant
                for i in range(8):
                    b1r = r + EL[i][0]; b1c = c + EL[i][1]
                    b2r = r + EL[i][2]; b2c = c + EL[i][3]
                    if (0 <= b1r < ROWS and 0 <= b1c < COLS and piece[b1r*COLS+b1c] == 0
                            and 0 <= b2r < ROWS and 0 <= b2c < COLS and piece[b2r*COLS+b2c] == 0):
                        nr = r + EL[i][4]; nc = c + EL[i][5]
                        if 0 <= nr < ROWS and 0 <= nc < COLS:
                            t = piece[nr*COLS+nc]
                            if t == 0:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                            elif side[nr*COLS+nc] != who:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1

            elif pc == 6 or pc == 7:  # king / guard
                for d in range(4):
                    nr = r + ORTH[d][0]; nc = c + ORTH[d][1]
                    if _in_palace(nr, nc, who):
                        t = piece[nr*COLS+nc]
                        if t == 0 or side[nr*COLS+nc] != who:
                            out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                if _is_pdiag(r, c):
                    for d in range(4):
                        nr = r + DIAG[d][0]; nc = c + DIAG[d][1]
                        if _in_palace(nr, nc, who) and _is_pdiag(nr, nc):
                            t = piece[nr*COLS+nc]
                            if t == 0 or side[nr*COLS+nc] != who:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1

            elif pc == 5:  # soldier
                fwd = 1 if who == 1 else -1
                nr = r + fwd; nc = c
                if 0 <= nr < ROWS:
                    t = piece[nr*COLS+nc]
                    if t == 0:
                        out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                    elif side[nr*COLS+nc] != who:
                        out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                for i in range(2):
                    nr = r; nc = c - 1 if i == 0 else c + 1
                    if 0 <= nc < COLS:
                        t = piece[nr*COLS+nc]
                        if t == 0:
                            out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                        elif side[nr*COLS+nc] != who:
                            out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
                if _is_pdiag(r, c):
                    for i in range(2):
                        nr = r + fwd; nc = c - 1 if i == 0 else c + 1
                        if 0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr, nc):
                            t = piece[nr*COLS+nc]
                            if t == 0:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=0; n+=1
                            elif side[nr*COLS+nc] != who:
                                out[n*5]=r; out[n*5+1]=c; out[n*5+2]=nr; out[n*5+3]=nc; out[n*5+4]=t; n+=1
    return n

# --------------------------------------------------- make / unmake + zobrist
DEF MAXHIST = 256
cdef int h_fr[MAXHIST]
cdef int h_fc[MAXHIST]
cdef int h_tr[MAXHIST]
cdef int h_tc[MAXHIST]
cdef int h_cap[MAXHIST]
cdef int h_capsd[MAXHIST]
cdef int h_top = 0

cdef unsigned long long ZTAB[90 * 8 * 3]
cdef unsigned long long Z_SIDE
cdef unsigned long long cur_hash = 0

cdef unsigned long long _splitmix(unsigned long long* st):
    st[0] += <unsigned long long>0x9E3779B97F4A7C15
    cdef unsigned long long z = st[0]
    z = (z ^ (z >> 30)) * <unsigned long long>0xBF58476D1CE4E5B9
    z = (z ^ (z >> 27)) * <unsigned long long>0x94D049BB133111EB
    return z ^ (z >> 31)

cdef void _init_zobrist():
    cdef unsigned long long st = <unsigned long long>20260702
    cdef int i
    for i in range(90 * 8 * 3):
        ZTAB[i] = _splitmix(&st)
    global Z_SIDE
    Z_SIDE = _splitmix(&st)

_init_zobrist()

cdef inline unsigned long long _zp(int sq, int pc, int sd):
    return ZTAB[(sq * 8 + pc) * 3 + sd]

cdef void _full_hash(int* piece, int* side, int who):
    global cur_hash
    cur_hash = 0
    cdef int i
    for i in range(90):
        if piece[i] != 0:
            cur_hash ^= _zp(i, piece[i], side[i])
    if who == 1:
        cur_hash ^= Z_SIDE

cdef void _make(int* piece, int* side, int fr, int fc, int tr, int tc):
    global h_top, cur_hash
    cdef int fi = fr*COLS+fc
    cdef int ti = tr*COLS+tc
    h_fr[h_top] = fr; h_fc[h_top] = fc
    h_tr[h_top] = tr; h_tc[h_top] = tc
    h_cap[h_top] = piece[ti]; h_capsd[h_top] = side[ti]
    h_top += 1
    if piece[ti] != 0:
        cur_hash ^= _zp(ti, piece[ti], side[ti])
    cur_hash ^= _zp(fi, piece[fi], side[fi])
    piece[ti] = piece[fi]; side[ti] = side[fi]
    cur_hash ^= _zp(ti, piece[ti], side[ti])
    piece[fi] = 0; side[fi] = 0
    cur_hash ^= Z_SIDE

cdef void _unmake(int* piece, int* side):
    global h_top, cur_hash
    h_top -= 1
    cdef int fr = h_fr[h_top], fc = h_fc[h_top]
    cdef int tr = h_tr[h_top], tc = h_tc[h_top]
    cdef int fi = fr*COLS+fc
    cdef int ti = tr*COLS+tc
    cur_hash ^= Z_SIDE
    cur_hash ^= _zp(ti, piece[ti], side[ti])
    piece[fi] = piece[ti]; side[fi] = side[ti]
    cur_hash ^= _zp(fi, piece[fi], side[fi])
    piece[ti] = h_cap[h_top]; side[ti] = h_capsd[h_top]
    if piece[ti] != 0:
        cur_hash ^= _zp(ti, piece[ti], side[ti])

# ----------------------------------------------------------------- evaluate
cdef int _cannon_screen(int* piece, int r, int c):
    cdef int d, dr, dc, nr, nc, t
    cdef int ORTH[4][2]
    ORTH[0][0]=1; ORTH[0][1]=0; ORTH[1][0]=-1; ORTH[1][1]=0
    ORTH[2][0]=0; ORTH[2][1]=1; ORTH[3][0]=0; ORTH[3][1]=-1
    for d in range(4):
        dr = ORTH[d][0]; dc = ORTH[d][1]
        nr = r + dr; nc = c + dc
        while 0 <= nr < ROWS and 0 <= nc < COLS:
            t = piece[nr*COLS+nc]
            if t != 0:
                return 1 if t != 2 else 0
            nr += dr; nc += dc
    return 0

cdef int g_base_ply = 0   # python-side history length at subtree root

cdef int _evaluate(int* piece, int* side):
    """evaluate(include_mobility=False), HAN-positive. Mirrors evaluate.py."""
    cdef int score = 0, material = 0
    cdef int r, c, idx, pc, s, base, v, adv
    cdef int ghr = -1, ghc = -1, gcr = -1, gcc = -1
    cdef int d, nr, nc, expo, danger, dr2, dc2
    cdef int ORTH[4][2]
    ORTH[0][0]=1; ORTH[0][1]=0; ORTH[1][0]=-1; ORTH[1][1]=0
    ORTH[2][0]=0; ORTH[2][1]=1; ORTH[3][0]=0; ORTH[3][1]=-1
    cdef int D8[8][2]
    D8[0][0]=1; D8[0][1]=0;  D8[1][0]=-1; D8[1][1]=0
    D8[2][0]=0; D8[2][1]=1;  D8[3][0]=0;  D8[3][1]=-1
    D8[4][0]=1; D8[4][1]=1;  D8[5][0]=1;  D8[5][1]=-1
    D8[6][0]=-1; D8[6][1]=1; D8[7][0]=-1; D8[7][1]=-1

    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS+c
            pc = piece[idx]
            if pc == 0:
                continue
            s = side[idx]
            base = PVAL[pc]
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

    cdef int ply = g_base_ply + h_top
    if ply >= 120:
        score += material * 2
    elif ply >= 80:
        score += material

    if ghr >= 0:
        expo = 0
        for d in range(4):
            nr = ghr + ORTH[d][0]; nc = ghc + ORTH[d][1]
            if _in_palace(nr, nc, 1) and piece[nr*COLS+nc] == 0:
                expo += 1
        score -= expo * 12
    if gcr >= 0:
        expo = 0
        for d in range(4):
            nr = gcr + ORTH[d][0]; nc = gcc + ORTH[d][1]
            if _in_palace(nr, nc, 2) and piece[nr*COLS+nc] == 0:
                expo += 1
        score += expo * 12

    cdef int gh = 0, gc2 = 0
    for idx in range(90):
        pc = piece[idx]
        if pc == 4 or pc == 7:
            if side[idx] == 1: gh += 1
            elif side[idx] == 2: gc2 += 1
    score += gh * 6
    score -= gc2 * 6

    if ghr >= 0:
        danger = 0
        if _attacked(piece, side, ghr, ghc, 2):
            danger += 12
        for d in range(8):
            dr2 = D8[d][0]; dc2 = D8[d][1]
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
            dr2 = D8[d][0]; dc2 = D8[d][1]
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
                        risk_h += DEF_W[pc]
                    else:
                        risk_h += UNDEF_W[pc]
            else:
                if _attacked(piece, side, r, c, 1):
                    if _attacked(piece, side, r, c, 2):
                        risk_c += DEF_W[pc]
                    else:
                        risk_c += UNDEF_W[pc]
    score -= risk_h
    score += risk_c
    return score

# ------------------------------------------------------------------ SEE
cdef int SEE_BUF[1200]

cdef int _see(int* piece, int* side, int fr, int fc, int tr, int tc):
    """Mirrors see.py. Move must be a capture (piece on target)."""
    cdef int ti = tr*COLS+tc
    cdef int fi = fr*COLS+fc
    if piece[ti] == 0 or piece[fi] == 0:
        return 0
    cdef int who = side[fi]
    cdef int gain[40]
    cdef int gi = 0
    gain[0] = PVAL[piece[ti]]
    gi = 1
    cdef int on_square = PVAL[piece[fi]]
    cdef int made = 0
    _make(piece, side, fr, fc, tr, tc)
    made += 1
    cdef int att_side = 3 - who
    cdef int n, m, best_v, best_fr, best_fc, v, i
    while True:
        n = _gen_pseudo(piece, side, att_side, SEE_BUF)
        best_v = 0x7FFFFFFF; best_fr = -1; best_fc = -1
        for m in range(n):
            if SEE_BUF[m*5+2] == tr and SEE_BUF[m*5+3] == tc:
                v = PVAL[piece[SEE_BUF[m*5]*COLS + SEE_BUF[m*5+1]]]
                if v < best_v:
                    best_v = v
                    best_fr = SEE_BUF[m*5]; best_fc = SEE_BUF[m*5+1]
        if best_fr < 0:
            break
        gain[gi] = on_square - gain[gi-1]
        gi += 1
        on_square = best_v
        _make(piece, side, best_fr, best_fc, tr, tc)
        made += 1
        att_side = 3 - att_side
        if gi >= 39:
            break
    for i in range(made):
        _unmake(piece, side)
    for i in range(gi - 1, 0, -1):
        v = -gain[i-1]
        if gain[i] > v:
            v = gain[i]
        gain[i-1] = -v
    return gain[0]

# -------------------------------------------------------------- search state
DEF TT_SIZE = 2097152          # 2^21 entries
DEF TT_MASK = 2097151
cdef unsigned long long tt_key[TT_SIZE]
cdef int tt_val[TT_SIZE]
cdef short tt_depth[TT_SIZE]
cdef signed char tt_flag[TT_SIZE]   # 0 exact, 1 lower, 2 upper, -1 empty
cdef int tt_best[TT_SIZE]           # from*90+to, -1 none

DEF MAXPLY = 64
cdef int MBUF[MAXPLY * 1024]        # per-ply move buffers (204 moves max)
cdef long long MKEY[204]            # ordering keys (scratch, per node)

cdef int killer1[MAXPLY]
cdef int killer2[MAXPLY]
cdef int histh[2 * 8100]

cdef long long g_nodes = 0
cdef long long g_qnodes = 0
cdef long long g_tthits = 0
cdef int g_timeout = 0
cdef double g_deadline = 0.0
cdef int g_ext = 0
cdef int g_maxdepth = 6
cdef int g_use_lmr = 1
cdef int g_use_ext = 1
cdef int g_use_tt = 1

cdef int _time_up():
    global g_timeout
    if g_timeout:
        return 1
    if g_deadline > 0.0 and (g_nodes + g_qnodes) % 4096 == 0:
        if _pytime.time() > g_deadline:
            g_timeout = 1
            return 1
    return 0

cdef int _qsearch(int* piece, int* side, int who, int alpha, int beta, int ply):
    global g_qnodes
    g_qnodes += 1
    if _time_up():
        return 0
    cdef int stand = _evaluate(piece, side)
    if who == 2:
        stand = -stand
    # Never index beyond the per-ply move buffer on pathological checking
    # cycles. At the cap the static score is the safest bounded fallback.
    if ply >= MAXPLY - 1:
        return stand

    cdef int in_chk = _in_check(piece, side, who)
    # Stand-pat is the choice to make no move, which is illegal while checked.
    if not in_chk:
        if stand >= beta:
            return beta
        if stand > alpha:
            alpha = stand

    cdef int* buf = &MBUF[ply * 1024]
    cdef int n = _gen_pseudo(piece, side, who, buf)
    cdef int i, j, m, best, tmp
    # Outside check collect captures; in check collect every possible evasion.
    cdef int caps[204]
    cdef int ckey[204]
    cdef int nc = 0
    for m in range(n):
        if in_chk or buf[m*5+4] != 0:
            caps[nc] = m
            if buf[m*5+4] != 0:
                ckey[nc] = PVAL[buf[m*5+4]] * 10 - PVAL[piece[buf[m*5]*COLS + buf[m*5+1]]]
            else:
                ckey[nc] = -PVAL[piece[buf[m*5]*COLS + buf[m*5+1]]]
            nc += 1
    # insertion sort desc by ckey
    for i in range(1, nc):
        m = caps[i]; tmp = ckey[i]
        j = i - 1
        while j >= 0 and ckey[j] < tmp:
            caps[j+1] = caps[j]; ckey[j+1] = ckey[j]
            j -= 1
        caps[j+1] = m; ckey[j+1] = tmp

    cdef int fr, fc, tr, tc, cap, score
    cdef int legal_found = 0
    for i in range(nc):
        m = caps[i]
        fr = buf[m*5]; fc = buf[m*5+1]; tr = buf[m*5+2]; tc = buf[m*5+3]; cap = buf[m*5+4]
        # SEE pruning is safe for optional captures, never for forced evasions.
        if not in_chk and _see(piece, side, fr, fc, tr, tc) < 0:
            continue
        _make(piece, side, fr, fc, tr, tc)
        # Pseudo-legal moves that leave our own general attacked are illegal.
        if _in_check(piece, side, who):
            _unmake(piece, side)
            continue
        legal_found = 1
        if cap == 6:  # capturing the general ends it
            _unmake(piece, side)
            return MATE
        score = -_qsearch(piece, side, 3 - who, -beta, -alpha, ply + 1)
        _unmake(piece, side)
        if g_timeout:
            return 0
        if score >= beta:
            return beta
        if score > alpha:
            alpha = score
    if in_chk and not legal_found:
        return -MATE + ply
    return alpha

cdef int _negamax(int* piece, int* side, int who, int depth, int alpha, int beta, int ply):
    global g_nodes, g_tthits, g_ext
    g_nodes += 1
    if _time_up():
        return 0

    cdef int alpha_orig = alpha
    cdef unsigned long long key = cur_hash
    cdef int slot = <int>(key & TT_MASK)
    cdef int tt_move = -1
    if g_use_tt and tt_flag[slot] >= 0 and tt_key[slot] == key:
        tt_move = tt_best[slot]
        if tt_depth[slot] >= depth:
            g_tthits += 1
            if tt_flag[slot] == 0:
                return tt_val[slot]
            if tt_flag[slot] == 1 and tt_val[slot] > alpha:
                alpha = tt_val[slot]
            elif tt_flag[slot] == 2 and tt_val[slot] < beta:
                beta = tt_val[slot]
            if alpha >= beta:
                return tt_val[slot]

    if depth == 0:
        return _qsearch(piece, side, who, alpha, beta, ply)

    cdef int* buf = &MBUF[ply * 1024]
    cdef int n = _gen_pseudo(piece, side, who, buf)

    # legal filter + ordering keys in one pass
    cdef int legal[204]
    cdef int nl = 0
    cdef int m, fr, fc, tr, tc, cap
    cdef long long k
    cdef int mt, is_tt, killer, hh, see_v, mvv
    for m in range(n):
        fr = buf[m*5]; fc = buf[m*5+1]; tr = buf[m*5+2]; tc = buf[m*5+3]; cap = buf[m*5+4]
        _make(piece, side, fr, fc, tr, tc)
        if _in_check(piece, side, who):
            _unmake(piece, side)
            continue
        _unmake(piece, side)
        mt = (fr*COLS+fc)*90 + (tr*COLS+tc)
        is_tt = 1 if (tt_move >= 0 and mt == tt_move) else 0
        if cap != 0:
            see_v = _see(piece, side, fr, fc, tr, tc)
            killer = 0
            hh = 0
        else:
            see_v = 0
            killer = 1 if (mt == killer1[depth & 63] or mt == killer2[depth & 63]) else 0
            hh = histh[(who-1)*8100 + mt]
        # composite ordering key (lexicographic like python tuple, desc)
        mvv = (PVAL[cap] * 10 - PVAL[piece[fr*COLS+fc]]) if cap != 0 else (0 - PVAL[piece[fr*COLS+fc]])
        k = ((<long long>is_tt) << 60) \
            + ((<long long>(see_v + 30000)) << 42) \
            + ((<long long>killer) << 40) \
            + ((<long long>(hh if hh < 1048575 else 1048575)) << 18) \
            + <long long>(mvv + 30000)
        legal[nl] = m
        MKEY[nl] = k
        nl += 1

    if nl == 0:
        return -MATE + (g_maxdepth - depth)

    # insertion sort desc by MKEY
    cdef int i, j, ti2
    cdef long long tk
    for i in range(1, nl):
        ti2 = legal[i]; tk = MKEY[i]
        j = i - 1
        while j >= 0 and MKEY[j] < tk:
            legal[j+1] = legal[j]; MKEY[j+1] = MKEY[j]
            j -= 1
        legal[j+1] = ti2; MKEY[j+1] = tk

    cdef int in_chk = _in_check(piece, side, who)
    cdef int extend = 0
    if g_use_ext and g_ext > 0 and in_chk:
        extend = 1
        g_ext -= 1

    cdef int best_score = -MATE * 2
    cdef int best_move = -1
    cdef int score, reduce, gives_check
    for i in range(nl):
        m = legal[i]
        fr = buf[m*5]; fc = buf[m*5+1]; tr = buf[m*5+2]; tc = buf[m*5+3]; cap = buf[m*5+4]
        _make(piece, side, fr, fc, tr, tc)
        reduce = 0
        if (g_use_lmr and extend == 0 and depth >= 3 and i >= 3 and cap == 0):
            gives_check = _in_check(piece, side, 3 - who)
            if not gives_check:
                reduce = 1
        score = -_negamax(piece, side, 3 - who, depth - 1 + extend - reduce, -beta, -alpha, ply + 1)
        if reduce and score > alpha and not g_timeout:
            score = -_negamax(piece, side, 3 - who, depth - 1 + extend, -beta, -alpha, ply + 1)
        _unmake(piece, side)
        if g_timeout:
            if extend:
                g_ext += 1
            return 0
        if score > best_score:
            best_score = score
            best_move = (fr*COLS+fc)*90 + (tr*COLS+tc)
        if best_score > alpha:
            alpha = best_score
        if alpha >= beta:
            if cap == 0:
                mt = (fr*COLS+fc)*90 + (tr*COLS+tc)
                if killer1[depth & 63] != mt:
                    killer2[depth & 63] = killer1[depth & 63]
                    killer1[depth & 63] = mt
                histh[(who-1)*8100 + mt] += depth * depth
            break

    if extend:
        g_ext += 1

    cdef signed char flag = 0
    if best_score <= alpha_orig:
        flag = 2
    elif best_score >= beta:
        flag = 1
    if g_use_tt:
        tt_key[slot] = key
        tt_val[slot] = best_score
        tt_depth[slot] = <short>depth
        tt_flag[slot] = flag
        tt_best[slot] = best_move
    return best_score

# ------------------------------------------------------------------- API
def core_reset(int max_depth, int ext_budget, int use_tt=1, int use_lmr=1, int use_ext=1):
    """Reset TT / killers / history / stats for a fresh Engine.search()."""
    global g_nodes, g_qnodes, g_tthits, g_timeout, g_ext, g_maxdepth
    global g_use_tt, g_use_lmr, g_use_ext
    cdef int i
    for i in range(TT_SIZE):
        tt_flag[i] = -1
    for i in range(MAXPLY):
        killer1[i] = -1
        killer2[i] = -1
    for i in range(2 * 8100):
        histh[i] = 0
    g_nodes = 0; g_qnodes = 0; g_tthits = 0
    g_timeout = 0
    g_ext = ext_budget
    g_maxdepth = max_depth
    g_use_tt = use_tt; g_use_lmr = use_lmr; g_use_ext = use_ext

def core_negamax(int[::1] piece, int[::1] side, int who, int depth,
                 int alpha, int beta, double deadline, int base_ply):
    """Search the subtree; returns score from `who`'s perspective.
    Raises TimeoutError if the deadline passes (partial result discarded)."""
    global g_deadline, h_top, g_base_ply
    g_deadline = deadline
    g_base_ply = base_ply
    h_top = 0
    _full_hash(&piece[0], &side[0], who)
    cdef int score = _negamax(&piece[0], &side[0], who, depth, alpha, beta, 0)
    if g_timeout:
        raise TimeoutError()
    return score

def core_stats():
    return (g_nodes, g_qnodes, g_tthits)

def core_perft(int[::1] piece, int[::1] side, int who, int depth):
    """Legal-move perft on the int arrays (verification)."""
    global h_top
    h_top = 0
    cdef long long total = _perft(&piece[0], &side[0], who, depth, 0)
    return total

cdef long long _perft(int* piece, int* side, int who, int depth, int ply):
    if depth == 0:
        return 1
    cdef int* buf = &MBUF[ply * 1024]
    cdef int n = _gen_pseudo(piece, side, who, buf)
    cdef long long total = 0
    cdef int m
    for m in range(n):
        _make(piece, side, buf[m*5], buf[m*5+1], buf[m*5+2], buf[m*5+3])
        if not _in_check(piece, side, who):
            total += _perft(piece, side, 3 - who, depth - 1, ply + 1)
        _unmake(piece, side)
    return total

def core_see(int[::1] piece, int[::1] side, int fr, int fc, int tr, int tc):
    global h_top
    h_top = 0
    return _see(&piece[0], &side[0], fr, fc, tr, tc)

def core_eval(int[::1] piece, int[::1] side, int base_ply):
    global g_base_ply, h_top
    g_base_ply = base_ply
    h_top = 0
    return _evaluate(&piece[0], &side[0])
