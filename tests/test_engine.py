"""Tests for the Janggi engine.

The most important tests verify the rules engine, because both search and
evaluation trust it completely. We test piece movement in isolation, the
facing-generals rule, check detection, and a small perft (move-count) on the
opening position.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi.board import Board, Move, HAN, CHO  # noqa: E402
from janggi.search import Engine, zobrist_hash  # noqa: E402
from janggi.evaluate import evaluate  # noqa: E402
import janggi.search as search_module  # noqa: E402


def empty_board() -> Board:
    return Board()  # grid all None, side_to_move = CHO


# --------------------------------------------------------------- piece rules
def test_chariot_slides_and_stops_at_capture():
    b = empty_board()
    b.grid[5][4] = ("C", CHO)
    b.grid[5][7] = ("J", HAN)   # enemy to the right
    b.grid[5][1] = ("J", CHO)   # friendly to the left
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (5, 4)]
    targets = {(m.tr, m.tc) for m in moves}
    # Right: 5,6 then capture at 5,7 (not beyond). Left: 5,3 5,2 (blocked by friend at 5,1).
    assert (5, 7) in targets
    assert (5, 8) not in targets
    assert (5, 1) not in targets  # cannot capture own piece
    assert (5, 2) in targets


def test_horse_leg_block():
    b = empty_board()
    b.grid[5][4] = ("M", CHO)
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (5, 4)]
    assert len(moves) == 8  # open board, all eight horse moves
    b.grid[4][4] = ("J", CHO)  # block the upward leg
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (5, 4)]
    targets = {(m.tr, m.tc) for m in moves}
    assert (3, 3) not in targets and (3, 5) not in targets  # both blocked legs gone


def test_cannon_needs_single_screen():
    b = empty_board()
    b.grid[5][0] = ("P", CHO)
    b.grid[5][3] = ("J", CHO)   # screen
    b.grid[5][6] = ("J", HAN)   # target beyond screen
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (5, 0)]
    targets = {(m.tr, m.tc) for m in moves}
    assert (5, 6) in targets        # can capture over one screen
    assert (5, 4) in targets        # can land on empty beyond screen
    assert (5, 1) not in targets    # cannot move before a screen


def test_cannon_cannot_capture_cannon():
    b = empty_board()
    b.grid[5][0] = ("P", CHO)
    b.grid[5][3] = ("J", CHO)
    b.grid[5][6] = ("P", HAN)   # enemy cannon beyond screen
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (5, 0)]
    targets = {(m.tr, m.tc) for m in moves}
    assert (5, 6) not in targets


def test_soldier_no_backward():
    b = empty_board()
    b.grid[5][4] = ("J", CHO)   # CHO soldiers move up (decreasing row)
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (5, 4)]
    targets = {(m.tr, m.tc) for m in moves}
    assert (4, 4) in targets     # forward
    assert (5, 3) in targets and (5, 5) in targets  # sideways
    assert (6, 4) not in targets  # never backward


def test_general_confined_to_palace():
    b = empty_board()
    b.grid[8][4] = ("K", CHO)
    moves = [m for m in b.generate_pseudo(CHO) if (m.fr, m.fc) == (8, 4)]
    targets = {(m.tr, m.tc) for m in moves}
    for t in targets:
        assert t[1] in (3, 4, 5) and t[0] in (7, 8, 9)


def test_facing_generals_detected_but_legal():
    # In Korean janggi, facing generals ("대궁"/빅장) is a LEGAL position, not
    # check. generals_face() still detects it (for draw handling), but it must
    # not make a move illegal or count as check.
    b = empty_board()
    b.grid[1][4] = ("K", HAN)
    b.grid[8][4] = ("K", CHO)
    assert b.generals_face() is True
    assert b.in_check(CHO) is False   # facing is NOT check
    assert b.in_check(HAN) is False
    b.grid[5][4] = ("J", CHO)         # something between them
    assert b.generals_face() is False


def test_piece_pinned_only_by_real_check_not_facing():
    # A cannon between the two generals on the same file may freely move; the
    # only true restriction is a piece that actually exposes its general to a
    # capturing attack (not the facing-generals rule).
    b = empty_board()
    b.grid[7][4] = ("K", CHO)
    b.grid[1][4] = ("K", HAN)
    b.grid[3][4] = ("P", CHO)   # cho cannon between the generals on file 4
    legal = [m for m in b.legal_moves(CHO) if (m.fr, m.fc) == (3, 4)]
    # The cannon should have legal moves (sliding away is fine; facing is legal).
    assert len(legal) > 0


def test_check_detection():
    b = empty_board()
    b.grid[8][4] = ("K", CHO)
    b.grid[8][0] = ("C", HAN)   # chariot rakes the rank onto the general
    assert b.in_check(CHO) is True


# ------------------------------------------------------------------- perft
def perft(board: Board, side: int, depth: int) -> int:
    if depth == 0:
        return 1
    total = 0
    for mv in board.legal_moves(side):
        board.make(mv)
        total += perft(board, -side, depth - 1)
        board.unmake()
    return total


def test_perft_opening_depth1():
    b = Board.standard()
    # Cho moves first. Depth-1 = number of legal opening moves for Cho.
    n = perft(b, CHO, 1)
    # Sanity range: a standard opening has on the order of 30 legal moves.
    assert 20 <= n <= 40, f"unexpected opening move count: {n}"


def test_make_unmake_restores_position():
    b = Board.standard()
    before = [row[:] for row in b.grid]
    moves = b.legal_moves(CHO)
    for mv in moves[:10]:
        b.make(mv)
        b.unmake()
    after = b.grid
    assert before == after


def test_zobrist_changes_on_move_and_restores():
    b = Board.standard()
    h0 = zobrist_hash(b)
    mv = b.legal_moves(CHO)[0]
    b.make(mv)
    h1 = zobrist_hash(b)
    assert h0 != h1
    b.unmake()
    assert zobrist_hash(b) == h0


# ------------------------------------------------------------------ engine
def test_engine_finds_free_capture():
    b = empty_board()
    b.grid[8][4] = ("K", CHO)
    b.grid[1][4] = ("K", HAN)
    b.grid[3][4] = ("J", HAN)   # blocks the central file so general isn't hanging
    b.grid[5][4] = ("C", CHO)   # chariot
    b.grid[5][7] = ("J", HAN)   # hanging soldier on same rank
    eng = Engine(max_depth=3)
    move, score = eng.search(b, CHO)
    assert move is not None
    # Best move should grab a free piece (the rank soldier or the file soldier).
    assert move.captured == "J"


def test_engine_avoids_immediate_loss_of_general():
    b = empty_board()
    b.grid[8][4] = ("K", CHO)
    b.grid[1][4] = ("K", HAN)
    b.grid[7][3] = ("G", CHO)
    eng = Engine(max_depth=3)
    move, score = eng.search(b, CHO)
    assert move is not None  # has a legal reply, does not crash


# ------------------------------------------------------------------- scoring
def test_scoring_initial_position():
    from janggi.score import side_score, judge, HAN_BONUS
    b = Board.standard()
    # Each side: 2C(26) + 2P(14) + 2M(10) + 2S(6) + 2G(6) + 5J(10) = 72
    cho = side_score(b, CHO)
    han = side_score(b, HAN)
    assert cho == 72
    assert han == 72 + HAN_BONUS
    result = judge(b)
    assert result["winner"] == "han"  # Han wins ties via the 1.5 bonus
    assert abs(result["margin"] - HAN_BONUS) < 1e-9


def test_scoring_after_capture():
    from janggi.score import side_score
    b = Board.standard()
    # Remove one Cho chariot (worth 13 points).
    b.grid[9][0] = None
    assert side_score(b, CHO) == 72 - 13


def test_elephant_second_leg_block():
    """Regression: elephant must be blocked if the SECOND intermediate square
    (the first diagonal step) is occupied, not only the first orthogonal step."""
    b = empty_board()
    b.grid[3][1] = ("S", HAN)
    open_targets = {(m.tr, m.tc) for m in b.generate_pseudo(HAN) if (m.fr, m.fc) == (3, 1)}
    assert (6, 3) in open_targets  # legal on an open board

    # Block only the second leg (first diagonal step) of the (3,1)->(6,3) move.
    b.grid[5][2] = ("P", HAN)
    blocked = {(m.tr, m.tc) for m in b.generate_pseudo(HAN) if (m.fr, m.fc) == (3, 1)}
    assert (6, 3) not in blocked


def test_elephant_first_leg_block():
    b = empty_board()
    b.grid[3][1] = ("S", HAN)
    b.grid[4][1] = ("P", HAN)  # block the orthogonal step
    blocked = {(m.tr, m.tc) for m in b.generate_pseudo(HAN) if (m.fr, m.fc) == (3, 1)}
    assert (6, 3) not in blocked


def test_elephant_central_eight_directions():
    b = empty_board()
    b.grid[4][4] = ("S", HAN)
    targets = {(m.tr, m.tc) for m in b.generate_pseudo(HAN) if (m.fr, m.fc) == (4, 4)}
    expected = {(1, 2), (1, 6), (2, 1), (2, 7), (6, 1), (6, 7), (7, 2), (7, 6)}
    assert targets == expected


# --------------------------------------------------------------------- gibo
def test_gibo_roundtrip_and_validate():
    from janggi.gibo import Gibo
    from janggi.board import Move
    b = Board.standard()
    g = Gibo(cho_formation="msm_s", han_formation="msm_s")
    # Play three legal moves, alternating sides, recording each.
    side = CHO
    for _ in range(3):
        mv = b.legal_moves(side)[0]
        g.add_move(mv, side)
        b.make(mv)
        side = -side
    text = g.to_json()
    g2 = Gibo.from_json(text)
    assert g2.moves == g.moves
    ok, msg = g2.validate()
    assert ok, msg


def test_gibo_detects_illegal_move():
    from janggi.gibo import Gibo
    g = Gibo(cho_formation="msm_s", han_formation="msm_s")
    # An obviously illegal move (a piece teleporting across the board).
    g.moves = [{"fr": 9, "fc": 0, "tr": 0, "tc": 8, "captured": None, "side": "cho"}]
    ok, msg = g.validate()
    assert not ok


# ------------------------------------------------------------- repetition
def test_repetition_blocks_third_occurrence():
    from janggi.repetition import RepetitionTracker
    b = empty_board()
    b.grid[8][4] = ("K", CHO)
    b.grid[1][4] = ("K", HAN)
    b.grid[5][4] = ("C", CHO)   # a chariot to shuffle back and forth
    b.grid[5][6] = ("C", HAN)

    tracker = RepetitionTracker()
    tracker.record(b)  # position seen once (count=1)

    # Move the chariot 5,4 -> 5,3 and back twice to build repetitions of the
    # starting position.
    def mv(fr, fc, tr, tc):
        from janggi.board import Move
        return Move(fr, fc, tr, tc, b.grid[tr][tc][0] if b.grid[tr][tc] else None)

    # 1st there-and-back -> back at start (2nd occurrence)
    b.make(mv(5, 4, 5, 3)); tracker.record(b)
    b.make(mv(5, 3, 5, 4)); n2 = tracker.record(b)
    assert n2 == 2

    # Now moving 5,4->5,3 again and back would make start occur a 3rd time.
    b.make(mv(5, 4, 5, 3)); tracker.record(b)
    from janggi.board import Move
    back = Move(5, 3, 5, 4, None)
    assert tracker.would_repeat_thrice(b, back) is True


def test_legal_nonrepeating_never_empty_when_moves_exist():
    from janggi.repetition import RepetitionTracker
    b = Board.standard()
    tracker = RepetitionTracker()
    tracker.record(b)
    moves = tracker.legal_nonrepeating(b, CHO)
    assert len(moves) > 0


# ----------------------------------------------------------------------- SEE
def test_see_free_capture():
    from janggi.see import see
    from janggi.board import Move
    b = empty_board()
    b.grid[9][4] = ("K", CHO); b.grid[0][4] = ("K", HAN)
    b.grid[5][2] = ("C", CHO); b.grid[5][5] = ("J", HAN)  # undefended soldier
    assert see(b, Move(5, 2, 5, 5, "J")) == 200


def test_see_losing_trade():
    from janggi.see import see
    from janggi.board import Move
    b = empty_board()
    b.grid[9][4] = ("K", CHO); b.grid[0][4] = ("K", HAN)
    b.grid[5][2] = ("C", CHO)   # chariot
    b.grid[5][5] = ("P", HAN)   # cannon (target)
    b.grid[5][8] = ("C", HAN)   # chariot recaptures along the rank
    # +cannon(700) - chariot(1300) = -600
    assert see(b, Move(5, 2, 5, 5, "P")) == -600


def test_see_even_trade():
    from janggi.see import see
    from janggi.board import Move
    b = empty_board()
    b.grid[9][4] = ("K", CHO); b.grid[0][4] = ("K", HAN)
    b.grid[5][2] = ("C", CHO); b.grid[5][5] = ("C", HAN); b.grid[5][8] = ("C", HAN)
    assert see(b, Move(5, 2, 5, 5, "C")) == 0


def test_see_noncapture_zero():
    from janggi.see import see
    from janggi.board import Move
    b = Board.standard()
    # A non-capturing soldier push.
    assert see(b, Move(6, 0, 5, 0, None)) == 0


# ------------------------------------------------------------- opening book
def test_book_build_and_lookup(tmp_path):
    from janggi.book import build_book, load_book, book_move
    from janggi.gibo import Gibo
    import json, os

    # Make a tiny gibo: cho plays (6,2)->(6,3), han plays (3,0)->(3,1).
    gibo = {
        "version": 1, "cho_formation": "msm_s", "han_formation": "msm_s",
        "moves": [
            {"fr": 6, "fc": 2, "tr": 6, "tc": 3, "captured": None, "side": "cho"},
            {"fr": 3, "fc": 0, "tr": 3, "tc": 1, "captured": None, "side": "han"},
        ],
        "result": None, "note": "",
    }
    p = tmp_path / "g.json"
    p.write_text(json.dumps(gibo), encoding="utf-8")
    out = tmp_path / "book.json"
    result = build_book([str(p)])
    out.write_text(json.dumps(result), encoding="utf-8")

    book = load_book(str(out))
    assert len(book) >= 2

    b = Board.standard("msm_s", "msm_s")
    mv = book_move(book, b)
    assert mv is not None
    assert mv.as_tuple() == (6, 2, 6, 3)  # cho's recorded opening move


def test_book_move_none_for_unknown_position():
    from janggi.book import book_move
    b = Board.standard()
    # Move a piece into an off-book position, empty book -> None.
    assert book_move({}, b) is None


# --------------------------------------------------------- search correctness
def test_quiescence_in_check_searches_quiet_evasions():
    """A checked side may not use stand-pat; quiet evasions must be searched."""
    b = empty_board()
    b.grid[1][4] = ("K", HAN)
    b.grid[8][4] = ("K", CHO)
    b.grid[5][4] = ("C", HAN)  # file check; CHO can evade without capturing
    b.resync_int_arrays()
    assert b.in_check(CHO)
    assert any(m.captured is None for m in b.legal_moves(CHO))

    eng = Engine(max_depth=1)
    eng._deadline = None
    eng._quiescence(b, CHO, -2 * search_module.MATE, 2 * search_module.MATE)

    # The old qsearch returned after its single stand-pat evaluation here.
    assert eng.stats.qnodes > 1
    assert len(b._history) == 0


def test_python_search_restores_board_when_timeout_interrupts_recursion():
    """A deadline exception must not leave nested make() calls on the board."""
    b = Board.standard()
    before_grid = tuple(tuple(row) for row in b.grid)
    before_side = b.side_to_move
    before_pc = tuple(b._pc)
    before_sd = tuple(b._sd)

    eng = Engine(max_depth=4)
    eng._ext_budget = eng.EXT_BUDGET
    calls = 0

    def interrupt_inside_tree():
        nonlocal calls
        calls += 1
        if calls >= 5:
            raise search_module._Timeout()

    eng._check_time = interrupt_inside_tree
    interrupted = False
    try:
        eng._negamax(b, CHO, 3, -2 * search_module.MATE, 2 * search_module.MATE)
    except search_module._Timeout:
        interrupted = True

    assert interrupted
    assert tuple(tuple(row) for row in b.grid) == before_grid
    assert b.side_to_move == before_side
    assert tuple(b._pc) == before_pc
    assert tuple(b._sd) == before_sd
    assert len(b._history) == 0
    assert eng._ext_budget == eng.EXT_BUDGET


def test_neural_value_score_uses_engine_perspective():
    from janggi.nn_eval import value_to_score

    assert value_to_score(1.0) > 0   # HAN advantage
    assert value_to_score(-1.0) < 0  # CHO advantage
    assert value_to_score(0.0) == 0


def test_custom_evaluator_is_used_by_python_search():
    b = empty_board()
    b.grid[1][4] = ("K", HAN)
    b.grid[8][4] = ("K", CHO)
    eng = Engine(max_depth=1, evaluator=lambda _board: 321)
    eng._deadline = None

    assert not eng._use_core
    assert eng._quiescence(
        b, HAN, -2 * search_module.MATE, 2 * search_module.MATE
    ) == 321


def test_neural_mcts_returns_visit_policy_and_restores_board():
    from janggi.mcts import NeuralMCTS

    b = Board.standard()
    before_grid = tuple(tuple(row) for row in b.grid)
    legal = {move.as_tuple() for move in b.legal_moves(CHO)}

    def uniform_predictor(position):
        moves = position.legal_moves(position.side_to_move)
        return {move.as_tuple(): 1.0 for move in moves}, 0.0

    mcts = NeuralMCTS(uniform_predictor, simulations=12, seed=7)
    move, policy = mcts.search(b, temperature=0.0, add_root_noise=False)

    assert move is not None and move.as_tuple() in legal
    assert policy and abs(sum(policy.values()) - 1.0) < 1e-9
    assert set(policy).issubset(legal)
    assert tuple(tuple(row) for row in b.grid) == before_grid
    assert b.side_to_move == CHO
    assert len(b._history) == 0


def test_neural_mcts_respects_forbidden_root_moves():
    from janggi.mcts import NeuralMCTS

    b = Board.standard()
    legal = b.legal_moves(CHO)
    allowed = legal[0].as_tuple()
    forbidden = {move.as_tuple() for move in legal[1:]}

    def uniform_predictor(position):
        moves = position.legal_moves(position.side_to_move)
        return {move.as_tuple(): 1.0 for move in moves}, 0.0

    move, policy = NeuralMCTS(
        uniform_predictor, simulations=4, seed=11
    ).search(b, forbidden_moves=forbidden)

    assert move is not None and move.as_tuple() == allowed
    assert policy == {allowed: 1.0}
    assert len(b._history) == 0


def test_cython_quiescence_in_check_searches_evasions_when_available():
    """Exercise the compiled deployment path when extensions are installed."""
    try:
        from janggi._core import core_reset, core_negamax, core_stats
    except ImportError:
        return

    b = empty_board()
    b.grid[1][4] = ("K", HAN)
    b.grid[8][4] = ("K", CHO)
    b.grid[5][4] = ("C", HAN)
    b.resync_int_arrays()
    before_pc, before_sd = tuple(b._pc), tuple(b._sd)

    core_reset(1, 0)
    core_negamax(
        b._pc, b._sd, 2, 0,
        -2 * search_module.MATE, 2 * search_module.MATE,
        0.0, 0,
    )
    _nodes, qnodes, _hits = core_stats()
    assert qnodes > 1
    assert tuple(b._pc) == before_pc
    assert tuple(b._sd) == before_sd
