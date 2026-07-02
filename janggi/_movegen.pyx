# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""Cython port of pseudo-move generation (board.py generate_pseudo + piece moves).

Operates on flat int arrays piece[90], side[90] (same encoding as _attack.pyx):
  piece: 0 empty, 1 C, 2 P, 3 M, 4 S, 5 J, 6 K, 7 G
  side:  0 none, 1 HAN, 2 CHO

Returns moves as a flat list of 5-int tuples (fr, fc, tr, tc, cap_code) where
cap_code is the captured piece code (0 if none). Python side converts to Move.

Logic mirrors board.py byte-for-byte; differentially verified.
"""

cdef int ROWS = 10
cdef int COLS = 9

cdef inline int _in_board(int r, int c) nogil:
    return 1 if (0 <= r < ROWS and 0 <= c < COLS) else 0

cdef inline int _is_pdiag(int r, int c) nogil:
    if c == 4 and (r == 1 or r == 8):
        return 1
    if (c == 3 or c == 5) and (r == 0 or r == 2 or r == 7 or r == 9):
        return 1
    return 0

cdef inline int _same_palace(int r, int nr) nogil:
    return 1 if ((r <= 2 and nr <= 2) or (r >= 7 and nr >= 7)) else 0

cdef inline int _in_palace(int r, int c, int side) nogil:
    if c < 3 or c > 5:
        return 0
    if side == 1:
        return 1 if (0 <= r <= 2) else 0
    else:
        return 1 if (7 <= r <= 9) else 0


def generate_pseudo_c(int[::1] piece, int[::1] side, int by_side):
    """by_side: 1=HAN, 2=CHO. Returns list of (fr,fc,tr,tc,cap) int tuples."""
    cdef list out = []
    cdef int r, c, idx, pc, nr, nc, t, tside, br, bc, dr, dc
    cdef int jumped
    cdef int b1r, b1c, b2r, b2c
    cdef int fwd

    # direction tables
    cdef int orth[4][2]
    orth[0][0]=1; orth[0][1]=0
    orth[1][0]=-1; orth[1][1]=0
    orth[2][0]=0; orth[2][1]=1
    orth[3][0]=0; orth[3][1]=-1
    cdef int diag[4][2]
    diag[0][0]=1; diag[0][1]=1
    diag[1][0]=1; diag[1][1]=-1
    diag[2][0]=-1; diag[2][1]=1
    diag[3][0]=-1; diag[3][1]=-1

    cdef int i, d

    for r in range(ROWS):
        for c in range(COLS):
            idx = r*COLS + c
            pc = piece[idx]
            if pc == 0 or side[idx] != by_side:
                continue

            if pc == 1:  # Chariot
                for d in range(4):
                    dr = orth[d][0]; dc = orth[d][1]
                    nr = r+dr; nc = c+dc
                    while _in_board(nr, nc):
                        t = piece[nr*COLS+nc]
                        if t == 0:
                            out.append((r,c,nr,nc,0))
                        else:
                            if side[nr*COLS+nc] != by_side:
                                out.append((r,c,nr,nc,t))
                            break
                        nr += dr; nc += dc
                if _is_pdiag(r, c):
                    for d in range(4):
                        dr = diag[d][0]; dc = diag[d][1]
                        nr = r+dr; nc = c+dc
                        while _in_board(nr,nc) and _is_pdiag(nr,nc):
                            if not _same_palace(r,nr):
                                break
                            t = piece[nr*COLS+nc]
                            if t == 0:
                                out.append((r,c,nr,nc,0))
                            else:
                                if side[nr*COLS+nc] != by_side:
                                    out.append((r,c,nr,nc,t))
                                break
                            nr += dr; nc += dc

            elif pc == 2:  # Cannon
                for d in range(4):
                    dr = orth[d][0]; dc = orth[d][1]
                    nr = r+dr; nc = c+dc
                    jumped = 0
                    while _in_board(nr,nc):
                        t = piece[nr*COLS+nc]
                        if jumped == 0:
                            if t != 0:
                                if t == 2:
                                    break
                                jumped = 1
                        else:
                            if t == 0:
                                out.append((r,c,nr,nc,0))
                            else:
                                if t != 2 and side[nr*COLS+nc] != by_side:
                                    out.append((r,c,nr,nc,t))
                                break
                        nr += dr; nc += dc
                if _is_pdiag(r, c):
                    for d in range(4):
                        dr = diag[d][0]; dc = diag[d][1]
                        nr = r+dr; nc = c+dc
                        jumped = 0
                        while _in_board(nr,nc) and _is_pdiag(nr,nc):
                            if not _same_palace(r,nr):
                                break
                            t = piece[nr*COLS+nc]
                            if jumped == 0:
                                if t != 0:
                                    if t == 2:
                                        break
                                    jumped = 1
                            else:
                                if t == 0:
                                    out.append((r,c,nr,nc,0))
                                else:
                                    if t != 2 and side[nr*COLS+nc] != by_side:
                                        out.append((r,c,nr,nc,t))
                                    break
                            nr += dr; nc += dc

            elif pc == 3:  # Horse
                _gen_horse(piece, side, r, c, by_side, out)

            elif pc == 4:  # Elephant
                _gen_elephant(piece, side, r, c, by_side, out)

            elif pc == 6 or pc == 7:  # King / Guard
                for d in range(4):
                    dr = orth[d][0]; dc = orth[d][1]
                    nr = r+dr; nc = c+dc
                    if _in_palace(nr, nc, by_side):
                        t = piece[nr*COLS+nc]
                        if t == 0 or side[nr*COLS+nc] != by_side:
                            out.append((r,c,nr,nc,t))
                if _is_pdiag(r, c):
                    for d in range(4):
                        dr = diag[d][0]; dc = diag[d][1]
                        nr = r+dr; nc = c+dc
                        if _in_palace(nr,nc,by_side) and _is_pdiag(nr,nc):
                            t = piece[nr*COLS+nc]
                            if t == 0 or side[nr*COLS+nc] != by_side:
                                out.append((r,c,nr,nc,t))

            elif pc == 5:  # Soldier
                fwd = 1 if by_side == 1 else -1
                _gen_add(piece, side, r, c, r+fwd, c, by_side, out)
                _gen_add(piece, side, r, c, r, c-1, by_side, out)
                _gen_add(piece, side, r, c, r, c+1, by_side, out)
                if _is_pdiag(r, c):
                    if _is_pdiag(r+fwd, c-1):
                        _gen_add(piece, side, r, c, r+fwd, c-1, by_side, out)
                    if _is_pdiag(r+fwd, c+1):
                        _gen_add(piece, side, r, c, r+fwd, c+1, by_side, out)

    return out


cdef int _gen_add(int[::1] piece, int[::1] side, int r, int c, int nr, int nc, int by_side, list out):
    """Mirror board._add: append if legal landing; return 1 if empty (slide continues)."""
    if not _in_board(nr, nc):
        return 0
    cdef int t = piece[nr*COLS+nc]
    if t == 0:
        out.append((r,c,nr,nc,0))
        return 1
    if side[nr*COLS+nc] != by_side:
        out.append((r,c,nr,nc,t))
    return 0

cdef void _gen_horse(int[::1] piece, int[::1] side, int r, int c, int by_side, list out):
    # legs: (br,bc,dr,dc) block-square then landing
    cdef int legs[8][4]
    legs[0][0]=-1; legs[0][1]=0; legs[0][2]=-2; legs[0][3]=-1
    legs[1][0]=-1; legs[1][1]=0; legs[1][2]=-2; legs[1][3]=1
    legs[2][0]=1;  legs[2][1]=0; legs[2][2]=2;  legs[2][3]=-1
    legs[3][0]=1;  legs[3][1]=0; legs[3][2]=2;  legs[3][3]=1
    legs[4][0]=0;  legs[4][1]=-1; legs[4][2]=-1; legs[4][3]=-2
    legs[5][0]=0;  legs[5][1]=-1; legs[5][2]=1;  legs[5][3]=-2
    legs[6][0]=0;  legs[6][1]=1;  legs[6][2]=-1; legs[6][3]=2
    legs[7][0]=0;  legs[7][1]=1;  legs[7][2]=1;  legs[7][3]=2
    cdef int i, br, bc, dr, dc
    for i in range(8):
        br=legs[i][0]; bc=legs[i][1]; dr=legs[i][2]; dc=legs[i][3]
        if _in_board(r+br, c+bc) and piece[(r+br)*COLS+(c+bc)] == 0:
            _gen_add(piece, side, r, c, r+dr, c+dc, by_side, out)

cdef void _gen_elephant(int[::1] piece, int[::1] side, int r, int c, int by_side, list out):
    cdef int legs[8][6]
    legs[0][0]=-1; legs[0][1]=0;  legs[0][2]=-2; legs[0][3]=-1; legs[0][4]=-3; legs[0][5]=-2
    legs[1][0]=-1; legs[1][1]=0;  legs[1][2]=-2; legs[1][3]=1;  legs[1][4]=-3; legs[1][5]=2
    legs[2][0]=1;  legs[2][1]=0;  legs[2][2]=2;  legs[2][3]=-1; legs[2][4]=3;  legs[2][5]=-2
    legs[3][0]=1;  legs[3][1]=0;  legs[3][2]=2;  legs[3][3]=1;  legs[3][4]=3;  legs[3][5]=2
    legs[4][0]=0;  legs[4][1]=-1; legs[4][2]=-1; legs[4][3]=-2; legs[4][4]=-2; legs[4][5]=-3
    legs[5][0]=0;  legs[5][1]=-1; legs[5][2]=1;  legs[5][3]=-2; legs[5][4]=2;  legs[5][5]=-3
    legs[6][0]=0;  legs[6][1]=1;  legs[6][2]=-1; legs[6][3]=2;  legs[6][4]=-2; legs[6][5]=3
    legs[7][0]=0;  legs[7][1]=1;  legs[7][2]=1;  legs[7][3]=2;  legs[7][4]=2;  legs[7][5]=3
    cdef int i, b1r, b1c, b2r, b2c, dr, dc
    for i in range(8):
        b1r=legs[i][0]; b1c=legs[i][1]; b2r=legs[i][2]; b2c=legs[i][3]; dr=legs[i][4]; dc=legs[i][5]
        if (_in_board(r+b1r,c+b1c) and piece[(r+b1r)*COLS+(c+b1c)]==0
                and _in_board(r+b2r,c+b2c) and piece[(r+b2r)*COLS+(c+b2c)]==0):
            _gen_add(piece, side, r, c, r+dr, c+dc, by_side, out)
