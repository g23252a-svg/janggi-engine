"""Web API tests.

The public endpoints take attacker-controlled JSON. Every one of these used to
be reachable with input that produced a 500 and a stack trace rather than a
400: an unknown piece letter walked straight into the piece-code table, a
non-integer depth into int(), a history entry missing "fr" into a KeyError.
"""

import json
import os
import re
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("flask")

import server as srv  # noqa: E402
from janggi.board import Board, CHO, HAN  # noqa: E402
from janggi.winprob import win_probability  # noqa: E402


@pytest.fixture
def client():
    srv.app.config["TESTING"] = True
    with srv.app.test_client() as c:
        yield c


def start_grid(cho="msm_s", han="msm_s"):
    return srv.grid_to_json(Board.standard(cho, han))


# ------------------------------------------------------------------- health
def test_health(client):
    body = client.get("/health").get_json()
    assert body["status"] == "ok"
    # version / accel are pinned in test_version.py, which owns that contract.


# ------------------------------------------------------------------ /api/new
def test_new_returns_start_position(client):
    body = client.post("/api/new", json={"cho": "smsm", "han": "msms"}).get_json()
    assert body["board"] == start_grid("smsm", "msms")


def test_new_rejects_unknown_formation(client):
    r = client.post("/api/new", json={"cho": "nope", "han": "msm_s"})
    assert r.status_code == 400
    assert "error" in r.get_json()


# -------------------------------------------------------------- /api/analyze
def test_analyze_returns_a_legal_move(client):
    body = client.post(
        "/api/analyze", json={"board": start_grid(), "side": "cho", "time": 0.5}
    ).get_json()
    assert body["gameOver"] is False
    move = body["move"]
    board = Board.standard()
    legal = {m.as_tuple() for m in board.legal_moves(CHO)}
    assert (move["fr"], move["fc"], move["tr"], move["tc"]) in legal


def test_analyze_reports_a_principal_variation(client):
    board = Board()
    board.grid[8][4] = ("K", CHO)
    board.grid[1][4] = ("K", HAN)
    board.grid[5][2] = ("C", CHO)
    board.grid[5][6] = ("C", HAN)
    body = client.post(
        "/api/analyze",
        json={"board": srv.grid_to_json(board), "side": "cho", "time": 0.5},
    ).get_json()
    assert body["depthReached"] >= 1
    assert isinstance(body["pv"], list) and body["pv"]
    first = body["pv"][0]
    assert (first["fr"], first["fc"]) == (body["move"]["fr"], body["move"]["fc"])


def test_analyze_with_history_plays_on(client):
    history = [{"fr": 6, "fc": 2, "tr": 6, "tc": 3, "captured": None, "side": "cho"}]
    body = client.post(
        "/api/analyze",
        json={
            "history": history, "cho_formation": "msm_s", "han_formation": "msm_s",
            "side": "han", "time": 0.5,
        },
    ).get_json()
    assert body["move"] is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"board": [[None] * 9] * 3, "side": "cho"},                 # wrong shape
        {"board": "not a board", "side": "cho"},                    # wrong type
        {"side": "cho"},                                            # missing
    ],
)
def test_analyze_rejects_malformed_board(client, payload):
    assert client.post("/api/analyze", json=payload).status_code == 400


def test_analyze_rejects_unknown_piece_letter(client):
    grid = start_grid()
    grid[5][5] = "cZ"           # letter that is not a piece
    r = client.post("/api/analyze", json={"board": grid, "side": "cho"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_analyze_rejects_bad_cell_shape(client):
    grid = start_grid()
    grid[5][5] = 42
    assert client.post("/api/analyze", json={"board": grid, "side": "cho"}).status_code == 400


def test_analyze_rejects_non_integer_depth(client):
    r = client.post(
        "/api/analyze", json={"board": start_grid(), "side": "cho", "depth": "deep"}
    )
    assert r.status_code == 400


def test_analyze_rejects_malformed_history(client):
    for history in ([{"fr": 1}], [{"fr": 1, "fc": 1, "tr": 1, "tc": "x"}], "nope",
                    [{"fr": 99, "fc": 0, "tr": 0, "tc": 0}]):
        r = client.post(
            "/api/analyze",
            json={"history": history, "cho_formation": "msm_s",
                  "han_formation": "msm_s", "side": "han"},
        )
        assert r.status_code == 400, history


def test_analyze_survives_an_absurd_time_request(client):
    body = client.post(
        "/api/analyze", json={"board": start_grid(), "side": "cho", "time": 1e9}
    ).get_json()
    assert body["move"] is not None


def test_analyze_detects_no_legal_move(client):
    """A side with no legal move has lost; the API must say so, not 500."""
    board = Board()
    board.grid[0][4] = ("K", HAN)   # only piece Han has left
    board.grid[9][3] = ("K", CHO)
    board.grid[0][0] = ("C", CHO)   # rakes rank 0: checks (0,4), covers (0,3)
    board.grid[9][4] = ("C", CHO)   # file 4: covers (1,4)
    board.grid[9][5] = ("C", CHO)   # file 5: covers (0,5)
    assert board.in_check(HAN)
    assert board.legal_moves(HAN) == []
    body = client.post(
        "/api/analyze", json={"board": srv.grid_to_json(board), "side": "han"}
    ).get_json()
    assert body["gameOver"] is True and body["move"] is None


# ------------------------------------------------- /api/analyze: winProb
def shuffle_history(n):
    """n plies of chariots stepping out and back: a valid game of any length,
    so the server's game_ply (the history length) can be put in any phase."""
    cycle = [(9, 0, 8, 0, "cho"), (0, 0, 1, 0, "han"), (8, 0, 9, 0, "cho"), (1, 0, 0, 0, "han")]
    return [{"fr": a, "fc": b, "tr": c, "tc": d, "captured": None, "side": side}
            for a, b, c, d, side in (cycle[i % 4] for i in range(n))]


class FixedScoreEngine:
    """Stands in for the search: a legal move and a score chosen by the test."""
    score = 0

    def __init__(self, *args, **kwargs):
        self.stats = types.SimpleNamespace(pv=[], depth_reached=7, total_nodes=1234)

    def search(self, board, side, **kwargs):
        return board.legal_moves(side)[0], self.score


def mate_in_one_board():
    """CHO to move mates with the chariot (5,0)->(0,0): rank 0 is then
    covered, rank 1 by the chariot on (1,8), and file 5 by the one on (9,5)."""
    board = Board()
    board.grid[0][4] = ("K", HAN)
    board.grid[9][3] = ("K", CHO)
    board.grid[5][0] = ("C", CHO)
    board.grid[1][8] = ("C", CHO)
    board.grid[9][5] = ("C", CHO)
    assert not board.in_check(HAN)
    return board


def test_analyze_reports_the_win_probability_of_the_side_to_move(client):
    body = client.post(
        "/api/analyze", json={"board": start_grid(), "side": "cho", "time": 0.5}
    ).get_json()
    assert isinstance(body["winProb"], float) and 0.0 < body["winProb"] < 1.0
    assert body["winProb"] == round(win_probability(body["score"], 0), 4)
    assert body["mateIn"] is None


@pytest.mark.parametrize("plies, score, expected", [
    (0, 300, 0.6883),      # k = 0.00264
    (79, -416, 0.2501),    # the opening's 75 % point, seen from the other side
    (85, 300, 0.636),      # k = 0.00186 from ply 80
    (125, 300, 0.6415),    # k = 0.00194 from ply 120
])
def test_win_probability_uses_the_ply_the_game_is_at(client, monkeypatch, plies, score, expected):
    """game_ply is the posted history's length: the same score means less
    once the endgame lock has doubled (ply 80) or tripled (ply 120) it."""
    monkeypatch.setattr(srv, "Engine", type("E", (FixedScoreEngine,), {"score": score}))
    body = client.post("/api/analyze", json={
        "history": shuffle_history(plies), "cho_formation": "msm_s", "han_formation": "msm_s",
        "side": "cho" if plies % 2 == 0 else "han", "time": 0.5,
    }).get_json()
    assert body["score"] == score
    assert body["winProb"] == pytest.approx(expected, abs=1e-4)
    assert body["winProb"] == round(win_probability(score, plies), 4)
    assert body["mateIn"] is None


def test_a_forced_mate_is_certain_and_counted(client):
    body = client.post("/api/analyze", json={
        "board": srv.grid_to_json(mate_in_one_board()), "side": "cho", "time": 1.0,
    }).get_json()
    assert (body["move"]["fr"], body["move"]["fc"]) == (5, 0)
    assert body["winProb"] == 1.0 and body["mateIn"] == 1


def test_the_side_being_mated_sees_zero_and_a_negative_count(client, monkeypatch):
    monkeypatch.setattr(srv, "Engine", type("E", (FixedScoreEngine,), {"score": -999_994}))
    body = client.post("/api/analyze", json={"board": start_grid(), "side": "cho"}).get_json()
    assert body["winProb"] == 0.0 and body["mateIn"] == -3


def test_no_legal_move_is_a_lost_position(client):
    """Both search cores must report it the same way: the compiled one says
    -MATE, the pure-Python one used to say 0 (a 50 % game)."""
    board = Board()
    board.grid[0][4] = ("K", HAN)
    board.grid[9][3] = ("K", CHO)
    board.grid[0][0] = ("C", CHO)
    board.grid[9][4] = ("C", CHO)
    board.grid[9][5] = ("C", CHO)
    body = client.post(
        "/api/analyze", json={"board": srv.grid_to_json(board), "side": "han"}
    ).get_json()
    assert body["gameOver"] is True
    assert body["score"] == -1_000_000
    assert body["winProb"] == 0.0 and body["mateIn"] == -1


def test_a_book_move_has_no_win_probability(client, monkeypatch):
    """A book reply is not searched; its score of 0 is not an evaluation."""
    monkeypatch.setattr(srv, "OPENING_BOOK", {"stub": True})
    monkeypatch.setattr(srv, "book_move", lambda book, board: board.legal_moves(board.side_to_move)[0])
    body = client.post("/api/analyze", json={"board": start_grid(), "side": "cho"}).get_json()
    assert body["fromBook"] is True
    assert "winProb" in body and body["winProb"] is None
    assert "mateIn" in body and body["mateIn"] is None


# ---------------------------------------------------------------- /api/legal
def test_legal_lists_moves_for_a_square(client):
    body = client.post(
        "/api/legal", json={"board": start_grid(), "fr": 6, "fc": 0}
    ).get_json()
    assert {(m["tr"], m["tc"]) for m in body["moves"]} == {(5, 0), (6, 1)}


def test_legal_rejects_missing_or_off_board_square(client):
    assert client.post("/api/legal", json={"board": start_grid()}).status_code == 400
    assert client.post(
        "/api/legal", json={"board": start_grid(), "fr": 99, "fc": 0}
    ).status_code == 400
    assert client.post(
        "/api/legal", json={"board": start_grid(), "fr": "x", "fc": 0}
    ).status_code == 400


def test_legal_needs_a_board_or_a_history(client):
    assert client.post("/api/legal", json={"fr": 0, "fc": 0}).status_code == 400


# ---------------------------------------------------------------- /api/score
def test_score_of_the_start_position(client):
    body = client.post("/api/score", json={"board": start_grid()}).get_json()
    assert body["cho"] == 72 and body["han"] == 73.5 and body["winner"] == "han"


def test_score_rejects_a_malformed_board(client):
    assert client.post("/api/score", json={"board": [[]]}).status_code == 400


# ----------------------------------------------------------- /api/repetition
def test_repetition_counts_the_current_position(client):
    body = client.post(
        "/api/repetition",
        json={"cho_formation": "msm_s", "han_formation": "msm_s", "history": []},
    ).get_json()
    assert body == {"count": 1, "repetition": False}


def test_repetition_rejects_a_bad_formation(client):
    r = client.post("/api/repetition", json={"cho_formation": "zzz", "history": []})
    assert r.status_code == 400


# ------------------------------------------------------------- board parsing
def test_json_to_board_keeps_accelerator_arrays_live():
    """The bug this guards: a board parsed from JSON that the compiled attack
    test then read as empty, so the server could not see check."""
    board = Board()
    board.grid[8][4] = ("K", CHO)
    board.grid[1][4] = ("K", HAN)
    board.grid[8][0] = ("C", HAN)
    parsed = srv.json_to_board(srv.grid_to_json(board))
    assert parsed.in_check(CHO) is True
    assert parsed.zobrist() == board.zobrist()


# ------------------------------------------------------------- opening book
def test_opening_book_is_off_by_default():
    """It was measured to lose ~138 centipawns a move against just searching;
    see the note in server.py. This pins the default so it cannot drift back on
    unnoticed."""
    assert srv.USE_BOOK is False
    assert srv.OPENING_BOOK == {}


def test_analyze_searches_the_opening_instead_of_quoting_it(client):
    body = client.post(
        "/api/analyze", json={"board": start_grid(), "side": "cho", "time": 1.0}
    ).get_json()
    assert body.get("fromBook") is not True
    assert body["depthReached"] >= 1, "a real search should have run"
    assert body["nodes"] > 0


# ---------------------------------------------------------------- the page
def test_the_page_has_the_win_probability_strip_and_review(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="winStrip"' in html and 'role="meter"' in html
    assert 'id="review"' in html
    assert 'class="scorebar"' not in html, "the strip replaces the old score bar"
    # The probability comes from the API, computed once in janggi/winprob.py;
    # the page only turns it round to the player's side.
    assert "res.winProb" in html and "res.mateIn" in html
    assert "function pMy" in html
    assert "Math.exp" not in html, "a second, JavaScript copy of the logistic"
    assert "{{" not in html and "{%" not in html


def test_a_saved_gibo_with_stored_evaluations_is_still_a_gibo(client):
    """The page now saves the side it played and its evaluations with the
    moves; Gibo.from_json (and so /api/gibo/validate) must take such a file
    exactly as it takes one without them."""
    moves = shuffle_history(4)
    record = {
        "version": 1, "cho_formation": "msm_s", "han_formation": "msm_s",
        "moves": moves, "result": None, "note": "", "finalScore": {"cho": 72, "han": 73.5},
        "mySide": "cho",
        "evals": [{"ply": 0, "side": "cho", "score": 41, "book": False, "depth": 11,
                   "winProb": 0.527, "mateIn": None,
                   "best": {"fr": 9, "fc": 0, "tr": 8, "tc": 0, "piece": "C"},
                   "played": {"fr": 9, "fc": 0, "tr": 8, "tc": 0, "piece": "C"}}],
    }
    with_evals = client.post("/api/gibo/validate", json=record).get_json()
    without = client.post("/api/gibo/validate", json={
        k: v for k, v in record.items() if k not in ("mySide", "evals")}).get_json()
    assert with_evals["valid"] is True and with_evals["moveCount"] == 4
    assert with_evals == without


# --------------------------------------------------------- home-screen assets
def test_the_page_can_fetch_everything_it_references(client):
    """The UI asks for the manifest, the icons and the service worker by
    relative path, so under Flask they resolve to the app root. A route missing
    here is a silent 404 on a phone -- the app just fails to install."""
    html = client.get("/").get_data(as_text=True)
    refs = set(re.findall(r'(?:href|src)="(?!https?:|/|#|data:)([^"]+)"', html))
    refs |= set(re.findall(r"register\('([^']+)'\)", html))
    assert "manifest.webmanifest" in refs, "parsed nothing; the regex needs updating"
    for ref in sorted(refs):
        assert client.get("/" + ref).status_code == 200, f"page references {ref}, server 404s"


def test_the_manifest_is_valid_and_its_icons_exist(client):
    r = client.get("/manifest.webmanifest")
    assert r.mimetype == "application/manifest+json"
    manifest = json.loads(r.get_data(as_text=True))
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert client.get("/" + icon["src"]).status_code == 200


def test_the_service_worker_is_served_from_the_root_and_uncached(client):
    """A worker can only control pages at or below its own path, so it has to
    be at /sw.js and not /static/sw.js. And a cached worker keeps serving a
    stale shell after a deploy, which is the classic way a PWA gets stuck."""
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.mimetype
    assert "no-cache" in r.headers.get("Cache-Control", "")


def test_an_unknown_icon_size_is_a_400_not_a_traversal(client):
    assert client.get("/icon-64.png").status_code == 400
