"""The analysis API, implemented without a web framework.

server.py serves these three calls over Flask. This module answers exactly the
same requests inside the browser, under Pyodide, so the GitHub Pages build works
with no backend at all. The board UI cannot tell the difference: it still calls
/api/new, /api/legal and /api/analyze and gets the same JSON back.

The engine here is the pure-Python fallback -- WebAssembly cannot load the
Cython extensions -- so it searches far less deeply than a real deployment.
The UI says so, and offers to forward to a real server instead.
"""

from __future__ import annotations

import json

from janggi.board import Board, Move, HAN, CHO, FORMATIONS, ROWS, COLS
from janggi.repetition import RepetitionTracker
from janggi.score import judge
from janggi.search import Engine

# Pure Python is roughly two orders of magnitude slower than the compiled core,
# so cap the work: iterative deepening keeps the deepest COMPLETED depth, and
# these bounds keep the page responsive instead of hanging the tab.
BROWSER_MAX_DEPTH = 5
BROWSER_MAX_TIME = 8.0


class BadRequest(ValueError):
    pass


def grid_to_json(board: Board):
    out = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            p = board.grid[r][c]
            row.append(None if p is None else ("h" if p[1] == HAN else "c") + p[0])
        out.append(row)
    return out


def json_to_board(grid) -> Board:
    if not isinstance(grid, list) or len(grid) != ROWS:
        raise BadRequest(f"board must be a {ROWS}x{COLS} grid")
    cells = []
    for row in grid:
        if not isinstance(row, list) or len(row) != COLS:
            raise BadRequest(f"board must be a {ROWS}x{COLS} grid")
        parsed = []
        for cell in row:
            if cell is None or cell == "":
                parsed.append(None)
            elif isinstance(cell, str) and len(cell) == 2 and cell[0] in "hc":
                parsed.append((cell[1], HAN if cell[0] == "h" else CHO))
            else:
                raise BadRequest(f"bad cell: {cell!r}")
        cells.append(parsed)
    try:
        return Board.from_grid(cells)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc


def _formations(data):
    cho = data.get("cho_formation", "msm_s")
    han = data.get("han_formation", "msm_s")
    if cho not in FORMATIONS or han not in FORMATIONS:
        raise BadRequest("invalid formation")
    return cho, han


def _rebuild(cho_form, han_form, history):
    board = Board.standard(cho_form, han_form)
    tracker = RepetitionTracker()
    tracker.record(board)
    hashes = [board.zobrist()]
    last_capture = 0
    for m in history or []:
        try:
            fr, fc, tr, tc = int(m["fr"]), int(m["fc"]), int(m["tr"]), int(m["tc"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BadRequest("history entries need integer fr/fc/tr/tc") from exc
        captured = board.grid[tr][tc]
        board.make(Move(fr, fc, tr, tc, captured[0] if captured else None))
        tracker.record(board)
        hashes.append(board.zobrist())
        if captured is not None:
            last_capture = len(hashes) - 1
    return board, tracker, hashes, last_capture


# ------------------------------------------------------------------ handlers
def api_new(data):
    cho, han = data.get("cho", "msm_s"), data.get("han", "msm_s")
    if cho not in FORMATIONS or han not in FORMATIONS:
        raise BadRequest("invalid formation")
    return {"board": grid_to_json(Board.standard(cho, han))}


def api_legal(data):
    try:
        fr, fc = int(data["fr"]), int(data["fc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequest("fr and fc are required integers") from exc
    if not (0 <= fr < ROWS and 0 <= fc < COLS):
        raise BadRequest("fr/fc are off the board")

    history = data.get("history")
    if history is not None:
        cho_form, han_form = _formations(data)
        board, tracker, _h, _lc = _rebuild(cho_form, han_form, history)
        piece = board.grid[fr][fc]
        if piece is None:
            return {"moves": []}
        moves = tracker.legal_nonrepeating(board, piece[1])
    else:
        if data.get("board") is None:
            raise BadRequest("board or history required")
        board = json_to_board(data["board"])
        piece = board.grid[fr][fc]
        if piece is None:
            return {"moves": []}
        moves = board.legal_moves(piece[1])
    return {
        "moves": [
            {"tr": m.tr, "tc": m.tc, "captured": m.captured}
            for m in moves if m.fr == fr and m.fc == fc
        ]
    }


def api_analyze(data):
    side = CHO if data.get("side", "cho") == "cho" else HAN
    try:
        requested = float(data.get("time", 3.0))
    except (TypeError, ValueError):
        requested = 3.0
    time_limit = max(0.5, min(requested, BROWSER_MAX_TIME))

    forbidden = set()
    history_hashes = []
    history = data.get("history")
    game_ply = len(history) if isinstance(history, list) else 0

    if history is not None:
        cho_form, han_form = _formations(data)
        board, tracker, hashes, last_capture = _rebuild(cho_form, han_form, history)
        history_hashes = hashes[last_capture:-1]
        legal = board.legal_moves(side)
        hard = {m.as_tuple() for m in legal if tracker.would_repeat_thrice(board, m)}
        if [m for m in legal if m.as_tuple() not in hard]:
            forbidden = hard
    else:
        board = json_to_board(data.get("board"))
    board.side_to_move = side

    engine = Engine(max_depth=BROWSER_MAX_DEPTH, time_limit=time_limit)
    move, score = engine.search(
        board, side,
        forbidden_moves=forbidden,
        history_hashes=history_hashes,
        game_ply=game_ply,
    )
    if move is None and board.legal_moves(side):
        move, score = engine.search(board, side, game_ply=game_ply)
    if move is None:
        return {"move": None, "score": score, "gameOver": True}

    if score <= -800:
        danger = {"level": "critical", "text": "외통/큰 위기 — 궁 수비 최우선"}
    elif score <= -300:
        danger = {"level": "bad", "text": "열세 — 공격 멈추고 방어 전환"}
    elif score <= -120:
        danger = {"level": "warn", "text": "약간 불리 — 궁성 주의"}
    else:
        danger = {"level": "ok", "text": ""}

    return {
        "move": {"fr": move.fr, "fc": move.fc, "tr": move.tr, "tc": move.tc,
                 "captured": move.captured},
        "score": score,
        "danger": danger,
        "depthReached": engine.stats.depth_reached,
        "nodes": engine.stats.total_nodes,
        "pv": [{"fr": a, "fc": b, "tr": c, "tc": d} for a, b, c, d in engine.stats.pv],
        "gameOver": False,
        "engine": "browser",
    }


def api_score(data):
    return judge(json_to_board(data.get("board")))


ROUTES = {
    "/api/new": api_new,
    "/api/legal": api_legal,
    "/api/analyze": api_analyze,
    "/api/score": api_score,
}


def handle(path: str, body: str) -> str:
    """Entry point called from JavaScript. Returns a JSON string."""
    for route, fn in ROUTES.items():
        if path.endswith(route):
            try:
                return json.dumps(fn(json.loads(body or "{}")), ensure_ascii=False)
            except BadRequest as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001 - surfaced in the UI
                return json.dumps({"error": f"{type(exc).__name__}: {exc}"},
                                  ensure_ascii=False)
    return json.dumps({"error": f"unknown endpoint {path}"})
