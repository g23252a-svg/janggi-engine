# Changelog

## Unreleased — large improvement patch

Measured against the previous engine (commit `9b5a7c3`), both compiled, at an
equal 0.5 s per move over 15 seeded openings played twice with colours swapped:

```
NEW vs OLD: +30 =0 -0 of 30   (score 100.0%)
```

Opening position, fixed depth, same machine:

| depth | before | after |
| ---: | ---: | ---: |
| 8 | 1.58M nodes, 7.97 s | 193k nodes, 0.93 s |
| 10 | — | 584k nodes, 2.5 s |
| 12 | — | 2.7M nodes, 12.0 s |

The same wall clock now reaches roughly four plies deeper.

### Fixed

- **The board and its accelerator arrays could silently disagree.** A `Board`
  kept the Python grid, the flat int arrays the Cython extensions read, and a
  Zobrist key as three separate things, but only `make`/`unmake` kept them
  together. Writing `board.grid[r][c]` directly left the int arrays stale, so
  the compiled attack test looked at an empty board and check detection failed
  without erroring. This had already caused one production incident (patched
  narrowly inside `json_to_board`) and it made 12 of the 36 unit tests fail
  whenever the extensions were compiled. `board.grid` is now a write-through
  view; whole-grid assignment resyncs as well, which also fixes
  `Gibo.replay()` snapshots.
- **CI never built the extensions**, so the configuration production actually
  runs was untested and the failures above never showed up. CI now builds them,
  asserts they loaded, and runs the suite against both the compiled and the
  pure-Python path.
- **Mate scores did not survive the transposition table.** They were computed
  as `MATE - (max_depth - depth)`, which is not the distance to mate once
  extensions and reductions move `depth` around, and they were stored without
  rebasing onto the probing ply. Both now use the ply.
- **The evaluator was not stable within a search.** Its endgame score-lock keyed
  off the board's history length *including the search stack*, so the same
  position evaluated differently at different depths and transposition entries
  disagreed with each other. It keys off the game ply now, which is fixed for
  the duration of a search.
- **The web API answered malformed input with a 500 and a stack trace.** An
  unknown piece letter, a non-integer `depth` and a history entry missing a
  field were all reachable crashes. They are 400s with a message.
- `is_attacked()` and `fast_is_attacked()` answer different questions — "can
  this side move here" versus "does it bear on here", which differ on squares
  holding one's own pieces, and the evaluator depends on the second. The
  docstrings claimed the two were identical, which invited a "fix" that would
  have broken the evaluator. Documented, with `Board.controls()` as the
  matching slow oracle and a test pinning the split.
- Killer moves are indexed by ply rather than by depth.
- Self-play (CLI and the match runner) tracks repetition, so it can no longer
  shuffle forever.

### Added

- **Search**: aspiration windows, principal variation search, null-move pruning
  (passing is a legal option in Janggi), reverse futility, futility and
  late-move pruning, a depth × move-index reduction table, the counter-move
  heuristic, delta pruning in quiescence, mate-distance pruning, and repetition
  detection inside the search so a repeated position scores as the draw it is.
- **The principal variation** is computed, validated move by move against the
  real move generator, and returned from `Engine.stats.pv` and `/api/analyze`.
- **`janggi/match.py`** — plays two engine configurations against each other in
  colour-swapped pairs from seeded openings and reports a score with a
  confidence interval. `SearchOptions` makes every search technique switchable
  so a change is measured rather than assumed.
- **`tests/test_tactics.py`** — forced wins certified by an exhaustive prover
  that never consults the engine, checked with each pruning technique disabled
  in turn. Pruning bugs show up as a forced win quietly disappearing, and this
  is what catches that.
- **`tests/test_parity.py`** — perft reference counts, exact Python/Cython
  equality for move generation, attack tests, evaluation and SEE, a frozen
  Zobrist fingerprint (the opening book is keyed by these hashes), and the
  board write-through invariants.
- **`tests/test_server.py`** — endpoint behaviour and input validation.
- `python -m janggi.cli --bench` for before/after comparisons.
- `JANGGI_NO_ACCEL=1` forces the pure-Python path, so one suite covers both
  implementations.
- `Board.from_grid()`, `Board.copy()`, `Board.set_piece()`, `Board.zobrist()`,
  `Board.controls()`, `Board.pieces()`.

### Changed

- **The root moved into the compiled core.** It used to sit in Python and call
  the core once per root move, which ruled out aspiration windows and a
  principal variation and forced a workaround that was costing real strength:
  from depth 4 onward only the top 10 root moves from the previous iteration
  were searched, so a move ranked 11th at shallow depth could never be found
  however good it was. Every root move is searched now.
- **Static evaluation is ~4x faster** (6.8 µs → 1.7 µs) and produces identical
  scores. It asked "is this square attacked" around 70 times per call, each
  rescanning the board; it now computes one attack map in a single forward pass
  over the pieces. Verified square-for-square against the scalar test over 235k
  entries.
- Zobrist keys are maintained incrementally instead of rescanning 90 squares
  per search node. The table, seed and draw order are unchanged and pinned by a
  test.
- `Board.find_general()` is O(1) rather than a 90-square scan.
- Transposition replacement is depth-preferred instead of always-replace.
- The server no longer pins a search depth per request tier; the time budget
  and iterative deepening decide, which is what they are for. `MAX_DEPTH` rose
  from 9 to 30 because depth 9 is now reachable inside the budget.
- The server serialises searches behind a lock. The compiled core keeps its
  tables in process-global C arrays, so exactly one search may be in flight per
  process. Deployment runs one thread per worker, so the lock is uncontended —
  it is there so that adding `--threads` later degrades throughput instead of
  silently corrupting every concurrent search.

### Removed

- `janggi/_fasteval.pyx` (347 lines) — a second copy of the attack test and the
  evaluator, exactly equal to `core_eval(...) + 2 * mobility` (verified over 9k
  positions), so it could only ever drift out of sync with `_core.pyx`.
- Three root-risk heuristics (`_root_landing_recapture_risk`,
  `_root_home_intruder_risk`, `_root_home_invasion_risk`) that nothing had
  called since the experiment they belonged to was reverted.
- The root top-K truncation described above.
