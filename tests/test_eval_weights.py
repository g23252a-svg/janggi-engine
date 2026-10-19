"""The compiled evaluator's weight tables (evalw).

Every constant of the compiled evaluator lives in a table: table 0 is the
hand-set evaluator, value for value, and table 1 the tuned set from
janggi/eval_weights.py. These tests pin that table 0 is the evaluator the
engine always had, that the feature probe the tuner learns from measures the
very code the search runs, and that the option is validated."""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi.board import Board, CHO, FORMATIONS  # noqa: E402
from janggi.search import Engine, SearchOptions, _HAVE_CORE  # noqa: E402

needs_core = pytest.mark.skipif(
    os.environ.get("JANGGI_NO_ACCEL") == "1" or not _HAVE_CORE,
    reason="the weight tables live in the compiled core",
)

# The constants _evaluate2 carried before the table existed, in table order.
HAND_SET = [0, 1300, 700, 500, 300, 200, 10000, 300,
            150, 60, 40,
            8, 10, 8, 30,
            3, 25, 12, 20,
            3, 15, 40,
            3, 7,
            5,
            12,
            100, 12,
            0, 360, 260, 190, 130, 25, 0, 90,
            0, 120, 90, 65, 40, 0, 0, 25,
            0,
            0, 0, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0]


def _positions(count, seed=7, plies=50):
    rng = random.Random(seed)
    forms = sorted(FORMATIONS)
    out = []
    while len(out) < count:
        b = Board.standard(rng.choice(forms), rng.choice(forms))
        for i in range(rng.randrange(plies)):
            legal = b.legal_moves(b.side_to_move)
            if not legal:
                break
            b.make(rng.choice(legal))
        out.append((b, len(b._history)))
    return out


@needs_core
def test_table_zero_is_the_hand_set_evaluator():
    import janggi._core as core
    scalars, pst = core.core_get_eval_table(0)
    assert scalars == HAND_SET
    assert not any(pst)


@needs_core
def test_probe_measures_the_evaluator():
    """sum(table0 * probe) is the evaluation up to integer truncation, at every
    lock phase -- the tuner's features are the search's own evaluator."""
    import janggi._core as core
    scalars, _ = core.core_get_eval_table(0)
    for b, _ in _positions(60):
        for ply in (10, 90, 130):
            ev = core.core_eval(b._pc, b._sd, ply, 2, 0, 2, 1, 1)
            f, const = core.core_eval2_probe(b._pc, b._sd, ply)
            w = list(scalars)
            w[44] = 2                      # table 0 takes mobility from the mob option
            pred = const + sum(x * y for x, y in zip(w, f))
            assert abs(pred - ev) <= 12, (ply, pred, ev)


@needs_core
def test_probe_leaves_the_live_table_alone():
    import janggi._core as core
    b, ply = _positions(1, seed=3)[0]
    before = core.core_eval(b._pc, b._sd, ply, 2, 0, 2, 1, 1)
    core.core_eval2_probe(b._pc, b._sd, ply)
    assert core.core_eval(b._pc, b._sd, ply, 2, 0, 2, 1, 1) == before


@needs_core
def test_only_table_one_is_writable_and_lengths_are_checked():
    import janggi._core as core
    scalars, pst = core.core_get_eval_table(1)
    with pytest.raises(ValueError):
        core.core_set_eval_table(0, scalars)
    with pytest.raises(ValueError):
        core.core_set_eval_table(3, scalars)
    with pytest.raises(ValueError):
        core.core_set_eval_table(1, scalars[:-1])
    core.core_set_eval_table(1, scalars, pst)      # a no-op round trip
    assert core.core_get_eval_table(1) == (scalars, pst)
    with pytest.raises(ValueError):                 # a short table is refused, not read past its end
        core.core_set_eval_table(1, scalars, pst[:100])
    with pytest.raises((ValueError, TypeError)):     # a bad value leaves the table as it was
        core.core_set_eval_table(1, scalars[:-1] + ["x"], pst)
    assert core.core_get_eval_table(1) == (scalars, pst)


def test_the_tuned_tables_have_the_core_layout():
    """Checked without the extensions, so a malformed file fails everywhere."""
    from janggi import eval_weights
    assert len(eval_weights.TUNED_SCALARS) == len(HAND_SET)
    assert len(eval_weights.TUNED_PST) == 720


def test_mob_is_refused_where_it_cannot_act():
    with pytest.raises(ValueError):
        SearchOptions.parse("mob=0")              # the default table carries its own
    assert SearchOptions.parse("evalw=0,mob=0").mobility_weight == 0
    assert SearchOptions.parse("mob=6,evalw=0").mobility_weight == 6


@needs_core
def test_an_unloaded_candidate_table_is_refused():
    b = Board.standard()                 # nothing in the suite loads table 2
    with pytest.raises(ValueError):
        Engine(max_depth=30, options=SearchOptions(node_limit=2000, eval_weights=2)).search(b, CHO)


@needs_core
def test_the_v1_evaluator_ignores_the_tables():
    """eval=1 is the original evaluator whatever table is live: the tuned
    tempo was fitted for the v2 evaluator and must not reach it."""
    b = Board.standard()
    runs = []
    for ew in (0, 1):
        e = Engine(max_depth=30, options=SearchOptions(node_limit=8000, eval_version=1, eval_weights=ew))
        runs.append((e.search(b, CHO, game_ply=0), e.stats.total_nodes))
    assert runs[0] == runs[1]


@needs_core
def test_the_endgame_lock_counts_official_material_on_every_table():
    """From ply 80 the lock adds material on the official scale; with a
    tuned table live it must not switch to the tuned piece values."""
    import janggi._core as core
    for b, _ in _positions(30, seed=5):
        f, const = core.core_eval2_probe(b._pc, b._sd, 130)
        f0, const0 = core.core_eval2_probe(b._pc, b._sd, 10)
        # the weight-dependent part is the same at any ply; only the lock moves
        assert f == f0
        assert const0 == 0
        lock = core.core_eval(b._pc, b._sd, 130, 2, 0, 2, 1, 1) - core.core_eval(b._pc, b._sd, 10, 2, 0, 2, 1, 1)
        assert lock == const


def test_option_parses_and_is_range_checked():
    assert SearchOptions.parse("evalw=0").eval_weights == 0
    assert SearchOptions().eval_weights == 1          # the tuned table, since 1.5.0
    assert SearchOptions.parse("evalw=2").eval_weights == 2
    with pytest.raises(ValueError):
        SearchOptions.parse("evalw=3")


@needs_core
def test_table_one_drives_the_search():
    """With table 1 set to something else, evalw=1 searches differently and
    evalw=0 is untouched."""
    import janggi._core as core
    keep = core.core_get_eval_table(1)
    b = Board.standard()
    e0 = Engine(max_depth=30, options=SearchOptions(node_limit=20000, eval_weights=0))
    base = e0.search(b, CHO, game_ply=0)
    try:
        w = list(HAND_SET)
        w[1] = 300                     # a chariot worth an elephant
        core.core_set_eval_table(1, w, keep[1])
        e1 = Engine(max_depth=30, options=SearchOptions(node_limit=20000, eval_weights=1))
        e1.search(b, CHO, game_ply=0)
        assert e1.stats.total_nodes != e0.stats.total_nodes
        again = Engine(max_depth=30, options=SearchOptions(node_limit=20000, eval_weights=0)).search(b, CHO, game_ply=0)
        assert again == base
    finally:
        core.core_set_eval_table(1, *keep)


@needs_core
def test_the_tuned_table_is_live():
    """Importing the search loads janggi/eval_weights.py into table 1, and the
    default engine plays with it."""
    import janggi._core as core
    from janggi import eval_weights
    assert core.core_get_eval_table(1) == (list(eval_weights.TUNED_SCALARS), list(eval_weights.TUNED_PST))
    assert len(eval_weights.TUNED_SCALARS) == len(HAND_SET)
    assert eval_weights.TUNED_SCALARS[1] == 1300       # the chariot pins the units
    assert eval_weights.TUNED_SCALARS[6] == 10000
    b = Board.standard()
    tuned = Engine(max_depth=30, options=SearchOptions(node_limit=20000))
    hand = Engine(max_depth=30, options=SearchOptions(node_limit=20000, eval_weights=0))
    tuned.search(b, CHO, game_ply=0); hand.search(b, CHO, game_ply=0)
    assert tuned.stats.total_nodes != hand.stats.total_nodes


def test_the_tuned_pst_is_left_right_symmetric_and_palace_bound():
    from janggi import eval_weights
    pst = eval_weights.TUNED_PST
    assert len(pst) == 720
    for pc in range(8):
        for r in range(10):
            for c in range(9):
                assert pst[pc * 90 + r * 9 + c] == pst[pc * 90 + r * 9 + 8 - c]
    for pc in (6, 7):                      # general and guards never leave the palace
        for r in range(10):
            for c in range(9):
                if not (r <= 2 and 3 <= c <= 5):
                    assert pst[pc * 90 + r * 9 + c] == 0


@needs_core
def test_the_threat_feature_counts_a_piece_attacked_by_a_cheaper_one():
    """Slots 50..57: a piece the enemy attacks with something cheaper than it.
    A HAN chariot in front of a CHO soldier is one (the soldier is worth
    less); the CHO soldier attacked by that chariot is not (the chariot is
    worth more). Table 0 never computes the term."""
    import janggi._core as core
    from janggi.board import HAN
    grid = [[None] * 9 for _ in range(10)]
    grid[1][4] = ("K", HAN)
    grid[8][3] = ("K", CHO)
    grid[5][4] = ("C", HAN)          # chariot
    grid[6][4] = ("J", CHO)          # soldier, bears on (5, 4)
    b = Board.from_grid(grid, HAN)
    f, _ = core.core_eval2_probe(b._pc, b._sd, 10)
    assert f[51] == -1.0             # score -= EW[THR+chariot] * (HAN threatened - CHO threatened)
    assert f[55] == 0.0              # the soldier is attacked by something dearer: no threat
    assert all(x == 0.0 for i, x in enumerate(f[50:58]) if i not in (1, 5))


@needs_core
def test_the_tuned_table_scores_a_position_and_its_mirror_exactly_opposite():
    """The colour mirror (rows flipped, sides swapped) must evaluate to the
    negation under the shipped table too -- piece-square, mobility, tempo-free
    evaluation and threat terms alike -- at every lock phase."""
    import janggi._core as core
    from janggi.board import ROWS, COLS

    def mirror(board):
        m = Board()
        for r in range(ROWS):
            for c in range(COLS):
                p = board.grid[r][c]
                if p is not None:
                    m.grid[ROWS - 1 - r][c] = (p[0], -p[1])
        return m
    for b, _ in _positions(80, seed=13, plies=80):
        mb = mirror(b)
        for ply in (10, 90, 130):
            assert core.core_eval(b._pc, b._sd, ply, 2, 0, 2, 1, 1, 1) == \
                -core.core_eval(mb._pc, mb._sd, ply, 2, 0, 2, 1, 1, 1)


def test_resume_refuses_a_log_played_under_another_table_one(tmp_path):
    """A log records the shipped table 1; resuming it after eval_weights.py
    changed must refuse, not mix two different engines under one spec."""
    import json
    from janggi import match
    log = tmp_path / "m.jsonl"
    log.write_text(json.dumps({"seed": 7, "a_is_cho": True, "winner": "cho", "plies": 1, "reason": "mate",
                               "a": "", "b": "lmr=0", "budget": {"nodes": 300, "time": None, "depth": 3},
                               "table1": "0000000000000000"}) + "\n")
    with pytest.raises(SystemExit, match="different match"):
        match.run_match(match.Config("A", 3, 300, None, ""), match.Config("B", 3, 300, None, "lmr=0"),
                        games=2, seed=7, opening_plies=4, log_path=str(log), resume=True,
                        table1="1111111111111111")
