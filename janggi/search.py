"""Search: the part that actually makes the engine strong.

Techniques implemented here:
  * Negamax alpha-beta pruning.
  * Iterative deepening (search depth 1, 2, 3 ... up to a limit or time budget),
    which gives a usable move at every step and feeds the transposition table
    and move ordering for the next, deeper pass.
  * Zobrist hashing + a transposition table to avoid re-searching positions
    reached by different move orders.
  * Quiescence search: at the leaves, keep searching captures until the
    position is "quiet", which removes the horizon effect on exchanges.
  * Move ordering: transposition-table best move first, then captures ordered
    by MVV-LVA (most valuable victim, least valuable attacker).

The public entry point is `Engine.search(board, side, ...)`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .board import Board, Move, HAN, CHO, ROWS, COLS, PIECE_VALUE
from .evaluate import evaluate
from .see import see

MATE = 1_000_000

# Transposition table entry flags.
EXACT, LOWER, UPPER = 0, 1, 2


# ----------------------------------------------------------------- Zobrist
_PIECE_INDEX = {k: i for i, k in enumerate("KCPMSGJ")}


def _build_zobrist() -> dict:
    rng = random.Random(20260619)  # fixed seed for reproducible hashing
    table = {}
    for r in range(ROWS):
        for c in range(COLS):
            for kind in _PIECE_INDEX:
                for side in (HAN, CHO):
                    table[(r, c, kind, side)] = rng.getrandbits(64)
    table["side"] = rng.getrandbits(64)
    return table


_ZOBRIST = _build_zobrist()


def zobrist_hash(board: Board) -> int:
    h = 0
    g = board.grid
    for r in range(ROWS):
        for c in range(COLS):
            p = g[r][c]
            if p is not None:
                h ^= _ZOBRIST[(r, c, p[0], p[1])]
    if board.side_to_move == CHO:
        h ^= _ZOBRIST["side"]
    return h


@dataclass
class TTEntry:
    depth: int
    value: int
    flag: int
    best: Move | None


@dataclass
class SearchStats:
    nodes: int = 0
    qnodes: int = 0
    tt_hits: int = 0
    depth_reached: int = 0


class Engine:
    EXT_BUDGET = 3  # check-extension ply budget per branch (0 disables)
    _ROOT_TOPK = 10  # deep root passes only search this many top shallow-ranked moves

    def __init__(self, max_depth: int = 6, time_limit: float | None = None) -> None:
        self.max_depth = max_depth
        self.time_limit = time_limit
        self.tt: dict[int, TTEntry] = {}
        self.stats = SearchStats()
        self._deadline: float | None = None
        self._forbidden: set[tuple[int, int, int, int]] = set()
        # Killer moves: two per ply that caused a beta cutoff (quiet moves only).
        self._killers: dict[int, list[tuple[int, int, int, int]]] = {}
        # History heuristic: cumulative cutoff score per (side, move) tuple.
        self._history: dict[tuple[int, int, int, int, int], int] = {}

    # --------------------------------------------------------- public API
    def search(
        self,
        board: Board,
        side: int,
        forbidden_moves: set[tuple[int, int, int, int]] | None = None,
    ) -> tuple[Move | None, int]:
        """Return (best_move, score_from_side's_perspective).

        forbidden_moves: root moves (as (fr,fc,tr,tc) tuples) the engine must
        not choose — used to exclude moves that would cause a 3rd repetition.
        """
        self._forbidden = forbidden_moves or set()
        self.stats = SearchStats()
        self._killers = {}
        self._history = {}
        # Total extra plies the search may spend on check-extensions in any one
        # branch. Caps tree growth so sharp positions go deeper without the whole
        # search blowing up. Read from the class attribute so it can be tuned or
        # disabled (0) for A/B comparison.
        self._ext_budget = self.EXT_BUDGET
        self._deadline = (time.time() + self.time_limit) if self.time_limit else None
        best_move: Move | None = None
        best_score = 0
        # Iterative deepening. A depth is only accepted once it COMPLETES; if the
        # time limit interrupts a depth partway through, that partial result is
        # discarded and we keep the last fully-searched depth. This keeps the
        # recommendation deterministic — the same position always yields the
        # same move for a given completed depth, instead of flickering based on
        # exactly how far an interrupted pass happened to get.
        # Candidate narrowing for deep iterations. Searching every root move to
        # full depth is what made heavy midgames overrun the clock (42 legal
        # moves -> depth 5 took ~55s, so in practice only depth 4 completed and a
        # worse move was played). Instead, once we have a shallow ranking we
        # restrict the deeper, expensive passes to the top-K moves from the
        # previous iteration. The good move is virtually always already near the
        # top of the shallow ranking (verified: the key defensive move sat at
        # rank 2-4 of 42 at depth 2-3), so this loses almost nothing in quality
        # while letting the full target depth actually finish in time.
        prev_order: list[Move] | None = None
        for depth in range(1, self.max_depth + 1):
            shortlist = None
            if prev_order is not None and depth >= 4:
                shortlist = prev_order[: self._ROOT_TOPK]
            try:
                score, move, prev_order = self._root(board, side, depth, shortlist)
            except _Timeout:
                break  # discard interrupted depth, keep previous complete one
            if move is not None:
                best_move, best_score = move, score
            self.stats.depth_reached = depth
            # Stop early on a forced mate.
            if abs(best_score) > MATE - 1000:
                break
        return best_move, best_score

    # ------------------------------------------------------------- internals
    def _check_time(self) -> None:
        if self._deadline is not None and time.time() > self._deadline:
            raise _Timeout()

    def _root(
        self,
        board: Board,
        side: int,
        depth: int,
        shortlist: list[Move] | None = None,
    ) -> tuple[int, Move | None, list[Move]]:
        alpha, beta = -MATE * 2, MATE * 2
        if shortlist is not None:
            # Deep pass: only search the top-K moves carried over from the
            # previous (shallower) iteration, in that order.
            moves = list(shortlist)
        else:
            moves = self._ordered_moves(board, side, depth)
        # Exclude moves that would cause a 3rd repetition (passed from caller).
        if self._forbidden:
            moves = [m for m in moves if m.as_tuple() not in self._forbidden]
        if not moves:
            return -MATE, None, []
        best_move = moves[0]
        best_score = -MATE * 2
        scored: list[tuple[int, Move]] = []
        for mv in moves:
            board.make(mv)
            try:
                score = -self._negamax(board, -side, depth - 1, -beta, -alpha)
                # Root mate/material-risk penalties:
                # Do not allow a root move that lets the opponent force an
                # immediate checkmate/net, even if shallow eval likes activity.
                score -= self._root_mate_risk(board, side)
                score -= self._root_material_risk(board, side)
            finally:
                board.unmake()
            scored.append((score, mv))
            if score > best_score:
                best_score = score
                best_move = mv
            if best_score > alpha:
                alpha = best_score

        # Move ordering for the next (deeper) iteration: best-scored first.
        scored.sort(key=lambda sm: sm[0], reverse=True)
        order = [mv for _, mv in scored]

        # --- Root-only blunder guard --------------------------------------
        # The chosen move is fully searched, but the horizon can still miss that
        # it leaves a valuable piece (chariot/cannon/horse/elephant) hanging
        # just beyond the last ply. Cheaply (root only, NOT per leaf) check
        # whether the best move lets the opponent win material by SEE; if so and
        # a near-equal alternative does not, prefer the safe alternative.
        guarded = self._blunder_guard(board, side, scored, best_score)
        if guarded is not None:
            return best_score, guarded, order
        return best_score, best_move, order

    def _root_material_risk(self, board: Board, side: int) -> int:
        """Penalty for material the opponent can immediately win after a root move.

        This is intentionally root-only. It is stronger than the old max-only
        blunder guard because real losses often come from several loose pieces
        or from one exchange that leaves another major piece hanging.
        """
        enemy = -side
        worst = 0
        total = 0
        count = 0

        for omv in board.generate_pseudo(enemy):
            if omv.captured not in ("C", "P", "M", "S", "G"):
                continue
            gain = see(board, omv)
            if gain <= 0:
                continue
            worst = max(worst, gain)
            total += gain
            count += 1

        if worst < 250:
            return 0

        # Worst immediate win matters most, but multiple loose pieces also matter.
        return min(1200, worst + total // 4 + count * 40)

    def _blunder_guard(
        self, board: Board, side: int, scored: list[tuple[int, Move]], best_score: int
    ) -> Move | None:
        """Return a safer root move if the top move leaves major material loose."""
        if len(scored) < 2:
            return None

        scored.sort(key=lambda sm: sm[0], reverse=True)
        top_score, top_move = scored[0]

        def risk_after(mv: Move) -> int:
            board.make(mv)
            try:
                return self._root_material_risk(board, side)
            finally:
                board.unmake()

        top_risk = risk_after(top_move)
        if top_risk < 250:
            return None

        margin = max(180, min(1000, top_risk))
        for sc, mv in scored[1:12]:
            if top_score - sc > margin:
                break
            if risk_after(mv) + 180 < top_risk:
                return mv

        return None


    def _negamax(self, board: Board, side: int, depth: int, alpha: int, beta: int) -> int:
        self.stats.nodes += 1
        self._check_time()

        alpha_orig = alpha
        key = zobrist_hash(board)
        entry = self.tt.get(key)
        tt_move: Move | None = None
        if entry is not None and entry.depth >= depth:
            self.stats.tt_hits += 1
            if entry.flag == EXACT:
                return entry.value
            if entry.flag == LOWER and entry.value > alpha:
                alpha = entry.value
            elif entry.flag == UPPER and entry.value < beta:
                beta = entry.value
            if alpha >= beta:
                return entry.value
        if entry is not None:
            tt_move = entry.best

        if depth == 0:
            return self._quiescence(board, side, alpha, beta)

        moves = self._ordered_moves(board, side, depth, tt_move)
        if not moves:
            # No legal move: in Janggi a side with no move loses (mate/stalemate).
            return -MATE + (self.max_depth - depth)

        best_score = -MATE * 2
        best_move: Move | None = None
        # Selective extension (check-only): search one ply deeper when the side to
        # move is in check. Checks are forced and tactically sharp — this is where
        # a fixed depth most often misses a mate net — and crucially they are
        # RARE in normal play, so this almost never costs base depth in quiet
        # midgames (unlike a recapture extension, which fired on routine trades
        # and dropped a whole ply; that version was tried and reverted). Bounded
        # by _ext_budget so a long checking sequence can't explode the tree.
        in_check = board.in_check(side)
        extend = 1 if (self._ext_budget > 0 and in_check) else 0
        if extend:
            self._ext_budget -= 1
        for move_index, mv in enumerate(moves):
            board.make(mv)
            # Late Move Reduction (LMR). Moves ordered late by the move orderer
            # are unlikely to be best, so search them one ply shallower first as a
            # cheap probe. Only reduce QUIET, non-checking moves that are well
            # down the list, and never when extending or near the leaves. If the
            # reduced search unexpectedly beats alpha, re-search at full depth so
            # nothing good is missed. This is what makes deeper iterations
            # tractable without dropping tactics.
            reduce = 0
            if (extend == 0 and depth >= 3 and move_index >= 3
                    and mv.captured is None and not board.in_check(-side)):
                reduce = 1
            score = -self._negamax(board, -side, depth - 1 + extend - reduce, -beta, -alpha)
            if reduce and score > alpha:
                # Promising despite the reduction — verify at full depth.
                score = -self._negamax(board, -side, depth - 1 + extend, -beta, -alpha)
            board.unmake()
            if score > best_score:
                best_score = score
                best_move = mv
            if best_score > alpha:
                alpha = best_score
            if alpha >= beta:
                if mv.captured is None:
                    mt = mv.as_tuple()
                    kl = self._killers.setdefault(depth, [])
                    if mt not in kl:
                        kl.insert(0, mt)
                        del kl[2:]  # keep at most two killers per depth
                    hkey = (side,) + mt
                    self._history[hkey] = self._history.get(hkey, 0) + depth * depth
                break  # beta cutoff
        if extend:
            self._ext_budget += 1

        flag = EXACT
        if best_score <= alpha_orig:
            flag = UPPER
        elif best_score >= beta:
            flag = LOWER
        self.tt[key] = TTEntry(depth, best_score, flag, best_move)
        return best_score

    def _quiescence(self, board: Board, side: int, alpha: int, beta: int) -> int:
        """Search only captures until the position is quiet."""
        self.stats.qnodes += 1
        self._check_time()
        stand_pat = evaluate(board, include_mobility=False) * side
        if stand_pat >= beta:
            return beta
        if stand_pat > alpha:
            alpha = stand_pat

        # Use pseudo-legal captures for speed; verify legality lazily via the
        # general-capture guard. A move that leaves our own general capturable
        # will simply be refuted on the opponent's reply (they capture the
        # general -> huge negative), so quiescence stays sound without the
        # expensive full legal_moves() filter.
        captures = [mv for mv in board.generate_pseudo(side) if mv.captured is not None]
        captures.sort(key=lambda m: self._mvv_lva(board, m), reverse=True)
        for mv in captures:
            # Capturing the enemy general ends the game immediately.
            if mv.captured == "K":
                return MATE
            # Skip captures that lose material after recaptures (SEE < 0). This
            # is what stops the engine from "winning" a cannon with a chariot
            # that then gets recaptured. Equal/winning captures still searched.
            if see(board, mv) < 0:
                continue
            board.make(mv)
            score = -self._quiescence(board, -side, -beta, -alpha)
            board.unmake()
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score
        return alpha

    # ------------------------------------------------------- move ordering
    def _mvv_lva(self, board: Board, mv: Move) -> int:
        victim = PIECE_VALUE.get(mv.captured, 0) if mv.captured else 0
        attacker_piece = board.grid[mv.fr][mv.fc]
        attacker = PIECE_VALUE.get(attacker_piece[0], 0) if attacker_piece else 0
        return victim * 10 - attacker

    def _ordered_moves(
        self, board: Board, side: int, depth: int, tt_move: Move | None = None
    ) -> list[Move]:
        moves = board.legal_moves(side)
        tt_tuple = tt_move.as_tuple() if tt_move is not None else None
        killers = self._killers.get(depth, ())

        def key(m: Move):
            mt = m.as_tuple()
            # 1) Transposition-table best move first.
            is_tt = 1 if (tt_tuple is not None and mt == tt_tuple) else 0
            if m.captured is not None:
                # 2) Winning/even captures (SEE) above quiet moves.
                see_val = see(board, m)
                killer = 0
                hist = 0
            else:
                see_val = 0
                # 3) Killer moves (caused a cutoff at this depth elsewhere).
                killer = 1 if mt in killers else 0
                # 4) History heuristic for the rest.
                hist = self._history.get((side,) + mt, 0)
            return (is_tt, see_val, killer, hist, self._mvv_lva(board, m))

        moves.sort(key=key, reverse=True)
        return moves


class _Timeout(Exception):
    pass
