"""Talk to Fairy-Stockfish (UCI, variant janggimodern) about positions of our Board.

The janggimodern variant is the one whose rules match janggi/board.py: same
legal moves (checked on 7,200 random positions), no bikjang restriction.
Fairy-Stockfish also allows a pass; callers restrict it with searchmoves.

Coordinates: our row 0 is HAN's back rank (top), row 9 CHO's; FSF's rank 1 is
White's (= CHO, who moves first) back rank. So rank = 10 - row, file = 'a' + col.
Pieces: ours K G S M C P J  ->  FSF K A B N R C P (CHO uppercase).
"""
import os
import re
import subprocess

# Path to a Fairy-Stockfish binary (largeboard build), e.g. fairy-stockfish-largeboard_x86-64
# from https://github.com/fairy-stockfish/Fairy-Stockfish/releases (GPL-3; not part of this repo).
FSF_PATH = os.environ.get("JANGGI_FSF", "fairy-stockfish")
PIECE_TO_FSF = {"K": "k", "G": "a", "S": "b", "M": "n", "C": "r", "P": "c", "J": "p"}
CHO, HAN = -1, 1


def sq(r, c):
    return f"{chr(97 + c)}{10 - r}"


def move_to_uci(m):
    fr, fc, tr, tc = m[:4]
    return sq(fr, fc) + sq(tr, tc)


def uci_to_move(u):
    mm = re.fullmatch(r"([a-i])(10|[1-9])([a-i])(10|[1-9])", u)
    if not mm:
        return None
    fc = ord(mm.group(1)) - 97
    fr = 10 - int(mm.group(2))
    tc = ord(mm.group(3)) - 97
    tr = 10 - int(mm.group(4))
    return (fr, fc, tr, tc)


def board_to_fen(board, side_to_move, fullmove=1):
    ranks = []
    for r in range(10):
        s, empty = "", 0
        for c in range(9):
            cell = board.grid[r][c]
            if cell is None:
                empty += 1
                continue
            if empty:
                s += str(empty)
                empty = 0
            kind, side = cell
            ch = PIECE_TO_FSF[kind]
            s += ch.upper() if side == CHO else ch
        if empty:
            s += str(empty)
        ranks.append(s)
    stm = "w" if side_to_move == CHO else "b"
    return "/".join(ranks) + f" {stm} - - 0 {fullmove}"


class FSF:
    def __init__(self, threads=1, hash_mb=256, variant="janggimodern", nnue=False):
        self.p = subprocess.Popen([FSF_PATH], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._send("uci")
        self._wait("uciok")
        self._send(f"setoption name UCI_Variant value {variant}")
        self._send(f"setoption name Threads value {threads}")
        self._send(f"setoption name Hash value {hash_mb}")
        self._send(f"setoption name Use NNUE value {'true' if nnue else 'false'}")
        self._send("isready")
        self._wait("readyok")

    def _send(self, s):
        self.p.stdin.write(s + "\n")
        self.p.stdin.flush()

    def _wait(self, token):
        lines = []
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("fsf died: " + "".join(lines[-5:]))
            lines.append(line)
            if line.startswith(token):
                return lines

    def new_game(self):
        self._send("ucinewgame")
        self._send("isready")
        self._wait("readyok")

    def analyse(self, fen, moves=(), depth=None, movetime=None, nodes=None, searchmoves=None, multipv=1):
        self._send(f"setoption name MultiPV value {multipv}")
        pos = f"position fen {fen}"
        if moves:
            pos += " moves " + " ".join(moves)
        self._send(pos)
        go = "go"
        if depth:
            go += f" depth {depth}"
        if movetime:
            go += f" movetime {movetime}"
        if nodes:
            go += f" nodes {nodes}"
        if searchmoves:
            go += " searchmoves " + " ".join(searchmoves)
        self._send(go)
        lines = self._wait("bestmove")
        best = lines[-1].split()[1]
        infos = {}
        for ln in lines:
            if not ln.startswith("info depth") or " pv " not in ln:
                continue
            toks = ln.split()
            d = int(toks[toks.index("depth") + 1])
            mpv = int(toks[toks.index("multipv") + 1]) if "multipv" in toks else 1
            i = toks.index("score")
            kind, val = toks[i + 1], int(toks[i + 2])
            if "upperbound" in toks[i + 3:i + 4] or "lowerbound" in toks[i + 3:i + 4]:
                continue
            pv = toks[toks.index("pv") + 1:]
            nodes_ = int(toks[toks.index("nodes") + 1])
            infos[mpv] = {"depth": d, "kind": kind, "val": val, "pv": pv, "nodes": nodes_}
        return best, infos

    def close(self):
        try:
            self._send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def score_cp(info):
    """Side-to-move centipawns; mate as +-100000 minus distance."""
    if info["kind"] == "mate":
        v = info["val"]
        return 100000 - abs(v) if v > 0 else -100000 + abs(v)
    return info["val"]
