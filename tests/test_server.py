"""Web API tests.

The public endpoints take attacker-controlled JSON. Every one of these used to
be reachable with input that produced a 500 and a stack trace rather than a
400: an unknown piece letter walked straight into the piece-code table, a
non-integer depth into int(), a history entry missing "fr" into a KeyError.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("flask")

import server as srv  # noqa: E402
from janggi.board import Board, CHO, HAN  # noqa: E402


@pytest.fixture
def client():
    srv.app.config["TESTING"] = True
    with srv.app.test_client() as c:
        yield c


def start_grid(cho="msm_s", han="msm_s"):
    return srv.grid_to_json(Board.standard(cho, han))


# ------------------------------------------------------------------- health
def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


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
