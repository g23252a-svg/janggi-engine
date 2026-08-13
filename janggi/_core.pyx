# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Cython search core: the whole search, on flat int arrays.

core_search() owns iterative deepening, aspiration windows, the root move list
and its ordering carry-over, the principal variation, and the alpha-beta tree
below it -- with no Python objects anywhere in the hot path. Python supplies
the position, the time or node budget, root moves to avoid (repetition), and
the game position keys the search needs to recognise a repetition.

Encodings match _attack / _movegen:
  piece: 0 empty, 1 C, 2 P, 3 M, 4 S, 5 J, 6 K, 7 G
  side : 0 none, 1 HAN, 2 CHO   (enemy of s is 3-s)

Every piece rule here is a transliteration of the verified Python sources
(board.py / evaluate.py / see.py) and tests/test_parity.py holds them together:
perft equality, square-for-square attack equality, exact evaluation and SEE
equality. Change one side of that pair and the tests will say so.
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

# Attack weights for king danger, indexed by piece code.
cdef int KDANGER[8]
KDANGER[0]=0; KDANGER[1]=40; KDANGER[2]=30; KDANGER[3]=20
KDANGER[4]=10; KDANGER[5]=10; KDANGER[6]=0; KDANGER[7]=5

# ------------------------------------------------------------- attack maps
# _attacked() answers one square at a time by scanning outward from it. The
# evaluator asks ~70 such questions per call (every piece for the loose-piece
# term, every palace square for king safety), which made the static evaluation
# the single most expensive thing in the search.
#
# _attack_maps() answers all 180 questions in one forward pass over the pieces:
# for each piece, mark every square it bears on. Same "does this side bear on
# this square" semantics as _attacked(), including defence of one's own pieces
# and the rule that a cannon never bears on a cannon. Exhaustively verified
# square-for-square against _attacked() in tests/test_parity.py.
#
# amap layout: amap[(s - 1) * 90 + sq], s = 1 HAN / 2 CHO.
cdef void _attack_maps(int* piece, int* side, int* amap):
    _attack_maps_w(piece, side, amap, NULL)

cdef void _attack_maps_w(int* piece, int* side, int* amap, int* awt):
    cdef int i, r, c, idx, pc, s, d, dr, dc, nr, nc, t, base, fwd, jumped
    cdef int sr, sc, b1r, b1c, b2r, b2c
    cdef int ORTH[4][2]
    ORTH[0][0]=1; ORTH[0][1]=0; ORTH[1][0]=-1; ORTH[1][1]=0
    ORTH[2][0]=0; ORTH[2][1]=1; ORTH[3][0]=0; ORTH[3][1]=-1
    cdef int DIAG[4][2]
    DIAG[0][0]=1; DIAG[0][1]=1; DIAG[1][0]=1; DIAG[1][1]=-1
    DIAG[2][0]=-1; DIAG[2][1]=1; DIAG[3][0]=-1; DIAG[3][1]=-1
    cdef int HL[8][4]
    HL[0][0]=-1; HL[0][1]=0;  HL[0][2]=-2; HL[0][3]=-1
    HL[1][0]=-1; HL[1][1]=0;  HL[1][2]=-2; HL[1][3]=1
    HL[2][0]=1;  HL[2][1]=0;  HL[2][2]=2;  HL[2][3]=-1
    HL[3][0]=1;  HL[3][1]=0;  HL[3][2]=2;  HL[3][3]=1
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

    for i in range(180):
        amap[i] = 0
    if awt != NULL:
        for i in range(180):
            awt[i] = 0

    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS + c
            pc = piece[idx]
            if pc == 0:
                continue
            s = side[idx]
            base = (s - 1) * 90

            if pc == 1:  # chariot: every square up to and including the blocker
                for d in range(4):
                    dr = ORTH[d][0]; dc = ORTH[d][1]
                    nr = r+dr; nc = c+dc
                    while 0 <= nr < ROWS and 0 <= nc < COLS:
                        amap[base + nr*COLS+nc] = 1
                        if awt != NULL:
                            awt[base + nr*COLS+nc] += KDANGER[pc]
                        if piece[nr*COLS+nc] != 0:
                            break
                        nr += dr; nc += dc
                if _is_pdiag(r, c):
                    for d in range(4):
                        dr = DIAG[d][0]; dc = DIAG[d][1]
                        nr = r+dr; nc = c+dc
                        while (0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr,nc)
                               and _same_half(r,nr)):
                            amap[base + nr*COLS+nc] = 1
                            if awt != NULL:
                                awt[base + nr*COLS+nc] += KDANGER[pc]
                            if piece[nr*COLS+nc] != 0:
                                break
                            nr += dr; nc += dc

            elif pc == 2:  # cannon: past exactly one non-cannon screen
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
                            if t == 2:
                                break        # never bears on a cannon
                            amap[base + nr*COLS+nc] = 1
                            if awt != NULL:
                                awt[base + nr*COLS+nc] += KDANGER[pc]
                            if t != 0:
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
                                if t == 2:
                                    break
                                amap[base + nr*COLS+nc] = 1
                                if awt != NULL:
                                    awt[base + nr*COLS+nc] += KDANGER[pc]
                                if t != 0:
                                    break
                            nr += dr; nc += dc

            elif pc == 3:  # horse
                for i in range(8):
                    sr = r + HL[i][0]; sc = c + HL[i][1]
                    if 0 <= sr < ROWS and 0 <= sc < COLS and piece[sr*COLS+sc] == 0:
                        nr = r + HL[i][2]; nc = c + HL[i][3]
                        if 0 <= nr < ROWS and 0 <= nc < COLS:
                            amap[base + nr*COLS+nc] = 1
                            if awt != NULL:
                                awt[base + nr*COLS+nc] += KDANGER[pc]

            elif pc == 4:  # elephant
                for i in range(8):
                    b1r = r + EL[i][0]; b1c = c + EL[i][1]
                    b2r = r + EL[i][2]; b2c = c + EL[i][3]
                    if (0 <= b1r < ROWS and 0 <= b1c < COLS and piece[b1r*COLS+b1c] == 0
                            and 0 <= b2r < ROWS and 0 <= b2c < COLS
                            and piece[b2r*COLS+b2c] == 0):
                        nr = r + EL[i][4]; nc = c + EL[i][5]
                        if 0 <= nr < ROWS and 0 <= nc < COLS:
                            amap[base + nr*COLS+nc] = 1
                            if awt != NULL:
                                awt[base + nr*COLS+nc] += KDANGER[pc]

            elif pc == 5:  # soldier
                fwd = 1 if s == 1 else -1
                nr = r + fwd
                if 0 <= nr < ROWS:
                    amap[base + nr*COLS+c] = 1
                    if awt != NULL:
                        awt[base + nr*COLS+c] += KDANGER[pc]
                if c - 1 >= 0:
                    amap[base + r*COLS+c-1] = 1
                    if awt != NULL:
                        awt[base + r*COLS+c-1] += KDANGER[pc]
                if c + 1 < COLS:
                    amap[base + r*COLS+c+1] = 1
                    if awt != NULL:
                        awt[base + r*COLS+c+1] += KDANGER[pc]
                if _is_pdiag(r, c):
                    nr = r + fwd
                    for i in range(2):
                        nc = c - 1 if i == 0 else c + 1
                        if 0 <= nr < ROWS and 0 <= nc < COLS and _is_pdiag(nr, nc):
                            amap[base + nr*COLS+nc] = 1
                            if awt != NULL:
                                awt[base + nr*COLS+nc] += KDANGER[pc]

            elif pc == 6 or pc == 7:  # general / guard, palace only
                if _in_palace(r, c, s):
                    for d in range(4):
                        nr = r + ORTH[d][0]; nc = c + ORTH[d][1]
                        if _in_palace(nr, nc, s):
                            amap[base + nr*COLS+nc] = 1
                            if awt != NULL:
                                awt[base + nr*COLS+nc] += KDANGER[pc]
                    if _is_pdiag(r, c):
                        for d in range(4):
                            nr = r + DIAG[d][0]; nc = c + DIAG[d][1]
                            if _in_palace(nr, nc, s) and _is_pdiag(nr, nc):
                                amap[base + nr*COLS+nc] = 1
                                if awt != NULL:
                                    awt[base + nr*COLS+nc] += KDANGER[pc]


def core_attack_map(int[::1] piece, int[::1] side):
    """Expose the maps for differential testing: returns a 180-long list."""
    cdef int amap[180]
    cdef int i
    _attack_maps(&piece[0], &side[0], amap)
    return [amap[i] for i in range(180)]


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
    global g_gencalls
    g_gencalls += 1
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
DEF MAXHIST = 384
cdef int h_fr[MAXHIST]
cdef int h_fc[MAXHIST]
cdef int h_tr[MAXHIST]
cdef int h_tc[MAXHIST]
cdef int h_cap[MAXHIST]
cdef int h_capsd[MAXHIST]
cdef int h_top = 0

cdef unsigned long long path_hash[MAXHIST + 1]
cdef int path_irrev[MAXHIST + 1]

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
    # Repetition bookkeeping: record the resulting position, and remember that
    # a capture makes everything before it unreachable.
    path_hash[h_top] = cur_hash
    if h_cap[h_top - 1] != 0:
        path_irrev[h_top] = h_top
    else:
        path_irrev[h_top] = path_irrev[h_top - 1]

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
    """Is there a piece this cannon could actually jump over? Any ray will do.

    This used to return on the first ray that contained anything, so a good
    screen to one side went unseen whenever the nearest piece on an earlier ray
    was another cannon -- and which ray came first decided the answer, which is
    why the evaluation was not symmetric between the two sides.
    """
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
                if t != 2:
                    return 1
                break          # a cannon cannot be a screen; try another ray
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

    # One forward pass replaces ~70 outward attack scans per evaluation.
    cdef int amap[180]
    _attack_maps(piece, side, amap)

    if ghr >= 0:
        danger = 0
        if amap[90 + ghr*COLS + ghc]:
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
            if amap[90 + idx]:
                danger += 3
        score -= danger * 18
    if gcr >= 0:
        danger = 0
        if amap[gcr*COLS + gcc]:
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
            if amap[idx]:
                danger += 3
        score += danger * 18

    cdef int risk_h = 0, risk_c = 0
    for idx in range(90):
        pc = piece[idx]
        if pc == 0 or pc == 6:
            continue
        s = side[idx]
        if s == 1:
            if amap[90 + idx]:
                if amap[idx]:
                    risk_h += DEF_W[pc]
                else:
                    risk_h += UNDEF_W[pc]
        else:
            if amap[idx]:
                if amap[90 + idx]:
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
    global g_seecalls
    g_seecalls += 1
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

# ============================================================ evaluation v2
# The v1 evaluator knows material, soldier advancement, "middle files are
# nice", loose pieces and a crude king-danger count. That is most of a chess
# engine's first evaluation and almost none of Janggi's actual content. v2 adds
# the things a Janggi player actually looks at:
#
#   * a game phase, so a cannon (which needs screens) is worth less as the
#     board empties and a soldier is worth more
#   * chariot activity: open files, the enemy soldier rank, pressure on the palace
#   * 면포 -- a cannon covering the palace face, the standard defensive setup
#   * horses and elephants scored by how many of their legs are actually free,
#     because a fully blocked horse is nearly a spectator
#   * soldier structure: connected soldiers, soldiers that have reached the palace
#   * king danger that grows with the square of the attacking force rather than
#     linearly, so three attackers matter far more than three times one
#
# Selectable at runtime (SearchOptions.eval_version) so it can be played against
# v1 directly rather than assumed to be better.

cdef int PHASE_TOTAL = 14400      # both sides' non-general material at the start

cdef inline int _horse_free_legs(int* piece, int r, int c):
    cdef int i, sr, sc, nr, nc, free = 0
    cdef int HL[8][4]
    HL[0][0]=-1; HL[0][1]=0;  HL[0][2]=-2; HL[0][3]=-1
    HL[1][0]=-1; HL[1][1]=0;  HL[1][2]=-2; HL[1][3]=1
    HL[2][0]=1;  HL[2][1]=0;  HL[2][2]=2;  HL[2][3]=-1
    HL[3][0]=1;  HL[3][1]=0;  HL[3][2]=2;  HL[3][3]=1
    HL[4][0]=0;  HL[4][1]=-1; HL[4][2]=-1; HL[4][3]=-2
    HL[5][0]=0;  HL[5][1]=-1; HL[5][2]=1;  HL[5][3]=-2
    HL[6][0]=0;  HL[6][1]=1;  HL[6][2]=-1; HL[6][3]=2
    HL[7][0]=0;  HL[7][1]=1;  HL[7][2]=1;  HL[7][3]=2
    for i in range(8):
        sr = r + HL[i][0]; sc = c + HL[i][1]
        nr = r + HL[i][2]; nc = c + HL[i][3]
        if (0 <= sr < ROWS and 0 <= sc < COLS and piece[sr*COLS+sc] == 0
                and 0 <= nr < ROWS and 0 <= nc < COLS):
            free += 1
    return free

cdef inline int _eleph_free_legs(int* piece, int r, int c):
    cdef int i, b1r, b1c, b2r, b2c, nr, nc, free = 0
    cdef int EL[8][6]
    EL[0][0]=-1; EL[0][1]=0;  EL[0][2]=-2; EL[0][3]=-1; EL[0][4]=-3; EL[0][5]=-2
    EL[1][0]=-1; EL[1][1]=0;  EL[1][2]=-2; EL[1][3]=1;  EL[1][4]=-3; EL[1][5]=2
    EL[2][0]=1;  EL[2][1]=0;  EL[2][2]=2;  EL[2][3]=-1; EL[2][4]=3;  EL[2][5]=-2
    EL[3][0]=1;  EL[3][1]=0;  EL[3][2]=2;  EL[3][3]=1;  EL[3][4]=3;  EL[3][5]=2
    EL[4][0]=0;  EL[4][1]=-1; EL[4][2]=-1; EL[4][3]=-2; EL[4][4]=-2; EL[4][5]=-3
    EL[5][0]=0;  EL[5][1]=-1; EL[5][2]=1;  EL[5][3]=-2; EL[5][4]=2;  EL[5][5]=-3
    EL[6][0]=0;  EL[6][1]=1;  EL[6][2]=-1; EL[6][3]=2;  EL[6][4]=-2; EL[6][5]=3
    EL[7][0]=0;  EL[7][1]=1;  EL[7][2]=1;  EL[7][3]=2;  EL[7][4]=2;  EL[7][5]=3
    for i in range(8):
        b1r = r + EL[i][0]; b1c = c + EL[i][1]
        b2r = r + EL[i][2]; b2c = c + EL[i][3]
        nr  = r + EL[i][4]; nc  = c + EL[i][5]
        if (0 <= b1r < ROWS and 0 <= b1c < COLS and piece[b1r*COLS+b1c] == 0
                and 0 <= b2r < ROWS and 0 <= b2c < COLS and piece[b2r*COLS+b2c] == 0
                and 0 <= nr < ROWS and 0 <= nc < COLS):
            free += 1
    return free


cdef int _evaluate2(int* piece, int* side):
    """HAN-positive static score. See the block comment above."""
    cdef int score = 0, material = 0, phase_mat = 0
    cdef int r, c, idx, pc, s, base, v, adv, i, d, nr, nc
    cdef int ghr = -1, ghc = -1, gcr = -1, gcc = -1
    cdef int amap[180]
    cdef int awt[180]
    cdef int sol_file[2][9]        # soldiers per side per file
    cdef int occ_file[9]           # any piece on the file (for open-file chariots)
    cdef int phase, freelegs, danger_h = 0, danger_c = 0
    cdef int ORTH[4][2]
    ORTH[0][0]=1; ORTH[0][1]=0; ORTH[1][0]=-1; ORTH[1][1]=0
    ORTH[2][0]=0; ORTH[2][1]=1; ORTH[3][0]=0; ORTH[3][1]=-1

    for c in range(9):
        sol_file[0][c] = 0; sol_file[1][c] = 0; occ_file[c] = 0

    for idx in range(90):
        pc = piece[idx]
        if pc == 0:
            continue
        s = side[idx]
        occ_file[idx % COLS] += 1
        if pc != 6:
            phase_mat += PVAL[pc]
        if pc == 5:
            sol_file[s-1][idx % COLS] += 1
        if pc == 6:
            if s == 1:
                ghr = idx // COLS; ghc = idx % COLS
            else:
                gcr = idx // COLS; gcc = idx % COLS

    phase = (phase_mat * 256) // PHASE_TOTAL
    if phase > 256:
        phase = 256
    if phase < 0:
        phase = 0

    _attack_maps_w(piece, side, amap, awt)

    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS + c
            pc = piece[idx]
            if pc == 0:
                continue
            s = side[idx]
            base = PVAL[pc]

            # --- phase-adjusted material -------------------------------
            # A cannon needs a screen, so it loses value as the board empties;
            # soldiers gain value; elephants lose a little.
            if pc == 2:
                base -= (150 * (256 - phase)) // 256
            elif pc == 5:
                base += (60 * (256 - phase)) // 256
            elif pc == 4:
                base -= (40 * (256 - phase)) // 256
            material += base if s == 1 else -base
            v = base

            if pc == 5:      # soldier
                adv = r if s == 1 else (ROWS - 1 - r)
                v += adv * 8
                if 3 <= c <= 5:
                    v += 10
                # connected soldiers defend each other
                if c > 0 and piece[idx-1] == 5 and side[idx-1] == s:
                    v += 8
                # a soldier that has reached the enemy palace is a real threat
                if _in_palace(r, c, 3 - s):
                    v += 30
            elif pc == 1:    # chariot
                v += (4 - (c-4 if c >= 4 else 4-c)) * 3
                if occ_file[c] == 1:          # only the chariot on this file
                    v += 25
                elif sol_file[s-1][c] == 0:   # no own soldier blocking it
                    v += 12
                # sitting on the rank the enemy soldiers start on
                if (s == 1 and r == 6) or (s == 2 and r == 3):
                    v += 20
            elif pc == 2:    # cannon
                v += (4 - (c-4 if c >= 4 else 4-c)) * 3
                if _cannon_screen(piece, r, c):
                    v += 15
                # 면포: a cannon inside its own palace covering the general
                if _in_palace(r, c, s):
                    v += 40
            elif pc == 3:    # horse
                v += (4 - (c-4 if c >= 4 else 4-c)) * 3
                freelegs = _horse_free_legs(piece, r, c)
                v += freelegs * 7 - 28        # 4 free legs is par
            elif pc == 4:    # elephant
                freelegs = _eleph_free_legs(piece, r, c)
                v += freelegs * 5 - 20
            elif pc == 7:    # guard: stay home with the general
                if _in_palace(r, c, s):
                    v += 12

            score += v if s == 1 else -v


    # --- king danger ------------------------------------------------------
    # Sum the attacking weight bearing on each palace square, then square it:
    # three pieces converging on a palace is far worse than three times one
    # piece looking at it, and a linear term never expresses that.
    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS + c
            if _in_palace(r, c, 1):
                danger_h += awt[90 + idx]
            elif _in_palace(r, c, 2):
                danger_c += awt[idx]
    score -= (danger_h * danger_h) // 900
    score += (danger_c * danger_c) // 900

    # --- general exposure (v1 term, kept) --------------------------------
    if ghr >= 0:
        v = 0
        for d in range(4):
            nr = ghr + ORTH[d][0]; nc = ghc + ORTH[d][1]
            if _in_palace(nr, nc, 1) and piece[nr*COLS+nc] == 0:
                v += 1
        score -= v * 12
    if gcr >= 0:
        v = 0
        for d in range(4):
            nr = gcr + ORTH[d][0]; nc = gcc + ORTH[d][1]
            if _in_palace(nr, nc, 2) and piece[nr*COLS+nc] == 0:
                v += 1
        score += v * 12

    # --- loose pieces (v1 term, kept) ------------------------------------
    cdef int risk_h = 0, risk_c = 0
    for idx in range(90):
        pc = piece[idx]
        if pc == 0 or pc == 6:
            continue
        s = side[idx]
        if s == 1:
            if amap[90 + idx]:
                risk_h += DEF_W[pc] if amap[idx] else UNDEF_W[pc]
        else:
            if amap[idx]:
                risk_c += DEF_W[pc] if amap[90 + idx] else UNDEF_W[pc]
    score -= risk_h
    score += risk_c

    # --- endgame lock onto official points --------------------------------
    if g_base_ply >= 120:
        score += material * 2
    elif g_base_ply >= 80:
        score += material
    return score


# -------------------------------------------------------------- search state
from libc.math cimport log

DEF TT_SIZE = 2097152          # 2^21 entries
DEF TT_MASK = 2097151
cdef unsigned long long tt_key[TT_SIZE]
cdef int tt_val[TT_SIZE]
cdef short tt_depth[TT_SIZE]
cdef signed char tt_flag[TT_SIZE]   # 0 exact, 1 lower, 2 upper, -1 empty
cdef int tt_best[TT_SIZE]           # from*90+to, -1 none

DEF MAXPLY = 96
cdef int MBUF[MAXPLY * 1024]        # per-ply move buffers (204 moves max)

cdef int killer1[MAXPLY]
cdef int killer2[MAXPLY]
cdef int histh[2 * 8100]
cdef int counterm[2 * 8100]         # counter-move: [side][previous move] -> reply

# Static evaluation at each ply, so a node can ask whether its own side is
# better off than it was two plies ago -- see `improving` in _negamax. NO_EVAL
# marks a ply that never computed one (in check, or above the root).
DEF NO_EVAL = -0x3FFFFFFF
cdef int ply_eval[MAXPLY]

# Mate scores are MATE - ply, so anything past this bound is a forced mate.
cdef int MATE_BOUND = MATE - 4096

# Late-move reduction table, indexed [depth][move index].
cdef int LMRTAB[64][64]

cdef long long g_seecalls = 0
cdef long long g_gencalls = 0
cdef long long g_nodes = 0
cdef long long g_qnodes = 0
cdef long long g_tthits = 0
cdef int g_timeout = 0
cdef double g_deadline = 0.0
cdef long long g_node_limit = 0
cdef int g_ext = 0
cdef int g_maxdepth = 6
cdef int g_use_lmr = 1
cdef int g_use_ext = 1
cdef int g_use_tt = 1
cdef int g_use_nmp = 1
cdef int g_use_pvs = 1
cdef int g_use_fut = 1
cdef int g_use_lmp = 1
cdef int g_use_asp = 1
cdef int g_use_rep = 1
cdef int g_eval_ver = 2      # 1 = original evaluator, 2 = the Janggi-aware one
cdef int g_use_improving = 1 # scale pruning by whether the position is improving
cdef int g_use_hist_lmr = 1  # scale the late-move reduction by move history
# Largest history value seen this search, so the LMR test above can ask "is this
# move in the top quarter of what we have seen" instead of comparing against a
# constant that drifts in meaning as histh accumulates.
cdef int g_hist_max = 0

# Repetition: hashes along the current search line plus the hashes of game
# positions since the last capture, which Python supplies.
DEF MAXGAME = 256
cdef unsigned long long game_hash[MAXGAME]
cdef int n_game_hash = 0

# Root move list, kept between iterations so the previous depth's ranking
# orders the next one.
cdef int root_from[204]
cdef int root_to[204]
cdef int root_cap[204]
cdef int root_score[204]
cdef int n_root = 0


cdef void _init_lmr():
    cdef int d, m
    cdef double red
    for d in range(64):
        for m in range(64):
            if d < 3 or m < 2:
                LMRTAB[d][m] = 0
            else:
                red = 0.55 + log(<double>d) * log(<double>m) / 2.5
                LMRTAB[d][m] = <int>red

_init_lmr()


cdef int _time_up():
    global g_timeout
    if g_timeout:
        return 1
    if g_node_limit > 0 and (g_nodes + g_qnodes) >= g_node_limit:
        g_timeout = 1
        return 1
    if g_deadline > 0.0 and (g_nodes + g_qnodes) % 2048 == 0:
        if _pytime.time() > g_deadline:
            g_timeout = 1
            return 1
    return 0


cdef inline int _eval_for(int* piece, int* side, int who):
    """Static score from `who`'s point of view."""
    cdef int s = _evaluate2(piece, side) if g_eval_ver == 2 else _evaluate(piece, side)
    return -s if who == 2 else s


cdef int _is_repetition():
    """True if the current position already occurred in this line or the game.

    Only positions with the same side to move can repeat, hence the stride of
    two, and nothing before the last capture can repeat at all.
    """
    cdef int i = h_top - 2
    cdef int base = path_irrev[h_top]
    while i >= base:
        if path_hash[i] == cur_hash:
            return 1
        i -= 2
    if base == 0:
        for i in range(n_game_hash):
            if game_hash[i] == cur_hash:
                return 1
    return 0


cdef void _make_null():
    """Pass the move. Janggi permits passing, so this cannot be zugzwang-unsound
    the way it can be in chess; the material guard below is belt and braces."""
    global h_top, cur_hash
    h_fr[h_top] = -1; h_fc[h_top] = -1
    h_tr[h_top] = -1; h_tc[h_top] = -1
    h_cap[h_top] = 0; h_capsd[h_top] = 0
    h_top += 1
    cur_hash ^= Z_SIDE
    path_hash[h_top] = cur_hash
    path_irrev[h_top] = h_top     # nothing before a pass can recur


cdef void _unmake_null():
    global h_top, cur_hash
    h_top -= 1
    cur_hash ^= Z_SIDE


cdef int _has_null_material(int* piece, int* side, int who):
    """At least one piece that can create a threat on its own."""
    cdef int i, pc
    for i in range(90):
        pc = piece[i]
        if side[i] == who and (pc == 1 or pc == 2 or pc == 3 or pc == 4):
            return 1
    return 0


cdef int _qsearch(int* piece, int* side, int who, int alpha, int beta, int ply):
    global g_qnodes
    g_qnodes += 1
    if _time_up():
        return 0
    cdef int stand = _eval_for(piece, side, who)
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
    cdef int i, j, m, tmp
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
        if not in_chk:
            # Delta pruning: even winning this piece outright cannot lift the
            # score to alpha, so the whole capture is irrelevant.
            if cap != 6 and stand + PVAL[cap] + 200 < alpha:
                continue
            # SEE pruning is safe for optional captures, never for forced
            # evasions. A capture whose victim is worth at least as much as the
            # attacker can never come out negative -- worst case the attacker
            # is lost and the victim kept -- so the call is skippable outright.
            if PVAL[cap] < PVAL[piece[fr*COLS+fc]]:
                if _see(piece, side, fr, fc, tr, tc) < 0:
                    continue
        _make(piece, side, fr, fc, tr, tc)
        # Pseudo-legal moves that leave our own general attacked are illegal.
        if _in_check(piece, side, who):
            _unmake(piece, side)
            continue
        legal_found = 1
        if cap == 6:  # capturing the general ends it
            _unmake(piece, side)
            return MATE - ply
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


cdef int _negamax(int* piece, int* side, int who, int depth, int alpha, int beta,
                  int ply, int is_pv, int can_null):
    global g_hist_max
    global g_nodes, g_tthits, g_ext
    g_nodes += 1
    if _time_up():
        return 0

    if ply > 0 and g_use_rep and _is_repetition():
        return 0                    # repetition: neither side has made progress

    # Mate-distance pruning: a mate found closer to the root already beats
    # anything this subtree can produce.
    if alpha < -MATE + ply:
        alpha = -MATE + ply
    if beta > MATE - ply - 1:
        beta = MATE - ply - 1
    if alpha >= beta:
        return alpha

    cdef int alpha_orig = alpha
    cdef unsigned long long key = cur_hash
    cdef int slot = <int>(key & TT_MASK)
    cdef int tt_move = -1
    cdef int tt_v, tt_f
    if g_use_tt and tt_flag[slot] >= 0 and tt_key[slot] == key:
        tt_move = tt_best[slot]
        if tt_depth[slot] >= depth and not is_pv:
            tt_v = tt_val[slot]
            # Mate scores are stored relative to the storing node; rebase them
            # onto this ply or the distance to mate comes out wrong.
            if tt_v > MATE_BOUND:
                tt_v -= ply
            elif tt_v < -MATE_BOUND:
                tt_v += ply
            g_tthits += 1
            tt_f = tt_flag[slot]
            if tt_f == 0:
                return tt_v
            if tt_f == 1 and tt_v > alpha:
                alpha = tt_v
            elif tt_f == 2 and tt_v < beta:
                beta = tt_v
            if alpha >= beta:
                return tt_v

    if depth <= 0:
        return _qsearch(piece, side, who, alpha, beta, ply)
    if ply >= MAXPLY - 2:
        return _eval_for(piece, side, who)

    cdef int in_chk = _in_check(piece, side, who)
    cdef int static_eval = 0
    cdef int R, score

    # Is this side better off than when it last moved? Ply-2 is the same
    # side to move, so the comparison is like for like. A position that is
    # getting worse is one where the quiet moves on offer are more likely to be
    # useless, so it is pruned harder; one that is improving gets the benefit of
    # the doubt. Unknown (in check here or there) counts as improving, which is
    # the conservative side -- it prunes less.
    cdef int improving = 1
    ply_eval[ply] = NO_EVAL

    if not in_chk:
        static_eval = _eval_for(piece, side, who)
        ply_eval[ply] = static_eval
        if g_use_improving and ply >= 2 and ply_eval[ply-2] != NO_EVAL:
            improving = 1 if static_eval > ply_eval[ply-2] else 0

        # Reverse futility: so far ahead that giving up a chunk per remaining
        # ply still beats beta.
        if (g_use_fut and not is_pv and depth <= 6 and beta < MATE_BOUND
                and static_eval - (110 - 20 * (1 - improving)) * depth >= beta):
            return static_eval

        # Null-move pruning: pass and see whether the opponent can still not
        # reach beta. Janggi allows an actual pass, so this models a real option.
        if (g_use_nmp and not is_pv and can_null and depth >= 3
                and static_eval >= beta and beta < MATE_BOUND
                and _has_null_material(piece, side, who)):
            # Scaling R by how far above beta the position stands was tried and
            # removed: 19-21 of 40 alone against no change at all, i.e. a
            # slightly negative trend and no evidence either way.
            R = 3 + depth / 5
            if R > depth - 1:
                R = depth - 1
            _make_null()
            score = -_negamax(piece, side, 3 - who, depth - 1 - R,
                              -beta, -beta + 1, ply + 1, 0, 0)
            _unmake_null()
            if g_timeout:
                return 0
            if score >= beta:
                # A mate "proved" by letting the opponent move twice is not a
                # mate; report the bound instead.
                if score > MATE_BOUND:
                    score = beta
                return score

    cdef int* buf = &MBUF[ply * 1024]
    cdef int n = _gen_pseudo(piece, side, who, buf)

    # legal filter + ordering keys in one pass
    cdef int legal[204]
    cdef long long mkey[204]
    cdef int nl = 0
    cdef int m, fr, fc, tr, tc, cap
    cdef long long k
    cdef int mt, bucket, sub, see_v, prev_mt, cm
    prev_mt = -1
    if h_top > 0 and h_fr[h_top-1] >= 0:
        prev_mt = (h_fr[h_top-1]*COLS + h_fc[h_top-1])*90 + (h_tr[h_top-1]*COLS + h_tc[h_top-1])
    cm = counterm[(who-1)*8100 + prev_mt] if prev_mt >= 0 else -1

    # Ordering keys only -- legality is checked lazily, when a move is actually
    # played. Testing every pseudo-move up front cost a make/unmake and a full
    # attack scan for all ~35 moves at every node, when alpha-beta typically
    # cuts off after two or three of them.
    for m in range(n):
        fr = buf[m*5]; fc = buf[m*5+1]; tr = buf[m*5+2]; tc = buf[m*5+3]; cap = buf[m*5+4]
        mt = (fr*COLS+fc)*90 + (tr*COLS+tc)
        if tt_move >= 0 and mt == tt_move:
            bucket = 7
            sub = 0
        elif cap != 0:
            # Ordering keeps the exact SEE. Approximating it here (victim minus
            # attacker) saved the call but ordered winning captures worse, and
            # a depth-12 opening search went from 2.5M nodes to 4.2M -- the
            # ordering is worth far more than the calls it costs.
            see_v = _see(piece, side, fr, fc, tr, tc)
            if see_v >= 0:
                bucket = 6
                sub = see_v * 16 + PVAL[cap] // 100
            else:
                bucket = 1          # losing capture: below every quiet move
                sub = 30000 + see_v
        elif mt == killer1[ply] or mt == killer2[ply]:
            bucket = 5
            sub = 0
        elif mt == cm:
            bucket = 4
            sub = 0
        else:
            bucket = 2
            sub = histh[(who-1)*8100 + mt]
            if sub > 4194303:
                sub = 4194303
        k = ((<long long>bucket) << 40) + <long long>sub
        legal[nl] = m
        mkey[nl] = k
        nl += 1

    if nl == 0:
        # No pseudo-move at all: in Janggi that loses on the spot.
        return -MATE + ply

    # insertion sort desc by mkey
    cdef int i, j, ti2
    cdef long long tk
    for i in range(1, nl):
        ti2 = legal[i]; tk = mkey[i]
        j = i - 1
        while j >= 0 and mkey[j] < tk:
            legal[j+1] = legal[j]; mkey[j+1] = mkey[j]
            j -= 1
        legal[j+1] = ti2; mkey[j+1] = tk

    cdef int extend = 0
    if g_use_ext and g_ext > 0 and in_chk:
        extend = 1
        g_ext -= 1

    cdef int best_score = -MATE * 2
    cdef int best_move = -1
    cdef int reduce, gives_check, new_depth, quiets, di, mi, hv
    cdef int fut_margin = 0
    cdef int played = 0          # legal moves actually searched at this node
    quiets = 0
    if g_use_fut and not in_chk and not is_pv and depth <= 3:
        fut_margin = static_eval + (120 - 25 * (1 - improving)) * depth + 180
    # A position that is getting worse gets a shorter quiet list before
    # late-move pruning gives up on it.
    cdef int lmp_count = 4 + depth * depth
    if not improving:
        lmp_count = 2 + depth * depth / 2

    for i in range(nl):
        m = legal[i]
        fr = buf[m*5]; fc = buf[m*5+1]; tr = buf[m*5+2]; tc = buf[m*5+3]; cap = buf[m*5+4]
        mt = (fr*COLS+fc)*90 + (tr*COLS+tc)

        if cap == 0 and best_move >= 0 and not in_chk and not is_pv:
            # Late-move pruning: deep in a bad-looking quiet list, stop looking.
            if (g_use_lmp and depth <= 4 and best_score > -MATE_BOUND
                    and quiets >= lmp_count):
                quiets += 1
                continue
            # Futility: this quiet move cannot plausibly reach alpha.
            if (fut_margin != 0 and fut_margin <= alpha
                    and best_score > -MATE_BOUND):
                quiets += 1
                continue

        _make(piece, side, fr, fc, tr, tc)
        # Now that the move is on the board, is it even legal?
        if _in_check(piece, side, who):
            _unmake(piece, side)
            continue
        if cap == 0:
            quiets += 1
        gives_check = _in_check(piece, side, 3 - who)
        new_depth = depth - 1 + extend

        reduce = 0
        if (g_use_lmr and extend == 0 and cap == 0 and not gives_check
                and depth >= 3 and played >= 2):
            di = depth if depth < 63 else 63
            mi = played if played < 63 else 63
            reduce = LMRTAB[di][mi]
            if is_pv and reduce > 0:
                reduce -= 1
            if not improving and reduce >= 0:
                reduce += 1
            if g_use_hist_lmr and reduce > 0:
                # A quiet move that has caused cutoffs all over this search is
                # not a late move in any meaningful sense -- the ordering just
                # has not caught up. Search it closer to full depth, and push
                # the ones with a history of doing nothing further down.
                #
                # Measured against the running maximum, NOT against a constant.
                # histh is only cleared once per search and grows without bound
                # (+= depth*depth), so a fixed cutoff means something different
                # in the first iteration than in the twelfth, and something
                # different again at another time limit. "In the top quarter of
                # what this search has seen" means the same thing throughout.
                hv = histh[(who-1)*8100 + mt]
                if hv == 0:
                    reduce += 1
                elif g_hist_max > 0 and hv * 4 >= g_hist_max * 3:
                    reduce -= 1
            if reduce > new_depth - 1:
                reduce = new_depth - 1
            if reduce < 0:
                reduce = 0

        if played == 0 or not g_use_pvs:
            score = -_negamax(piece, side, 3 - who, new_depth - reduce,
                              -beta, -alpha, ply + 1, is_pv, 1)
            if reduce and score > alpha and not g_timeout:
                score = -_negamax(piece, side, 3 - who, new_depth,
                                  -beta, -alpha, ply + 1, is_pv, 1)
        else:
            # Principal variation search: everything after the first move only
            # has to be shown to be worse, which a null window does far cheaper.
            score = -_negamax(piece, side, 3 - who, new_depth - reduce,
                              -alpha - 1, -alpha, ply + 1, 0, 1)
            if score > alpha and reduce and not g_timeout:
                score = -_negamax(piece, side, 3 - who, new_depth,
                                  -alpha - 1, -alpha, ply + 1, 0, 1)
            if score > alpha and score < beta and not g_timeout:
                score = -_negamax(piece, side, 3 - who, new_depth,
                                  -beta, -alpha, ply + 1, is_pv, 1)
        _unmake(piece, side)
        played += 1

        if g_timeout:
            if extend:
                g_ext += 1
            return 0
        if score > best_score:
            best_score = score
            best_move = mt
        if best_score > alpha:
            alpha = best_score
        if alpha >= beta:
            if cap == 0:
                if killer1[ply] != mt:
                    killer2[ply] = killer1[ply]
                    killer1[ply] = mt
                histh[(who-1)*8100 + mt] += depth * depth
                if histh[(who-1)*8100 + mt] > g_hist_max:
                    g_hist_max = histh[(who-1)*8100 + mt]
                if prev_mt >= 0:
                    counterm[(who-1)*8100 + prev_mt] = mt
            break

    if extend:
        g_ext += 1

    if played == 0:
        # Every pseudo-move was illegal, or every one that was not got pruned.
        # Pruning never fires while best_move is unset, so this really is
        # "no legal move" -- which loses in Janggi, checked or not.
        return -MATE + ply

    cdef signed char flag = 0
    if best_score <= alpha_orig:
        flag = 2
    elif best_score >= beta:
        flag = 1
    if g_use_tt:
        # Depth-preferred: never let a shallow node evict a deeper result for
        # the same position.
        if tt_flag[slot] < 0 or tt_key[slot] != key or depth >= tt_depth[slot]:
            tt_v = best_score
            if tt_v > MATE_BOUND:
                tt_v += ply
            elif tt_v < -MATE_BOUND:
                tt_v -= ply
            tt_key[slot] = key
            tt_val[slot] = tt_v
            tt_depth[slot] = <short>depth
            tt_flag[slot] = flag
            tt_best[slot] = best_move
    return best_score


# ------------------------------------------------------------------- root
cdef int _root_iteration(int* piece, int* side, int who, int depth,
                         int alpha, int beta, int* best_idx):
    """Search every root move at `depth`; fill root_score and return the best."""
    cdef int i, fr, fc, tr, tc, score, best = -MATE * 2
    cdef int a = alpha
    best_idx[0] = -1
    for i in range(n_root):
        fr = root_from[i] // COLS; fc = root_from[i] % COLS
        tr = root_to[i] // COLS;   tc = root_to[i] % COLS
        _make(piece, side, fr, fc, tr, tc)
        if i == 0 or not g_use_pvs:
            score = -_negamax(piece, side, 3 - who, depth - 1, -beta, -a, 1, 1, 1)
        else:
            score = -_negamax(piece, side, 3 - who, depth - 1, -a - 1, -a, 1, 0, 1)
            if score > a and score < beta and not g_timeout:
                score = -_negamax(piece, side, 3 - who, depth - 1, -beta, -a, 1, 1, 1)
        _unmake(piece, side)
        if g_timeout:
            # Keep whatever this iteration already proved; the caller decides
            # whether a partial iteration may replace the previous best.
            return best
        root_score[i] = score
        if score > best:
            best = score
            best_idx[0] = i
        if score > a:
            a = score
        if a >= beta:
            break
    return best


cdef void _sort_root():
    """Best-scoring root move first, so the next iteration orders well."""
    cdef int i, j, tf, tt_, tc_, ts
    for i in range(1, n_root):
        tf = root_from[i]; tt_ = root_to[i]; tc_ = root_cap[i]; ts = root_score[i]
        j = i - 1
        while j >= 0 and root_score[j] < ts:
            root_from[j+1] = root_from[j]; root_to[j+1] = root_to[j]
            root_cap[j+1] = root_cap[j]; root_score[j+1] = root_score[j]
            j -= 1
        root_from[j+1] = tf; root_to[j+1] = tt_
        root_cap[j+1] = tc_; root_score[j+1] = ts


# ------------------------------------------------------------------- API
def core_reset(int max_depth, int ext_budget, int use_tt=1, int use_lmr=1,
               int use_ext=1, int use_nmp=1, int use_pvs=1, int use_fut=1,
               int use_lmp=1, int use_asp=1, int use_rep=1,
               long long node_limit=0, int eval_version=2,
               int use_improving=1, int use_hist_lmr=1):
    """Reset TT / killers / history / stats for a fresh Engine.search()."""
    global g_nodes, g_qnodes, g_tthits, g_timeout, g_ext, g_maxdepth
    global g_use_tt, g_use_lmr, g_use_ext, g_use_nmp, g_use_pvs
    global g_use_fut, g_use_lmp, g_use_asp, g_use_rep, g_node_limit, n_game_hash
    global g_eval_ver, g_use_improving, g_use_hist_lmr, g_hist_max
    cdef int i
    for i in range(TT_SIZE):
        tt_flag[i] = -1
    for i in range(MAXPLY):
        killer1[i] = -1
        killer2[i] = -1
        ply_eval[i] = NO_EVAL
    for i in range(2 * 8100):
        histh[i] = 0
        counterm[i] = -1
    g_nodes = 0; g_qnodes = 0; g_tthits = 0
    global g_seecalls, g_gencalls
    g_seecalls = 0; g_gencalls = 0
    g_timeout = 0
    g_ext = ext_budget
    g_maxdepth = max_depth
    g_use_tt = use_tt; g_use_lmr = use_lmr; g_use_ext = use_ext
    g_use_nmp = use_nmp; g_use_pvs = use_pvs; g_use_fut = use_fut
    g_use_lmp = use_lmp; g_use_asp = use_asp; g_use_rep = use_rep
    g_node_limit = node_limit
    g_eval_ver = eval_version
    g_use_improving = use_improving
    g_use_hist_lmr = use_hist_lmr
    g_hist_max = 0
    n_game_hash = 0


def core_search(int[::1] piece, int[::1] side, int who, int max_depth,
                double deadline, int base_ply, object history_hashes=None,
                object forbidden=None):
    """Full iterative-deepening search, root included.

    Returns (from_sq, to_sq, cap, score, depth_reached, pv) where pv is a list
    of (from_sq, to_sq) pairs walked out of the transposition table.

    Keeping the root in here (rather than driving it one move at a time from
    Python) is what makes aspiration windows, root move ordering carry-over
    and a real principal variation possible.
    """
    global g_deadline, h_top, g_base_ply, n_root, n_game_hash, g_timeout, g_ext
    g_deadline = deadline
    g_base_ply = base_ply
    g_timeout = 0
    h_top = 0
    _full_hash(&piece[0], &side[0], who)
    path_hash[0] = cur_hash
    path_irrev[0] = 0

    n_game_hash = 0
    if history_hashes is not None:
        for h in history_hashes:
            if n_game_hash >= MAXGAME:
                break
            game_hash[n_game_hash] = <unsigned long long>h
            n_game_hash += 1

    cdef int ban[204]
    cdef int n_ban = 0
    cdef int bfr, bfc, btr, btc
    if forbidden is not None:
        for entry in forbidden:
            if n_ban >= 204:
                break
            bfr = entry[0]; bfc = entry[1]; btr = entry[2]; btc = entry[3]
            ban[n_ban] = (bfr * COLS + bfc) * 90 + (btr * COLS + btc)
            n_ban += 1

    # --- build the legal root move list ---------------------------------
    cdef int rbuf[1024]
    cdef int n = _gen_pseudo(&piece[0], &side[0], who, rbuf)
    cdef int m, fr, fc, tr, tc, cap, mt, i, banned
    n_root = 0
    for m in range(n):
        fr = rbuf[m*5]; fc = rbuf[m*5+1]; tr = rbuf[m*5+2]; tc = rbuf[m*5+3]; cap = rbuf[m*5+4]
        _make(&piece[0], &side[0], fr, fc, tr, tc)
        if _in_check(&piece[0], &side[0], who):
            _unmake(&piece[0], &side[0])
            continue
        _unmake(&piece[0], &side[0])
        mt = (fr*COLS+fc)*90 + (tr*COLS+tc)
        banned = 0
        for i in range(n_ban):
            if ban[i] == mt:
                banned = 1
                break
        if banned:
            continue
        root_from[n_root] = fr*COLS+fc
        root_to[n_root] = tr*COLS+tc
        root_cap[n_root] = cap
        root_score[n_root] = 0
        n_root += 1
        if n_root >= 204:
            break

    if n_root == 0:
        return (-1, -1, 0, -MATE, 0, [])

    # Order the first iteration by capture value so depth 1 already starts well.
    for i in range(n_root):
        root_score[i] = PVAL[root_cap[i]] * 10
    _sort_root()

    cdef int depth, best_idx, score, alpha, beta, window
    cdef int final_from = root_from[0], final_to = root_to[0]
    cdef int final_cap = root_cap[0], final_score = 0, depth_done = 0
    cdef int ext_budget = g_ext

    for depth in range(1, max_depth + 1):
        g_ext = ext_budget
        if g_use_asp and depth >= 4 and depth_done > 0 and final_score < MATE_BOUND \
                and final_score > -MATE_BOUND:
            window = 40
        else:
            window = 0

        while True:
            if window > 0:
                alpha = final_score - window
                beta = final_score + window
            else:
                alpha = -MATE * 2
                beta = MATE * 2
            score = _root_iteration(&piece[0], &side[0], who, depth,
                                    alpha, beta, &best_idx)
            if g_timeout:
                break
            if window > 0 and (score <= alpha or score >= beta):
                # Aspiration failed: widen and redo this depth.
                window *= 4
                if window > 1200:
                    window = 0
                continue
            break

        if g_timeout:
            # Accept a partially searched deeper iteration only when it found
            # something strictly better than the last completed depth.
            if best_idx >= 0 and score > final_score and depth_done > 0:
                final_from = root_from[best_idx]
                final_to = root_to[best_idx]
                final_cap = root_cap[best_idx]
                final_score = score
            break

        if best_idx >= 0:
            final_from = root_from[best_idx]
            final_to = root_to[best_idx]
            final_cap = root_cap[best_idx]
            final_score = score
        depth_done = depth
        _sort_root()
        if final_score > MATE_BOUND or final_score < -MATE_BOUND:
            break     # forced result found; deeper search cannot improve on it

    # --- principal variation, walked out of the transposition table ------
    pv = []
    cdef int pv_from = final_from, pv_to = final_to
    cdef int made = 0, slot, bm, ok
    cdef int pn, pm
    cdef int pbuf[1024]
    cdef int cur_who = who
    while made < 24:
        pv.append((pv_from, pv_to))
        _make(&piece[0], &side[0], pv_from // COLS, pv_from % COLS,
              pv_to // COLS, pv_to % COLS)
        made += 1
        cur_who = 3 - cur_who
        slot = <int>(cur_hash & TT_MASK)
        if tt_flag[slot] < 0 or tt_key[slot] != cur_hash:
            break
        bm = tt_best[slot]
        if bm < 0:
            break
        pv_from = bm // 90
        pv_to = bm % 90
        # Validate against real legal moves: a table collision must never put
        # an impossible move in front of the user.
        pn = _gen_pseudo(&piece[0], &side[0], cur_who, pbuf)
        ok = 0
        for pm in range(pn):
            if (pbuf[pm*5]*COLS + pbuf[pm*5+1]) == pv_from and \
               (pbuf[pm*5+2]*COLS + pbuf[pm*5+3]) == pv_to:
                ok = 1
                break
        if not ok:
            break
    for i in range(made):
        _unmake(&piece[0], &side[0])

    return (final_from, final_to, final_cap, final_score, depth_done, pv)


def core_negamax(int[::1] piece, int[::1] side, int who, int depth,
                 int alpha, int beta, double deadline, int base_ply):
    """Search one subtree; returns the score from `who`'s perspective.
    Raises TimeoutError if the deadline passes (partial result discarded)."""
    global g_deadline, h_top, g_base_ply, n_game_hash
    g_deadline = deadline
    g_base_ply = base_ply
    h_top = 0
    n_game_hash = 0
    _full_hash(&piece[0], &side[0], who)
    path_hash[0] = cur_hash
    path_irrev[0] = 0
    cdef int score = _negamax(&piece[0], &side[0], who, depth, alpha, beta, 0, 1, 1)
    if g_timeout:
        raise TimeoutError()
    return score


def core_stats():
    return (g_nodes, g_qnodes, g_tthits)

def core_diag():
    return (g_seecalls, g_gencalls)


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


def core_eval(int[::1] piece, int[::1] side, int base_ply, int version=1):
    """evaluate(board, include_mobility=False). version=1 is the original."""
    global g_base_ply, h_top
    g_base_ply = base_ply
    h_top = 0
    if version == 2:
        return _evaluate2(&piece[0], &side[0])
    return _evaluate(&piece[0], &side[0])


cdef int MOBBUF[1024]

def core_eval_mob(int[::1] piece, int[::1] side, int base_ply):
    """evaluate(board, include_mobility=True).

    Counts both sides' pseudo-moves in C rather than materialising two Python
    lists of Move tuples purely to take their length.
    """
    global g_base_ply, h_top
    g_base_ply = base_ply
    h_top = 0
    cdef int mob = _gen_pseudo(&piece[0], &side[0], 1, MOBBUF)
    mob -= _gen_pseudo(&piece[0], &side[0], 2, MOBBUF)
    return _evaluate(&piece[0], &side[0]) + 2 * mob
