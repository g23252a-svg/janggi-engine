# Janggi Engine (장기 엔진)

A Korean chess (Janggi) engine written in pure Python, built for study and
analysis. It implements the standard strengthening techniques used by real
board-game engines, with a clean rules core that the search and evaluation
both trust as the single source of truth.

This is a **practice and analysis** tool: run it to study positions, check the
best move for either side, or watch it play itself. It is not meant for use
against live human opponents on online services.

## What makes it reasonably strong

- **Alpha-beta negamax** search with beta cutoffs.
- **Iterative deepening** — searches depth 1, 2, 3 … so there is always a move
  ready, and each pass warms up move ordering and the transposition table for
  the next.
- **Transposition table** with **Zobrist hashing** — positions reached by
  different move orders are searched once, not repeatedly.
- **Quiescence search** — at the leaves it keeps resolving captures until the
  position is quiet, which removes the "horizon effect" where a shallow search
  mis-reads the middle of an exchange.
- **Move ordering** — transposition-table best move first, then captures by
  MVV-LVA (most valuable victim, least valuable attacker), which makes
  alpha-beta prune far more.
- **Janggi-specific evaluation** — material plus soldier advancement, central
  control, line-piece mobility, general safety / guard cover, and a bonus for
  cannons that have a usable screen.

## Rules implemented

Full standard Janggi movement: chariot (車) including palace diagonals, cannon
(包/砲) screen-jump rules and the "cannon cannot jump or capture a cannon"
restriction, horse (馬) and elephant (象) leg-blocking, palace confinement for
general and guard, soldiers (졸/兵) that never move backward, the
facing-generals (빅장) prohibition, and check / no-legal-move detection. Four
starting formations are selectable for each side: 마상상마, 상마상마, 마상마상,
상마마상.

## Install

No third-party runtime dependencies — Python 3.10+ standard library only.

```bash
git clone https://github.com/<your-username>/janggi-engine.git
cd janggi-engine
pip install -e ".[dev]"   # installs pytest for the test suite
```

## Usage

Analyze the opening for Cho at depth 4:

```bash
python -m janggi.cli --analyze cho --depth 4
```

Use a time budget per move instead of a fixed depth (recommended — iterative
deepening goes as deep as it can in the time given):

```bash
python -m janggi.cli --analyze han --time 3.0 --han-formation smsm
```

Watch the engine play itself:

```bash
python -m janggi.cli --selfplay --moves 30 --time 1.0
```

### As a library

```python
from janggi import Board, Engine, CHO

board = Board.standard(cho_formation="msm_s", han_formation="smsm")
engine = Engine(max_depth=20, time_limit=3.0)   # depth cap + time budget
move, score = engine.search(board, CHO)
print(move, score)
board.make(move)
```

## Web server / Railway deployment

A Flask server (`server.py`) exposes an analysis API and serves a board UI.

Run locally:

```bash
pip install -r requirements.txt
python server.py            # http://localhost:8080
```

Endpoints: `GET /` (board UI), `POST /api/new` (start position for chosen
formations), `POST /api/analyze` (best move + score for a side), `GET /health`.

### Deploy on Railway

The repo includes `Procfile`, `railway.json`, and `runtime.txt`. On Railway:

1. New Project → Deploy from GitHub repo → pick `janggi-engine`.
2. Railway auto-detects Python, installs `requirements.txt`, and runs the
   `gunicorn` start command. No env vars are required (`PORT` is provided).
3. Open the generated domain to use the analyzer.

Per-request work is capped (`MAX_TIME`, `MAX_DEPTH` in `server.py`) so a single
analysis cannot hang the instance.

## Project layout

```
janggi/
  board.py      rules core: board, move generation, legality (single source of truth)
  evaluate.py   static evaluation (all positional knowledge lives here)
  search.py     iterative deepening, TT + Zobrist, quiescence, alpha-beta
  cli.py        command-line demo
server.py       Flask web server (analysis API + UI)
templates/
  index.html    board front-end
tests/
  test_engine.py  rules tests, perft, make/unmake, Zobrist, engine sanity
```

## Tests

```bash
python -m pytest tests/ -q
```

## Performance notes

Pure Python is the bottleneck. A fixed depth-4 search of the (capture-heavy)
opening takes a few seconds; using `--time` keeps each move bounded and lets
the engine reach deeper in quieter midgame positions. Natural next steps to go
faster and stronger: killer-move and history heuristics, null-move pruning, a
bitboard or array-based board for faster make/unmake, an opening book, and an
endgame database.

## Roadmap

- [ ] Killer moves + history heuristic for better ordering
- [ ] Null-move pruning
- [ ] Opening book from recorded games
- [ ] Faster board representation
- [ ] Optional UI / web front-end

## License

MIT — see [LICENSE](LICENSE).
