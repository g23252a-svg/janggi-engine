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

from flask import Flask, jsonify, request, render_template

from janggi.board import Board, Move, HAN, CHO, FORMATIONS, ROWS, COLS
from janggi.search import Engine, zobrist_hash
from janggi.score import judge, SCORE_POINTS, HAN_BONUS
from janggi.repetition import RepetitionTracker
from janggi.book import load_book, book_move

app = Flask(__name__)

# Bound the work the public endpoint will do so a request cannot hang the dyno.
MAX_TIME = 6.0
MAX_DEPTH = 7

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


def json_to_board(grid: list[list[str | None]]) -> Board:
    b = Board()
    for r in range(ROWS):
        for c in range(COLS):
            cell = grid[r][c]
            if cell:
                side = HAN if cell[0] == "h" else CHO
                b.grid[r][c] = (cell[1], side)
    return b


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
        return jsonify({"error": "invalid formation"}), 400
    board = Board.standard(cho, han)
    return jsonify({"board": grid_to_json(board)})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(force=True, silent=True) or {}
    grid = data.get("board")
    side_str = data.get("side", "cho")
    if not grid or len(grid) != ROWS or any(len(row) != COLS for row in grid):
        return jsonify({"error": "board must be a 10x9 grid"}), 400

    side = CHO if side_str == "cho" else HAN

    # Live-game time manager.
    # The app is being used in 5-minute + 30-second byo-yomi games. A 28-second
    # search is too dangerous because network/input delay can lose on time. Use
    # the request "time" value as an actual thinking-budget hint and keep the
    # server safely below the byo-yomi limit.
    try:
        requested = float(data.get("time", 3.0))
    except (TypeError, ValueError):
        requested = 3.0

    requested = max(0.5, min(requested, MAX_TIME))

    if requested <= 1.5:
        depth = 4
        time_limit = 1.2
    elif requested <= 3.0:
        depth = 5
        time_limit = 2.2
    else:
        depth = 6
        time_limit = 4.5

    if "depth" in data:
        # Explicit depth is allowed, but the time cap still wins.
        depth = min(int(data["depth"]), MAX_DEPTH)

    depth = min(depth, MAX_DEPTH)
    time_limit = min(time_limit, MAX_TIME)

    board = json_to_board(grid)
    board.side_to_move = side

    # If a move history is supplied, work out which of this side's moves repeat.
    # Two tiers:
    #   hard  — would cause a 3rd repetition: never recommend (rule).
    #   soft  — would merely return to an earlier position: avoid when any
    #           non-repeating move exists, so the engine stops shuffling a piece
    #           back and forth and instead makes progress when it is winning.
    forbidden: set[tuple[int, int, int, int]] = set()
    soft_forbidden: set[tuple[int, int, int, int]] = set()
    history = data.get("history")
    if history is not None:
        cho_form = data.get("cho_formation", "msm_s")
        han_form = data.get("han_formation", "msm_s")
        if cho_form in FORMATIONS and han_form in FORMATIONS:
            hist_board, tracker = _rebuild_with_history(cho_form, han_form, history)
            legal_here = hist_board.legal_moves(side)
            for m in legal_here:
                if tracker.would_repeat_thrice(hist_board, m):
                    forbidden.add(m.as_tuple())
                elif tracker.would_repeat_twice(hist_board, m):
                    soft_forbidden.add(m.as_tuple())

            # Shuffle detection (independent of whole-board repetition): if this
            # side has been moving one piece back and forth between two squares,
            # forbid continuing that shuffle. This matters because the opponent
            # may vary their own moves just enough that the *whole position*
            # never repeats 3x, yet our side is stuck wasting moves — and an
            # external app enforcing its own repetition rule can block the
            # shuffle move we'd otherwise recommend, leaving the user unable to
            # play our arrow. We look at this side's recent moves and ban any
            # recommendation that immediately undoes the last one (A->B then
            # B->A), or revisits a square this piece sat on in its last few moves.
            side_str_hist = "cho" if side == CHO else "han"
            own_recent = [
                mm for mm in (history or []) if mm.get("side") == side_str_hist
            ][-3:]
            if own_recent:
                last = own_recent[-1]
                # Immediate undo: piece now at (last.tr,last.tc) going back to
                # (last.fr,last.fc).
                undo = (last["tr"], last["tc"], last["fr"], last["fc"])
                soft_forbidden.add(undo)
                # Revisiting any from-square this side vacated in its last moves.
                vacated = {(mm["fr"], mm["fc"]) for mm in own_recent}
                for m in legal_here:
                    if (m.tr, m.tc) in vacated and (m.fr, m.fc) == (last["tr"], last["tc"]):
                        soft_forbidden.add(m.as_tuple())

            # Apply hard bans always; apply soft bans only while a legal move
            # still remains afterwards.
            combined = forbidden | soft_forbidden
            non_repeating = [
                m for m in legal_here if m.as_tuple() not in combined
            ]
            if non_repeating:
                forbidden = combined

    # Consult the opening book first: if this exact position was seen in
    # recorded games, recommend the most-played (and still legal) reply
    # instantly, skipping the search entirely. This strengthens the otherwise
    # slow/weak opening phase using verified human moves.
    bmove = book_move(OPENING_BOOK, board) if OPENING_BOOK else None
    if bmove is not None and bmove.as_tuple() not in forbidden:
        return jsonify(
            {
                "move": {
                    "fr": bmove.fr, "fc": bmove.fc,
                    "tr": bmove.tr, "tc": bmove.tc,
                    "captured": bmove.captured,
                },
                "score": 0,
                "danger": {"level": "ok", "text": ""},
                "depthReached": 0,
                "nodes": 0,
                "fromBook": True,
                "gameOver": False,
            }
        )

    engine = Engine(max_depth=depth, time_limit=time_limit)
    move, score = engine.search(board, side, forbidden_moves=forbidden)

    # If forbidding repetition left no recommendation but legal moves exist
    # (i.e. the only legal move happens to be a repeating one — e.g. the sole
    # escape from check), retry without the repetition filter so the user still
    # sees the forced move rather than an empty result.
    if move is None and board.legal_moves(side):
        move, score = engine.search(board, side)

    if move is None:
        return jsonify({"move": None, "score": score, "gameOver": True})

    # Danger warning. The search score is from the side-to-move's point of view
    # (negative = the player is losing). Surfacing this as an explicit warning
    # lets the player switch to defending their palace BEFORE a mating net closes
    # — the recurring loss pattern was being a piece up on the board while the
    # general was quietly getting smothered, with no signal that anything was
    # wrong. The thresholds are in centipawn-ish search units.
    if score <= -800:
        danger = {"level": "critical", "text": "외통/큰 위기 — 궁 수비 최우선"}
    elif score <= -300:
        danger = {"level": "bad", "text": "열세 — 공격 멈추고 방어 전환"}
    elif score <= -120:
        danger = {"level": "warn", "text": "약간 불리 — 궁성 주의"}
    else:
        danger = {"level": "ok", "text": ""}

    return jsonify(
        {
            "move": {
                "fr": move.fr, "fc": move.fc,
                "tr": move.tr, "tc": move.tc,
                "captured": move.captured,
            },
            "score": score,
            "danger": danger,
            "depthReached": engine.stats.depth_reached,
            "nodes": engine.stats.nodes,
            "gameOver": False,
        }
    )


def _rebuild_with_history(cho_form, han_form, history):
    """Rebuild a board from the starting formations by replaying a move history,
    and a RepetitionTracker that has seen every position along the way."""
    board = Board.standard(cho_form, han_form)
    tracker = RepetitionTracker()
    tracker.record(board)
    for m in history or []:
        board.make(Move(m["fr"], m["fc"], m["tr"], m["tc"], m.get("captured")))
        tracker.record(board)
    return board, tracker


@app.route("/api/legal", methods=["POST"])
def api_legal():
    """Return legal moves for a square, excluding 3-fold-repetition moves.

    If `history` (list of past moves with formations) is supplied, repetition
    is enforced; otherwise it falls back to plain legality on the given board.
    """
    data = request.get_json(force=True, silent=True) or {}
    fr = data.get("fr")
    fc = data.get("fc")
    if fr is None or fc is None:
        return jsonify({"error": "fr and fc required"}), 400

    history = data.get("history")
    if history is not None:
        cho_form = data.get("cho_formation", "msm_s")
        han_form = data.get("han_formation", "msm_s")
        if cho_form not in FORMATIONS or han_form not in FORMATIONS:
            return jsonify({"error": "invalid formation"}), 400
        board, tracker = _rebuild_with_history(cho_form, han_form, history)
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
    grid = data.get("board")
    if not grid or len(grid) != ROWS or any(len(row) != COLS for row in grid):
        return jsonify({"error": "board or history required"}), 400
    board = json_to_board(grid)
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
    cho_form = data.get("cho_formation", "msm_s")
    han_form = data.get("han_formation", "msm_s")
    if cho_form not in FORMATIONS or han_form not in FORMATIONS:
        return jsonify({"error": "invalid formation"}), 400
    history = data.get("history", [])
    board, tracker = _rebuild_with_history(cho_form, han_form, history)
    count = tracker.count(board)
    return jsonify({"count": count, "repetition": count >= 3})


@app.route("/api/score", methods=["POST"])
def api_score():
    data = request.get_json(force=True, silent=True) or {}
    grid = data.get("board")
    if not grid or len(grid) != ROWS or any(len(row) != COLS for row in grid):
        return jsonify({"error": "board must be a 10x9 grid"}), 400
    board = json_to_board(grid)
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
