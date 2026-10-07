"""The A/B match runner has to survive the box being reclaimed mid-run.

Two 60-game measurements died at game 19 and game 10 with nothing to show for
them. These pin the three things that make that survivable: every finished game
is logged, a resumed run counts the logged games and plays only the rest, and
shards run with different seed ranges pool into one summary.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from janggi import match  # noqa: E402


def tiny(spec=""):
    # 300 nodes a move: a game is over in a second, and the result is
    # deterministic because the budget is in nodes, not seconds.
    return match.Config("A" if spec == "" else "B", 3, 300, None, spec)


def test_every_finished_game_is_logged_as_it_finishes(tmp_path, capsys):
    log = tmp_path / "m.jsonl"
    match.run_match(tiny(), tiny("lmr=0"), games=2, seed=7, opening_plies=4,
                    log_path=str(log))
    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert {(r["seed"], r["a_is_cho"]) for r in lines} == {(7, True), (7, False)}
    assert all(r["winner"] in ("cho", "han", "draw") for r in lines)
    assert all(r["reason"] in ("mate", "stalemate", "repetition", "no_move", "cap") for r in lines)
    assert "endings (A wins-losses):" in capsys.readouterr().out


def test_resume_counts_logged_games_and_plays_only_the_rest(tmp_path, capsys):
    log = tmp_path / "m.jsonl"
    # Pretend a run of 4 games (2 pairs) died after the first pair.
    log.write_text(
        json.dumps({"seed": 7, "a_is_cho": True, "winner": "cho", "plies": 50, "a": "", "b": "lmr=0"}) + "\n"
        + json.dumps({"seed": 7, "a_is_cho": False, "winner": "cho", "plies": 60, "a": "", "b": "lmr=0"}) + "\n"
    )
    res = match.run_match(tiny(), tiny("lmr=0"), games=4, seed=7, opening_plies=4,
                          log_path=str(log), resume=True)
    out = capsys.readouterr().out
    assert "(from log)" in out
    assert "2 game(s) taken from" in out
    assert res["games"] == 4
    # the two logged results were A-as-cho wins cho (A wins) and A-as-han wins cho (A loses)
    assert res["wins"] >= 1 and res["losses"] >= 1
    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 4, "only the two unplayed games were appended"
    assert {(r["seed"], r["a_is_cho"]) for r in lines} == {(7, True), (7, False), (8, True), (8, False)}


def test_resume_is_idempotent(tmp_path, capsys):
    """Resuming a finished match plays nothing and reports the same numbers."""
    log = tmp_path / "m.jsonl"
    first = match.run_match(tiny(), tiny("lmr=0"), games=2, seed=11, opening_plies=4,
                            log_path=str(log))
    before = log.read_text()
    again = match.run_match(tiny(), tiny("lmr=0"), games=2, seed=11, opening_plies=4,
                            log_path=str(log), resume=True)
    assert log.read_text() == before, "a fully logged match must not append"
    assert (again["wins"], again["draws"], again["losses"]) == (first["wins"], first["draws"], first["losses"])


def test_pool_merges_shards_and_dedups_repeated_games(tmp_path, capsys):
    s1 = tmp_path / "s1.jsonl"
    s2 = tmp_path / "s2.jsonl"
    rec = lambda seed, cho, w: json.dumps({"seed": seed, "a_is_cho": cho, "winner": w, "plies": 1, "a": "", "b": "x=0"}) + "\n"
    s1.write_text(rec(1, True, "cho") + rec(1, False, "cho"))          # A wins, A loses
    s2.write_text(rec(2, True, "han") + rec(2, False, "han") + rec(1, True, "cho"))  # A loses, A wins, duplicate
    res = match.pool_logs([str(s1), str(s2)])
    assert res["games"] == 4, "the duplicated game counts once"
    assert (res["wins"], res["losses"]) == (2, 2)


def test_pool_warns_when_configurations_differ(tmp_path, capsys):
    s1 = tmp_path / "s1.jsonl"
    s2 = tmp_path / "s2.jsonl"
    s1.write_text(json.dumps({"seed": 1, "a_is_cho": True, "winner": "cho", "plies": 1, "a": "", "b": "x=0"}) + "\n")
    s2.write_text(json.dumps({"seed": 2, "a_is_cho": True, "winner": "cho", "plies": 1, "a": "", "b": "y=0"}) + "\n")
    match.pool_logs([str(s1), str(s2)])
    assert "different configurations" in capsys.readouterr().out


# ------------------------------------------------------------ the interval
def test_pair_interval_is_zero_width_when_every_pair_splits(capsys):
    """Every pair 1-1: the opening decided nothing, A and B are level, and
    there is no variance between pairs to put in the interval."""
    res = match.summarize("A", "B", 10, 0, 10, pair_scores=[1.0] * 10)
    lo, hi = res["ci"]
    assert abs(lo - 0.5) < 1e-9 and abs(hi - 0.5) < 1e-9


def test_pair_interval_is_wider_than_per_game_when_pairs_are_decided_by_the_opening(capsys):
    """Every pair 2-0 or 0-2: each opening decided both games the same way, so
    there are 10 independent observations, not 20. The per-game formula
    pretends there are 20 and reports an interval that is too narrow."""
    pairs = [2.0] * 5 + [0.0] * 5
    pair_res = match.summarize("A", "B", 10, 0, 10, pair_scores=pairs)
    game_res = match.summarize("A", "B", 10, 0, 10)
    assert pair_res["score"] == game_res["score"] == 0.5
    assert (pair_res["ci"][1] - pair_res["ci"][0]) > (game_res["ci"][1] - game_res["ci"][0])


def test_summary_round_trips_through_json_even_at_a_clean_sweep(capsys):
    import json as _json
    res = match.summarize("A", "B", 4, 0, 0, pair_scores=[2.0, 2.0])
    _json.dumps(res)                      # inf would raise
    assert res["elo"] > 0 and res["elo"] < 10_000


def test_pool_uses_pairs_and_counts_half_pairs_in_the_score_only(tmp_path, capsys):
    rec = lambda seed, cho, w: json.dumps({"seed": seed, "a_is_cho": cho, "winner": w, "plies": 1, "a": "", "b": "x=0"}) + "\n"
    s1 = tmp_path / "s1.jsonl"
    s1.write_text(rec(1, True, "cho") + rec(1, False, "han")      # pair 1: A 2-0
                  + rec(2, True, "han") + rec(2, False, "cho")    # pair 2: A 0-2
                  + rec(3, True, "cho"))                          # half pair: A won one
    res = match.pool_logs([str(s1)])
    assert res["games"] == 5 and res["wins"] == 3 and res["losses"] == 2
    assert res["pairs"] == 2
    assert "half-played pair" in capsys.readouterr().out
