"""Search: the part that actually makes the engine strong.

The public entry point is ``Engine.search(board, side, ...)``. Where the work
happens depends on how the engine was built:

* Normally the compiled core in ``_core.pyx`` runs the entire search, root
  included -- iterative deepening with aspiration windows, a transposition
  table, principal variation search, null-move / futility / late-move pruning,
  late move reductions, check extensions, killers, counter-moves, history,
  quiescence with SEE and delta pruning, and repetition detection.
* If the extensions are not built, or a custom Python ``evaluator`` is supplied
  (the neural-network bridge, which the compiled core cannot call into), the
  pure-Python implementation below runs instead. It is the same idea at a
  fraction of the speed.

``SearchOptions`` switches individual techniques off so a change can be
measured against the same engine rather than against an intuition -- see
``janggi/match.py``.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .board import Board, Move, HAN, CHO, ROWS, COLS, PIECE_VALUE
from .evaluate import evaluate
from .see import see

try:
    from .board import DISABLE_ACCEL as _NO_ACCEL
    if _NO_ACCEL:
        raise ImportError("accelerators disabled by JANGGI_NO_ACCEL")
    from janggi._core import core_reset, core_negamax, core_search, core_stats
    _HAVE_CORE = True
except Exception:
    _HAVE_CORE = False

_CODE_PIECE = {1: "C", 2: "P", 3: "M", 4: "S", 5: "J", 6: "K", 7: "G"}

MATE = 1_000_000

# Transposition table entry flags.
EXACT, LOWER, UPPER = 0, 1, 2


# ----------------------------------------------------------------- Zobrist
# The table itself now lives in board.py, which maintains the key incrementally
# through make/unmake and every square write. These names are re-exported so
# existing callers (repetition, book, tests) keep working unchanged.
from .board import _PIECE_INDEX, _ZOBRIST, _build_zobrist  # noqa: F401


def zobrist_hash(board: Board) -> int:
    """The board's Zobrist key.

    O(1): boards keep the key up to date on every mutation instead of rescanning
    all 90 squares per search node, which is what this used to cost.
    """
    return board.zobrist()


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
    #: Principal variation as (fr, fc, tr, tc) tuples, best line first.
    pv: list[tuple[int, int, int, int]] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def total_nodes(self) -> int:
        return self.nodes + self.qnodes

    def nps(self) -> float:
        return self.total_nodes / self.elapsed if self.elapsed > 0 else 0.0


@dataclass(frozen=True)
class SearchOptions:
    """Individually switchable search techniques.

    Every one of these is on by default; they exist as flags so a change can be
    measured against the same engine with the feature disabled
    (``python -m janggi.match --a "" --b "nmp=0"``) instead of against a guess.
    """

    use_tt: bool = True             # transposition table
    use_lmr: bool = True            # late move reductions
    use_ext: bool = True            # check extensions
    use_nmp: bool = True            # null-move pruning
    use_pvs: bool = True            # principal variation search
    use_futility: bool = True       # futility + reverse futility
    use_lmp: bool = True            # late move pruning
    use_aspiration: bool = True     # aspiration windows at the root
    use_repetition: bool = True     # repetition detection inside the search
    ext_budget: int = 3             # extra plies a single branch may spend
    node_limit: int = 0             # 0 = unlimited; used for reproducible A/B
    eval_version: int = 2           # 1 = original evaluator, 2 = Janggi-aware
    use_improving: bool = True      # prune harder when the side to move is worse off
    use_nmp_scale: bool = True      # scale the null-move reduction by the eval margin
    use_hist_lmr: bool = True       # scale the late-move reduction by move history

    _ALIASES = {
        "tt": "use_tt", "lmr": "use_lmr", "ext": "use_ext", "nmp": "use_nmp",
        "pvs": "use_pvs", "futility": "use_futility", "fut": "use_futility",
        "lmp": "use_lmp", "asp": "use_aspiration", "aspiration": "use_aspiration",
        "rep": "use_repetition", "repetition": "use_repetition",
        "extbudget": "ext_budget", "nodes": "node_limit",
        "eval": "eval_version",
        "imp": "use_improving", "improving": "use_improving",
        "nmpscale": "use_nmp_scale", "histlmr": "use_hist_lmr",
    }

    @classmethod
    def parse(cls, spec: str) -> "SearchOptions":
        """Build options from a "nmp=0,lmr=0,nodes=200000" style string."""
        values: dict[str, int | bool] = {}
        for chunk in (spec or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError(f"option {chunk!r} must look like key=value")
            key, _, raw = chunk.partition("=")
            key = key.strip().lower()
            field_name = cls._ALIASES.get(key, key)
            if field_name not in cls.__dataclass_fields__:
                raise ValueError(f"unknown search option {key!r}")
            if field_name in ("ext_budget", "node_limit", "eval_version"):
                values[field_name] = int(raw)
            else:
                values[field_name] = raw.strip() not in ("0", "false", "False", "no")
        return cls(**values)


class Engine:
    EXT_BUDGET = 3  # check-extension ply budget per branch (0 disables)

    def __init__(
        self,
        max_depth: int = 6,
        time_limit: float | None = None,
        evaluator: Callable[[Board], int] | None = None,
        options: "SearchOptions | None" = None,
    ) -> None:
        """Create a search engine.

        ``evaluator`` is an optional static evaluator whose score must use the
        engine convention (positive = HAN, negative = CHO).  A custom Python
        evaluator disables the Cython search core because the compiled core
        cannot call it; this is the opt-in bridge used by ``nn_eval``.
        """
        self.max_depth = max_depth
        self.time_limit = time_limit
        self.options = options or SearchOptions()
        self._evaluator = evaluator
        self._use_core = _HAVE_CORE and evaluator is None
        self.tt: dict[int, TTEntry] = {}
        self.stats = SearchStats()
        self._deadline: float | None = None
        self._forbidden: set[tuple[int, int, int, int]] = set()
        # Killer moves: two per ply that caused a beta cutoff (quiet moves only).
        self._killers: dict[int, list[tuple[int, int, int, int]]] = {}
        # History heuristic: cumulative cutoff score per (side, move) tuple.
        self._history: dict[tuple[int, int, int, int, int], int] = {}
        # Moves played in the game before this search; fixed for its duration.
        self._game_ply = 0

    # --------------------------------------------------------- public API
    def search(
        self,
        board: Board,
        side: int,
        forbidden_moves: set[tuple[int, int, int, int]] | None = None,
        history_hashes: list[int] | None = None,
        game_ply: int | None = None,
    ) -> tuple[Move | None, int]:
        """Return (best_move, score_from_side's_perspective).

        forbidden_moves: root moves (as (fr,fc,tr,tc) tuples) the engine must
            not choose — used to exclude moves that would cause a 3rd repetition.
        history_hashes: Zobrist keys of game positions since the last capture,
            so the search can recognise a repetition instead of chasing one.
        game_ply: moves played in the game so far. Only the endgame score-lock
            uses it; it defaults to the board's own history length, which is
            wrong for a board rebuilt from a posted position (its history is
            empty), so callers that know better should pass it.
        """
        self._forbidden = forbidden_moves or set()
        self.stats = SearchStats()
        self._killers = {}
        self._history = {}
        opts = self.options
        if game_ply is None:
            game_ply = len(board._history)
        started = time.time()
        # Total extra plies the search may spend on check-extensions in any one
        # branch. Caps tree growth so sharp positions go deeper without the whole
        # search blowing up.
        self._ext_budget = opts.ext_budget

        if self._use_core:
            move, score = self._core_search(board, side, game_ply, history_hashes)
            self.stats.elapsed = time.time() - started
            return move, score

        self._deadline = (started + self.time_limit) if self.time_limit else None
        self._game_ply = game_ply
        best_move: Move | None = None
        best_score = 0
        # Iterative deepening. A depth is only accepted once it COMPLETES; if the
        # time limit interrupts a depth partway through, that partial result is
        # discarded and we keep the last fully-searched depth.
        for depth in range(1, self.max_depth + 1):
            try:
                score, move, _order = self._root(board, side, depth)
            except _Timeout:
                break  # discard interrupted depth, keep previous complete one
            if move is not None:
                best_move, best_score = move, score
            self.stats.depth_reached = depth
            # Stop early on a forced mate.
            if abs(best_score) > MATE - 1000:
                break
        self.stats.elapsed = time.time() - started
        if best_move is not None:
            self.stats.pv = [best_move.as_tuple()]
        return best_move, best_score

    def _core_search(
        self,
        board: Board,
        side: int,
        game_ply: int,
        history_hashes: list[int] | None,
    ) -> tuple[Move | None, int]:
        """Drive the compiled search, which owns the root as well.

        The root used to live here and call into the core once per root move,
        which meant no aspiration window, no principal variation, and a
        hand-rolled "only search the top 10 root moves at depth >= 4" cut that
        could discard the best move outright. All of that now happens in C.
        """
        opts = self.options
        core_reset(
            self.max_depth, opts.ext_budget,
            1 if opts.use_tt else 0,
            1 if opts.use_lmr else 0,
            1 if opts.use_ext else 0,
            1 if opts.use_nmp else 0,
            1 if opts.use_pvs else 0,
            1 if opts.use_futility else 0,
            1 if opts.use_lmp else 0,
            1 if opts.use_aspiration else 0,
            1 if opts.use_repetition else 0,
            opts.node_limit,
            opts.eval_version,
            1 if opts.use_improving else 0,
            1 if opts.use_nmp_scale else 0,
            1 if opts.use_hist_lmr else 0,
        )
        deadline = (time.time() + self.time_limit) if self.time_limit else 0.0
        frm, to, cap, score, depth, pv = core_search(
            board._pc, board._sd,
            1 if side == HAN else 2,
            self.max_depth, deadline, game_ply,
            list(history_hashes or ()),
            sorted(self._forbidden),
        )
        cn, cq, ct = core_stats()
        self.stats.nodes = cn
        self.stats.qnodes = cq
        self.stats.tt_hits = ct
        self.stats.depth_reached = depth
        self.stats.pv = [
            (f // COLS, f % COLS, t // COLS, t % COLS) for f, t in pv
        ]
        if frm < 0:
            return None, score
        return (
            Move(frm // COLS, frm % COLS, to // COLS, to % COLS,
                 _CODE_PIECE[cap] if cap else None),
            score,
        )

    # ------------------------------------------------------------- internals
    def _check_time(self) -> None:
        if self._deadline is not None and time.time() > self._deadline:
            raise _Timeout()

    def _static_evaluate(self, board: Board) -> int:
        """Return a fast leaf score using the configured sign convention."""
        if self._evaluator is not None:
            return int(self._evaluator(board))
        return evaluate(board, include_mobility=False, ply=self._game_ply)

    def _root(
        self,
        board: Board,
        side: int,
        depth: int,
    ) -> tuple[int, Move | None, list[Move]]:
        alpha, beta = -MATE * 2, MATE * 2
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
                if self._use_core:
                    try:
                        score = -core_negamax(
                            board._pc, board._sd,
                            1 if -side == HAN else 2,
                            depth - 1, -beta, -alpha,
                            self._deadline or 0.0,
                            len(board._history),
                        )
                    except TimeoutError:
                        raise _Timeout()
                else:
                    score = -self._negamax(board, -side, depth - 1, -beta, -alpha, 1)
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

        # --- Root-only tactical guards ------------------------------------
        # A proven forced win needs no second-guessing: the material guard below
        # would happily swap a mate for a move that leaves less hanging.
        if best_score > MATE - 4096:
            return best_score, best_move, order

        # First avoid a top move that allows immediate one-move checkmate.
        # This guard is intentionally post-root and best-move-first only, so it
        # does not multiply legal_moves() across every search node.
        mate_guarded = self._mate_threat_guard(board, side, scored)
        if mate_guarded is not None:
            return best_score, mate_guarded, order

        # Then avoid a top move that leaves major material loose.
        guarded = self._blunder_guard(board, side, scored, best_score)
        if guarded is not None:
            return best_score, guarded, order
        return best_score, best_move, order

    def _allows_immediate_mate(self, board: Board, side: int) -> bool:
        """Return True if the opponent has an immediate legal mate.

        Fast v2-lite:
        - scan opponent pseudo moves
        - verify opponent move legality after make
        - only call legal_moves(side) when the move actually gives check
        - scan all checking moves; a low cap can miss chariot mate nets
        """
        enemy = -side
        checked_checks = 0

        for omv in board.generate_pseudo(enemy):
            board.make(omv)
            try:
                if board.in_check(enemy):
                    continue

                if not board.in_check(side):
                    continue

                checked_checks += 1
                if not board.legal_moves(side):
                    return True

                # No low cap here. This is root-only and legal_moves() is only
                # called for actual checking moves. A low cap can miss mate nets.
            finally:
                board.unmake()

        return False

    def _mate_threat_guard(self, board: Board, side: int, scored: list[tuple[int, Move]]) -> Move | None:
        """Replace the top root move only if it allows immediate mate."""
        if len(scored) < 2:
            return None

        scored.sort(key=lambda sm: sm[0], reverse=True)
        top_score, top_move = scored[0]

        def unsafe_after(mv: Move) -> bool:
            board.make(mv)
            try:
                return self._allows_immediate_mate(board, side)
            finally:
                board.unmake()

        if not unsafe_after(top_move):
            return None

        # If the top move allows immediate mate, reject it regardless of
        # eval margin. Being checkmated is worse than any static score.
        for sc, mv in scored[1:16]:
            if not unsafe_after(mv):
                return mv

        return None




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

        # Do not ignore "small" official-score losses.
        # A cannon lost to a horse is only +200 in engine units, but it is still
        # a real 2-point loss on the Janggi score sheet. Repeated 2~3 point leaks
        # were enough to lose games even without a single huge blunder.
        if worst < 180:
            return 0

        # Worst immediate win matters most, but multiple loose pieces also matter.
        # Make repeated medium leaks visible at root.
        return min(1400, worst + total // 3 + count * 60)

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
        if top_risk < 180:
            return None

        margin = max(150, min(1000, top_risk))
        for sc, mv in scored[1:12]:
            if top_score - sc > margin:
                break
            if risk_after(mv) + 120 < top_risk:
                return mv

        return None


    def _negamax(self, board: Board, side: int, depth: int, alpha: int,
                 beta: int, ply: int = 0) -> int:
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
            return self._quiescence(board, side, alpha, beta, ply=ply)

        moves = self._ordered_moves(board, side, depth, tt_move)
        if not moves:
            # No legal move: in Janggi a side with no move loses (mate/stalemate).
            # Scored by distance from the root, so a mate in one beats a mate in
            # three instead of tying with it.
            return -MATE + ply

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
            try:
                # Late Move Reduction (LMR). Moves ordered late by the move
                # orderer are searched one ply shallower as a cheap probe.
                reduce = 0
                if (extend == 0 and depth >= 3 and move_index >= 3
                        and mv.captured is None and not board.in_check(-side)):
                    reduce = 1
                score = -self._negamax(
                    board, -side, depth - 1 + extend - reduce, -beta, -alpha, ply + 1
                )
                if reduce and score > alpha:
                    # Promising despite the reduction: verify at full depth.
                    score = -self._negamax(
                        board, -side, depth - 1 + extend, -beta, -alpha, ply + 1
                    )
            except _Timeout:
                # Restore the check-extension token owned by this node. Normal
                # completion restores it after the move loop below.
                if extend:
                    self._ext_budget += 1
                raise
            finally:
                # Deadline exceptions can originate at any descendant. Always
                # restore this ply before propagating them to iterative search.
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

    def _quiescence(
        self, board: Board, side: int, alpha: int, beta: int,
        ply: int = 0, qply: int = 0
    ) -> int:
        """Resolve checks and captures until the position is legally quiet."""
        self.stats.qnodes += 1
        self._check_time()
        in_check = board.in_check(side)

        # Stand-pat means choosing to make no move. It is only valid outside
        # check; while checked every legal evasion (including quiet moves) must
        # be searched. The cap prevents pathological perpetual-check cycles.
        if qply >= 32:
            return self._static_evaluate(board) * side
        if not in_check:
            stand_pat = self._static_evaluate(board) * side
            if stand_pat >= beta:
                return beta
            if stand_pat > alpha:
                alpha = stand_pat

        candidates = board.generate_pseudo(side)
        if not in_check:
            candidates = [mv for mv in candidates if mv.captured is not None]
        candidates.sort(
            key=lambda m: (m.captured is not None, self._mvv_lva(board, m)),
            reverse=True,
        )

        legal_found = False
        for mv in candidates:
            # Skip captures that lose material after recaptures (SEE < 0). This
            # is what stops the engine from "winning" a cannon with a chariot
            # that then gets recaptured. In check, never prune a forced evasion.
            if not in_check and see(board, mv) < 0:
                continue
            board.make(mv)
            try:
                # Pseudo move generation is fast, but a pinned capture or
                # evasion that leaves our general attacked is not a legal move.
                if board.in_check(side):
                    continue
                legal_found = True
                if mv.captured == "K":
                    return MATE - (ply + qply)
                score = -self._quiescence(
                    board, -side, -beta, -alpha, ply=ply, qply=qply + 1
                )
            finally:
                board.unmake()
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score
        if in_check and not legal_found:
            return -MATE + ply + qply
        return alpha

    # ------------------------------------------------------- move ordering
    def _mvv_lva(self, board: Board, mv: Move) -> int:
        victim = PIECE_VALUE.get(mv.captured, 0) if mv.captured else 0
        attacker_piece = board._g[mv.fr][mv.fc]
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
