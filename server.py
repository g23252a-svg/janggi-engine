"""Flask web server for the Janggi engine, deployable on Railway.

Endpoints
---------
GET  /                 -> serves the static board UI (templates/index.html)
POST /api/analyze      -> body: {"board": <10x9 grid>, "side": "cho"|"han",
                                   "time": <seconds>, "depth": <int>}
                          returns the engine's best move and score.
POST /api/new          -> body: {"cho": <formation>, "han": <formation>}
                          returns the starting grid for the chosen formations.
GET  /health           -> liveness probe for Railway.

The grid is serialized as a 10x9 array where each cell is null or a two-char
string like "cC" (cho chariot) / "hK" (han general): first char side
('c'|'h'), second char piece type.
"""

from __future__ import annotations

import json
import os
import threading

from flask import Flask, jsonify, request, render_template

from janggi.board import Board, Move, HAN, CHO, FORMATIONS, ROWS, COLS
from janggi.search import Engine, zobrist_hash
from janggi.score import judge, SCORE_POINTS, HAN_BONUS
from janggi.repetition import RepetitionTracker
from janggi.book import load_book, book_move

app = Flask(__name__)

# Bound the work the public endpoint will do so a request cannot hang the dyno.
MAX_TIME = 6.0
# Iterative deepening plus the time budget decides how deep a search actually
# gets, so this is only a ceiling. It used to be 9 because a depth-9 search
# could not finish in time; it now can.
MAX_DEPTH = 30

# The compiled search core keeps its transposition table, killers and history
# in module-level C arrays, so exactly one search may be in flight per process.
# Deployment runs gunicorn with --workers 2 and one thread each, so this lock is
# uncontended there -- it exists so that adding --threads later degrades
# throughput instead of silently corrupting every concurrent search.
_SEARCH_LOCK = threading.Lock()


class BadRequest(ValueError):
    """Client error worth a 400 rather than a stack trace and a 500."""


@app.errorhandler(BadRequest)
def _bad_request(exc: BadRequest):
    return jsonify({"error": str(exc)}), 400

# Opening book learned from recorded games (gibo). Loaded once at startup.
_BOOK_PATH = os.path.join(os.path.dirname(__file__), "data", "opening_book.json")
OPENING_BOOK = load_book(_BOOK_PATH)


def grid_to_json(board: Board) -> list[list[str | None]]:
    out: list[list[str | None]] = []
    for r in range(ROWS):
        row: list[str | None] = []
        for c in range(COLS):
            p = board.grid[r][c]
            if p is None:
                row.append(None)
            else:
                side_ch = "h" if p[1] == HAN else "c"
                row.append(side_ch + p[0])
        out.append(row)
    return out


def json_to_board(grid) -> Board:
    """Parse a posted 10x9 grid. Raises BadRequest on anything malformed.

    Everything here is attacker-controlled, so each cell is validated rather
    than indexed into and hoped for; an unknown piece letter used to reach the
    piece-code table and come back as a 500.
    """
    if not isinstance(grid, list) or len(grid) != ROWS:
        raise BadRequest(f"board must be a {ROWS}x{COLS} grid")
    cells: list[list[tuple[str, int] | None]] = []
    for r, row in enumerate(grid):
        if not isinstance(row, list) or len(row) != COLS:
            raise BadRequest(f"board must be a {ROWS}x{COLS} grid")
        parsed: list[tuple[str, int] | None] = []
        for c, cell in enumerate(row):
            if cell is None or cell == "":
                parsed.append(None)
                continue
            if not isinstance(cell, str) or len(cell) != 2 or cell[0] not in "hc":
                raise BadRequest(f"bad cell at ({r},{c}): {cell!r}")
            parsed.append((cell[1], HAN if cell[0] == "h" else CHO))
        cells.append(parsed)
    try:
        return Board.from_grid(cells)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc


def _formations(data: dict) -> tuple[str, str]:
    cho = data.get("cho_formation", "msm_s")
    han = data.get("han_formation", "msm_s")
    if cho not in FORMATIONS or han not in FORMATIONS:
        raise BadRequest("invalid formation")
    return cho, han


def _parse_history(history) -> list[dict]:
    """Validate a posted move history into plain int dicts."""
    if history is None:
        return []
    if not isinstance(history, list):
        raise BadRequest("history must be a list of moves")
    out = []
    for i, mv in enumerate(history):
        if not isinstance(mv, dict):
            raise BadRequest(f"history[{i}] must be an object")
        try:
            fr, fc, tr, tc = (int(mv["fr"]), int(mv["fc"]), int(mv["tr"]), int(mv["tc"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise BadRequest(f"history[{i}] needs integer fr/fc/tr/tc") from exc
        if not (0 <= fr < ROWS and 0 <= tr < ROWS and 0 <= fc < COLS and 0 <= tc < COLS):
            raise BadRequest(f"history[{i}] is off the board")
        out.append({"fr": fr, "fc": fc, "tr": tr, "tc": tc,
                    "captured": mv.get("captured"), "side": mv.get("side")})
    return out


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/new", methods=["POST"])
def api_new():
    data = request.get_json(force=True, silent=True) or {}
    cho = data.get("cho", "msm_s")
    han = data.get("han", "msm_s")
    if cho not in FORMATIONS or han not in FORMATIONS:
        raise BadRequest("invalid formation")
    board = Board.standard(cho, han)
    return jsonify({"board": grid_to_json(board)})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(force=True, silent=True) or {}
    side_str = data.get("side", "cho")
    side = CHO if side_str == "cho" else HAN

    # Live-game time manager. The app is used for 5-minute + 30-second byo-yomi
    # games, so the search must finish comfortably inside the byo-yomi period.
    # Depth is no longer pinned per tier: iterative deepening spends the budget
    # and keeps the deepest completed result, which reaches far deeper in quiet
    # positions than any fixed guess.
    try:
        requested = float(data.get("time", 3.0))
    except (TypeError, ValueError):
        requested = 3.0
    requested = max(0.5, min(requested, MAX_TIME))
    if requested <= 1.5:
        time_limit = 1.2
    elif requested <= 3.0:
        time_limit = 2.5
    else:
        time_limit = min(requested, MAX_TIME) - 0.5

    depth = MAX_DEPTH
    if "depth" in data:
        try:
            depth = max(1, min(int(data["depth"]), MAX_DEPTH))
        except (TypeError, ValueError) as exc:
            raise BadRequest("depth must be an integer") from exc

    history = _parse_history(data.get("history"))
    forbidden: set[tuple[int, int, int, int]] = set()
    soft_forbidden: set[tuple[int, int, int, int]] = set()
    history_hashes: list[int] = []
    game_ply = len(history)

    if data.get("history") is not None:
        # Replaying the game gives the true position, the repetition counts and
        # the played ply count all at once.
        cho_form, han_form = _formations(data)
        board, tracker, hashes, last_capture = _rebuild_with_history(
            cho_form, han_form, history
        )
        history_hashes = hashes[last_capture:-1]
        legal_here = board.legal_moves(side)
        for m in legal_here:
            if tracker.would_repeat_thrice(board, m):
                forbidden.add(m.as_tuple())
            elif tracker.would_repeat_twice(board, m):
                soft_forbidden.add(m.as_tuple())

        # Shuffle detection, independent of whole-board repetition: if this side
        # has been moving one piece back and forth, ban continuing the shuffle.
        # The opponent may vary just enough that the whole position never
        # repeats three times while our side wastes move after move.
        side_str_hist = "cho" if side == CHO else "han"
        own_recent = [m for m in history if m.get("side") == side_str_hist][-3:]
        if own_recent:
            last = own_recent[-1]
            soft_forbidden.add((last["tr"], last["tc"], last["fr"], last["fc"]))
            vacated = {(m["fr"], m["fc"]) for m in own_recent}
            for m in legal_here:
                if (m.tr, m.tc) in vacated and (m.fr, m.fc) == (last["tr"], last["tc"]):
                    soft_forbidden.add(m.as_tuple())

        # Hard bans always apply; soft bans only while a legal move survives.
        combined = forbidden | soft_forbidden
        if [m for m in legal_here if m.as_tuple() not in combined]:
            forbidden = combined
    else:
        board = json_to_board(data.get("board"))
    board.side_to_move = side

    # Consult the opening book first: if this exact position was seen in
    # recorded games, recommend the most-played (and still legal) reply
    # instantly, skipping the search entirely.
    bmove = book_move(OPENING_BOOK, board) if OPENING_BOOK else None
    if bmove is not None and bmove.as_tuple() not in forbidden:
        return jsonify({
            "move": {"fr": bmove.fr, "fc": bmove.fc,
                     "tr": bmove.tr, "tc": bmove.tc, "captured": bmove.captured},
            "score": 0,
            "danger": {"level": "ok", "text": ""},
            "depthReached": 0, "nodes": 0, "pv": [],
            "fromBook": True, "gameOver": False,
        })

    engine = Engine(max_depth=depth, time_limit=time_limit)
    with _SEARCH_LOCK:
        move, score = engine.search(
            board, side,
            forbidden_moves=forbidden,
            history_hashes=history_hashes,
            game_ply=game_ply,
        )
        # If forbidding repetition left no recommendation but legal moves exist
        # (the only legal move happens to repeat -- e.g. the sole escape from
        # check), retry unfiltered so the user still sees the forced move.
        if move is None and board.legal_moves(side):
            move, score = engine.search(board, side, game_ply=game_ply)
        pv = list(engine.stats.pv)
        depth_reached = engine.stats.depth_reached
        nodes = engine.stats.total_nodes

    if move is None:
        return jsonify({"move": None, "score": score, "gameOver": True})

    # Danger warning. The search score is from the side-to-move's point of view
    # (negative = the player is losing). Surfacing this lets the player switch to
    # defending the palace BEFORE a mating net closes -- the recurring loss
    # pattern was being a piece up while the general was quietly smothered.
    if score <= -800:
        danger = {"level": "critical", "text": "외통/큰 위기 — 궁 수비 최우선"}
    elif score <= -300:
        danger = {"level": "bad", "text": "열세 — 공격 멈추고 방어 전환"}
    elif score <= -120:
        danger = {"level": "warn", "text": "약간 불리 — 궁성 주의"}
    else:
        danger = {"level": "ok", "text": ""}

    return jsonify({
        "move": {"fr": move.fr, "fc": move.fc,
                 "tr": move.tr, "tc": move.tc, "captured": move.captured},
        "score": score,
        "danger": danger,
        "depthReached": depth_reached,
        "nodes": nodes,
        "pv": [{"fr": a, "fc": b, "tr": c, "tc": d} for a, b, c, d in pv],
        "gameOver": False,
    })


def _rebuild_with_history(cho_form, han_form, history):
    """Replay a move history onto the starting position.

    Returns the board, a RepetitionTracker that has seen every position along
    the way, the list of position keys, and the index of the position right
    after the last capture (nothing before it can ever repeat).
    """
    board = Board.standard(cho_form, han_form)
    tracker = RepetitionTracker()
    tracker.record(board)
    hashes = [board.zobrist()]
    last_capture = 0
    for m in history or []:
        captured = board.grid[m["tr"]][m["tc"]]
        board.make(Move(m["fr"], m["fc"], m["tr"], m["tc"],
                        captured[0] if captured else None))
        tracker.record(board)
        hashes.append(board.zobrist())
        if captured is not None:
            last_capture = len(hashes) - 1
    return board, tracker, hashes, last_capture


@app.route("/api/legal", methods=["POST"])
def api_legal():
    """Return legal moves for a square, excluding 3-fold-repetition moves.

    If `history` (list of past moves with formations) is supplied, repetition
    is enforced; otherwise it falls back to plain legality on the given board.
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        fr = int(data["fr"])
        fc = int(data["fc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequest("fr and fc are required integers") from exc
    if not (0 <= fr < ROWS and 0 <= fc < COLS):
        raise BadRequest("fr/fc are off the board")

    history = data.get("history")
    if history is not None:
        cho_form, han_form = _formations(data)
        board, tracker, _hashes, _lc = _rebuild_with_history(
            cho_form, han_form, _parse_history(history)
        )
        piece = board.grid[fr][fc]
        if piece is None:
            return jsonify({"moves": []})
        side = piece[1]
        moves = [
            {"tr": m.tr, "tc": m.tc, "captured": m.captured}
            for m in tracker.legal_nonrepeating(board, side)
            if m.fr == fr and m.fc == fc
        ]
        return jsonify({"moves": moves})

    # Fallback: plain legality on the posted board (no repetition context).
    if data.get("board") is None:
        raise BadRequest("board or history required")
    board = json_to_board(data["board"])
    piece = board.grid[fr][fc]
    if piece is None:
        return jsonify({"moves": []})
    side = piece[1]
    moves = [
        {"tr": m.tr, "tc": m.tc, "captured": m.captured}
        for m in board.legal_moves(side)
        if m.fr == fr and m.fc == fc
    ]
    return jsonify({"moves": moves})


@app.route("/api/repetition", methods=["POST"])
def api_repetition():
    """Given formations + move history, return how many times the CURRENT
    position (after all history moves) has occurred. 3 or more => repetition
    draw / score decision should trigger."""
    data = request.get_json(force=True, silent=True) or {}
    cho_form, han_form = _formations(data)
    board, tracker, _hashes, _lc = _rebuild_with_history(
        cho_form, han_form, _parse_history(data.get("history", []))
    )
    count = tracker.count(board)
    return jsonify({"count": count, "repetition": count >= 3})


@app.route("/api/score", methods=["POST"])
def api_score():
    data = request.get_json(force=True, silent=True) or {}
    board = json_to_board(data.get("board"))
    result = judge(board)
    return jsonify(result)


@app.route("/api/gibo/validate", methods=["POST"])
def api_gibo_validate():
    """Validate an uploaded gibo and return its final position + score."""
    from janggi.gibo import Gibo

    data = request.get_json(force=True, silent=True) or {}
    try:
        gibo = Gibo.from_json(json.dumps(data))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"invalid gibo: {exc}"}), 400
    ok, msg = gibo.validate()
    board = gibo.starting_board()
    for m in gibo.moves:
        board.make(Move(m["fr"], m["fc"], m["tr"], m["tc"], m.get("captured")))
    return jsonify(
        {
            "valid": ok,
            "message": msg,
            "moveCount": len(gibo.moves),
            "finalBoard": grid_to_json(board),
            "score": judge(board),
        }
    )


if __name__ == "__main__":
    # Railway provides the port via the PORT env var.
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
