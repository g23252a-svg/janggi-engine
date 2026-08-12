# Janggi Engine (장기 엔진)

A Korean chess (Janggi) engine: a readable pure-Python rules core, a compiled
Cython search that runs the same rules at speed, and a small web front end for
studying positions.

This is a **practice and analysis** tool: run it to study positions, check the
best move for either side, or watch it play itself. It is not meant for use
against live human opponents on online services.

## What it does

- **Alpha-beta negamax with iterative deepening**, so there is always a move
  ready and each pass orders the next one.
- **Aspiration windows** around the previous iteration's score, widening on
  failure.
- **Principal variation search** — after the first move, a null window is
  enough to show the rest are worse.
- **Transposition table** with Zobrist hashing, depth-preferred replacement,
  and mate scores rebased onto the probing ply.
- **Null-move pruning.** Passing is a legal option in Janggi, so the usual
  chess worry about zugzwang does not apply the same way here.
- **Futility, reverse futility and late-move pruning**, plus late move
  reductions from a depth × move-index table.
- **Quiescence search** with SEE and delta pruning: keeps resolving captures
  until the position is quiet, so a shallow search cannot misread the middle of
  an exchange.
- **Move ordering** — transposition move, then captures by static exchange
  evaluation, then killers, counter-moves and history.
- **Check extensions** on a per-branch budget.
- **Repetition detection inside the search**, so a repeated position scores as
  the draw it is rather than being re-evaluated as something new.
- **Janggi-specific evaluation** — material, soldier advancement, central
  control, line-piece mobility, general safety and guard cover, cannon screens,
  loose-piece risk, and an endgame lock onto official material points.
- **Opening book** built from recorded games (기보).

## Rules implemented

Full standard Janggi movement: chariot (車) including palace diagonals, cannon
(包/砲) screen-jump rules and the "cannon cannot jump or capture a cannon"
restriction, horse (馬) and elephant (象) leg-blocking, palace confinement for
general and guard, soldiers (졸/兵) that never move backward, the
facing-generals (빅장) position, and check / no-legal-move detection. Four
starting formations are selectable for each side: 마상상마, 상마상마, 마상마상,
상마마상.

Note on 빅장: facing generals is treated as a legal position, not as check and
not as an illegal move. `Board.generals_face()` detects it for callers that
want to apply a draw rule.

## Install

The engine runs on the Python standard library alone. Building the Cython
extensions is optional but strongly recommended — they are roughly two orders
of magnitude faster, and everything falls back to pure Python automatically if
they are missing.

```bash
git clone https://github.com/<your-username>/janggi-engine.git
cd janggi-engine
pip install -e ".[dev]"          # pytest for the test suite
python setup.py build_ext --inplace   # optional, much faster
```

## Usage

Analyze the opening for Cho with a time budget (recommended — iterative
deepening goes as deep as it can in the time given):

```bash
python -m janggi.cli --analyze cho --time 3
python -m janggi.cli --analyze han --time 3 --han-formation smsm
```

Watch the engine play itself, and benchmark it:

```bash
python -m janggi.cli --selfplay --moves 40 --time 1.0
python -m janggi.cli --bench
```

### As a library

```python
from janggi import Board, Engine, CHO

board = Board.standard(cho_formation="msm_s", han_formation="smsm")
engine = Engine(max_depth=20, time_limit=3.0)   # depth cap + time budget
move, score = engine.search(board, CHO)
print(move, score, engine.stats.pv)
board.make(move)
```

`board.grid[r][c]` reads and writes squares directly and keeps the compiled
accelerators, the Zobrist key and the tracked general positions in step, so
setting up a position by hand is safe:

```python
board = Board()
board.grid[8][4] = ("K", CHO)
board.grid[1][4] = ("K", HAN)
board.grid[8][0] = ("C", HAN)
assert board.in_check(CHO)
```

## Measuring a change

Nothing about search or evaluation should be changed on intuition alone. The
match runner plays two engine configurations against each other in
colour-swapped pairs from seeded openings, and reports a score with a
confidence interval:

```bash
# does null-move pruning actually help, at an equal node budget?
python -m janggi.match --games 100 --nodes 150000 --a "" --b "nmp=0"

# more depth for one side
python -m janggi.match --games 40 --depth-a 10 --depth-b 8
```

Every technique can be switched off individually — `tt`, `lmr`, `ext`, `nmp`,
`pvs`, `fut`, `lmp`, `asp`, `rep`, plus `extbudget=` and `nodes=`. The same
options are available in code through `SearchOptions`.

What that measurement currently says, at an equal 60k nodes per move:

| technique | score with it on | verdict |
| --- | ---: | --- |
| futility + late-move pruning | 65.0% of 40 | clearly better |
| late move reductions | 60.0% of 40 | better, not significant at this sample |
| null-move pruning | 48.3% of 60 | no measurable effect — untuned, retest it |

## Play it in the browser (GitHub Pages)

The board UI is published as a static site with the engine running **inside the
page**, compiled to WebAssembly via Pyodide — no server, nothing to install,
just open the URL.

To switch it on: repository **Settings → Pages → Source: "GitHub Actions"**.
Not "Deploy from a branch" — the site is assembled by `.github/workflows/pages.yml`,
not committed. The first push after that publishes to
`https://<user>.github.io/janggi-engine/`.

Two things to know about the in-browser engine:

- WebAssembly cannot load the Cython extensions, so it runs the pure-Python
  fallback: a few plies shallower than a real deployment. Fine for studying a
  position, not the engine at full strength.
- The page has a box for an engine server URL. Paste the address of a
  deployment (Railway, or `python server.py` on your own machine) and every
  analysis request goes there instead, at full speed. The setting is
  remembered in the browser.

The published page is the same `templates/index.html` the Flask server renders
— the build injects one script tag rather than keeping a second copy, so the
two cannot drift. Build it locally with:

```bash
python web/build_site.py site && python -m http.server -d site 8000
```

## Web server / Railway deployment

A Flask server (`server.py`) exposes an analysis API and serves a board UI.

```bash
pip install -r requirements.txt
python server.py            # http://localhost:8080
```

| Endpoint | Purpose |
| --- | --- |
| `GET /` | board UI |
| `GET /health` | liveness probe |
| `POST /api/new` | start position for the chosen formations |
| `POST /api/analyze` | best move, score, depth, node count and principal variation |
| `POST /api/legal` | legal moves for one square, repetition-aware |
| `POST /api/repetition` | how many times the current position has occurred |
| `POST /api/score` | official material score (점수제) |
| `POST /api/gibo/validate` | validate an uploaded game record |

Malformed input is answered with a 400 and a message. Per-request work is
capped by `MAX_TIME` / `MAX_DEPTH` in `server.py`, and a lock serialises
searches because the compiled core keeps its tables in process-global memory.

### Deploy on Railway

The repo includes `Procfile`, `railway.json`, `nixpacks.toml` and
`runtime.txt`. On Railway: New Project → Deploy from GitHub repo → pick
`janggi-engine`. The build step compiles the Cython extensions in place; if
that fails the app still starts on the pure-Python fallback, just slower. No
env vars are required (`PORT` is provided).

## Project layout

```
janggi/
  board.py      rules core: board, move generation, legality (single source of truth)
  evaluate.py   static evaluation (all positional knowledge lives here)
  search.py     Engine, SearchOptions, and the pure-Python search
  see.py        static exchange evaluation
  repetition.py repetition bookkeeping over Zobrist keys
  score.py      official Janggi point scoring (점수제)
  book.py       opening book built from game records
  gibo.py       game records (기보): save, load, replay, validate
  match.py      A/B match runner for measuring engine changes
  mcts.py       PUCT search for the neural self-play loop
  cli.py        command line: analyze, self-play, bench
  _core.pyx     compiled search core (search, evaluation, SEE, perft)
  _attack.pyx   compiled attack test
  _movegen.pyx  compiled move generator
server.py       Flask web server (analysis API + UI)
templates/
  index.html    board front-end (served by Flask AND published to Pages)
web/
  build_site.py    assembles the GitHub Pages site
  browser-engine.js  answers /api/ calls from Pyodide instead of a server
  engine_api.py    the analysis API without a web framework
tests/
  test_engine.py   rules, make/unmake, Zobrist, engine sanity
  test_parity.py   perft references, Python/Cython equality, board invariants
  test_tactics.py  certified forced wins, incl. with each pruning pass disabled
  test_server.py   web API behaviour and input validation
  test_web_build.py  the Pages build and its in-browser API
```

## Tests

```bash
python -m pytest tests/ -q                    # compiled path
JANGGI_NO_ACCEL=1 python -m pytest tests/ -q  # pure-Python fallback
```

`JANGGI_NO_ACCEL=1` forces the fallback so one suite covers both
implementations. CI runs both, and asserts the extensions actually loaded in
the compiled job — a silent build failure otherwise looks exactly like a pass.

## Performance

Opening position, fixed depth, on a 2026 cloud vCPU:

| depth | nodes | time |
| ---: | ---: | ---: |
| 8 | 193k | 0.9s |
| 10 | 584k | 2.5s |
| 12 | 2.7M | 12.0s |

For comparison, the same depth-8 search cost 1.58M nodes and 8.0s before the
search rewrite and the attack-map evaluator — the same wall clock now reaches
about four plies deeper. Played head to head at an equal 0.5 s per move, over
15 seeded openings each played twice with colours swapped:

```
NEW vs OLD (commit 9b5a7c3): +30 =0 -0 of 30
```

## Roadmap

- [x] Killer moves + history heuristic for better ordering
- [x] Null-move pruning
- [x] Opening book from recorded games
- [x] Faster board representation
- [x] Web front-end
- [ ] Tapered evaluation by game phase
- [ ] Endgame tablebase for common material
- [ ] Cross-version match harness in CI to catch strength regressions

## License

MIT — see [LICENSE](LICENSE).
