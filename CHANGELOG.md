# Changelog

Versions before 0.4.0 predate this file; `setup.py` sat at 0.3.0 through all
three of the patches below, which is what 1.0.0's versioning work is about.

## Unreleased — 1.1.0

A user played a game through the web UI following the engine's own
recommendations and lost it. The release is that game, fixed by proof, and
the measurement discipline it took to ship nothing else.

### What ships

**rootguard.** At the end of an interrupted iteration the engine used to keep
the previous depth's best move unless the partial pass had found a *better*
one. If the partial pass had re-searched that move and proven it **lost by
force**, the proof was thrown away and the move was played — that is how it
walked into a 15-ply mate at the UI's budget while two moves held. Now a root
move that the current depth has proven lost is never played; the best move
that is not a proven loss is. Under the aspiration window a fail-low is only
a bound, so the PV move that fails low is re-searched once with the floor
removed, in the same iteration, and its score is exact. On the lost game's
position, at the UI's actual 3.5 s: deployed plays the mate; 1.1.0 plays
(0,5,1,5) and holds. CHO still proves the mate in 214k nodes. Depth 12 from
the opening: 2,822,961 nodes deployed, 2,823,173 with the guard — 212 more.
Measured against the deployed engine: ROOTGUARD_ROWS_PENDING.

**Measurement that survives the box.** The container running matches is
reclaimed between sessions; two 60-game runs died mid-way with nothing to show
before `--log` / `--resume` / `--pool` existed. Every game is one JSON line
with its opening seed, colours, result, ending reason and budget; a killed run
resumes from the last finished game; shards with disjoint seed ranges pool
into one summary. A log from a different configuration refuses to resume.

**Intervals computed with the right unit.** The two games of a colour-swapped
pair share an opening, so they are not independent trials, and every interval
this repository had printed was too narrow. `summarize()` now reports the
interval over pairs (sample variance, at least two pairs, no verdict under 20
games). It paid for itself at once: a flag that had measured "50.0%, not
distinguishable" turned out to have played the identical game in every pair —
the pair interval is zero-width, the per-game one said 34..66.

**A prover sweep that can fail.** The mate-in-1/2 sweep in `test_tactics.py`
passes every flag in milliseconds, including one that demonstrably hides a
mate. The sweep that catches that is the 15-ply proof from the lost game,
under every configuration that could ship, with a 2× node margin. And the
exact deployed 1.0.0 search is pinned, flag by flag, at 2,822,961 nodes:
every A/B in this campaign used `""` to mean that engine.

### What was tried and did not ship

Eight search and evaluation changes were written, each behind a flag, each
default-off and node-identical when off, each measured alone against the
deployed engine. One ships. The rest are in the table below with the number
that decided them; the code and flags stay so the next attempt starts from a
measurement rather than a memory.

Two of them were recorded as shipping before being reversed, and both
reversals are worth more than the changes:

- `mthreat=1` was "a defect fix at zero cost" on 30-30 of 60. The design
  review then measured the half of the contract the verdict had not: after the
  fatal move the attacker must prove the mate, and with the flag on it scores
  +2072 instead. The 30-30 was also no evidence — 17 of 30 pairs were the same
  game. And the mechanism could never have worked: the threat is detected only
  when the static eval is at or above beta, futility prunes only below alpha.
- `asp=0 + rootguard=1 (+ extbudget=4)` fixed the lost game by proof and
  scored 60% at 60k. At 300k, the regime the defect lives in, the pair scored
  43% and the bundle 45%, and the leave-one-out showed the window was the
  drag: turning aspiration off is worth more the deeper the search goes, and
  the UI searches deeper still. Keeping the window and making the guard
  reachable under it is what ships instead.

Every row below is a colour-swapped match at an equal node budget per move,
60 games over three 20-game shards with disjoint opening seeds, pooled with
`python -m janggi.match --pool` and reported with the pair-aware interval.

### Measured, not assumed

| change | alone vs 1.0.0 | verdict |
| --- | ---: | --- |
| `histmalus` — bounded signed history (gravity + malus) | 60 games: +33 =0 -27, 55.0%; extended once, as pre-declared, to **120 games: +67 =0 -53, 55.8%, +41 elo (pairs CI 47.5..64.2)** | not distinguishable at 120 — **off by default**, code and flag kept |
| `rootguard=1` (window on, exact re-search of the PV move on a fail-low) vs deployed, 60k | +28 =0 -32 of 60, 46.7%, -23 elo (pairs CI 34.3..59.0); 2 of 30 pairs identical | not distinguishable; 300k pending |
| **the bundle** `asp=0,rootguard=1,extbudget=4` vs deployed, 60k nodes | +31 =0 -29 of 60, 51.7%, +12 elo (pairs CI 37.4..65.9) | not worse at 60k — **passes** its rule; 300k pending |
| **the bundle** vs deployed, **300k** nodes — the regime the defect lives in | +27 =0 -33 of 60, 45.0%, -35 elo (pairs CI 32.3..57.7) | not distinguishable, but below its best member at both budgets — the plan's leave-one-out rule fires: the pair without `extbudget=4` is measured at 300k |
| the pair `asp=0,rootguard=1` vs deployed, **300k** (leave-one-out) | +26 =0 -34 of 60, 43.3%, -47 elo (pairs CI 32.1..54.6) | same as the bundle: `extbudget=4` was not the drag, **`asp=0` is** — 60% at 60k, 43% at 300k; the window is worth more the deeper the search. Neither ships. Contingency: keep the window and make rootguard reachable under it |
| `asp=0,rootguard=1` — no aspiration window, and never play a root move the current depth has proven lost | +36 =0 -24 of 60, 60.0%, +70 elo (pairs CI 46.6..73.4) | not distinguishable alone — judged as part of the defect-fix bundle below |
| `extbudget=4` — one more check extension per path | +31 =0 -29 of 60, 51.7% (pairs CI 44.4..58.9); 9 of 23 pairs the same game | inert at 60k — judged at 300k, where the defect lives |
| `mob=2` — coverage mobility in the compiled evaluator | +36 =0 -24 of 60, 60.0%, +70 elo (pairs CI 49.3..70.7) | not distinguishable, and it costs 11.5% nps that a node-limited match cannot see — **off**, flag kept |
| `lmrcap` — reduce SEE-negative captures like late quiets | +10 =0 -13 of 23, 43.5% — stopped early | **off**: palace sacrifices are SEE-negative captures by definition |
| `soltab=1` — soldier advancement by a per-row table instead of linear to the back rank | +34 =0 -26 of 60, 56.7%, +47 elo (pairs CI 43.8..69.5) | not distinguishable — **off**, flag kept |
| `soltab=1`, second strike | the 300k proof sweep: CHO scores **+2888 instead of mate** with it on — re-valuing the soldiers in the mating net hides the proof | **off**; listed in `HIDES_THE_PROOF` so it cannot ship until the proof survives it |
| `mthreat=2` — the pruning gate alone, on top of the bundle, at **300k** nodes | +20 =0 -20 of 40, 50.0%, pairs CI **50.0..50.0** — every pair 1-1: A and B played the identical game | **inert** — off, flag kept; the pair interval sees this, the per-game one (34.5..65.5) could not |
| `mthreat=1` — a null-move fail-low with a mate score extends and disables margin pruning | +30 =0 -30 of 60, 50.0% — but 17 of 30 pairs were the **same game** (inert at 60k) | **does not ship**: it hides the CHO-side mate proof (see below) |

`histmalus` is the principled fix for the finding in the 1.0.0 correction below
(the history "rescue" branch never fired because the running max was
unreachable). It is more correct and it measures the same as the flaw it fixes.
`mthreat=1` was recorded here as shipping on that 30-30, and that was wrong
twice over. First, the match could not have said anything: at 60k nodes the
null-move search almost never fails low with a mate score, so in 17 of the 30
colour-swapped pairs A and B played the identical game. Second, and decisive,
the design review checked the half of the regression contract the verdict
had not: once the fatal move is on the board, CHO must prove the mate.
Deployed does, in 206k nodes. With `mthreat=1` CHO scores +2072 at 300k
nodes and never proves it — the extension it adds on the defending side
grows the attacker's tree past the budget. Flipping it on would have turned
`test_the_mate_after_the_fatal_move_is_seen_quickly` red. It stays off. A
gate-only form (`mthreat=2`: no extension, only the pruning gate) kept the
proof and measured inert — and the pre-release review found why neither
mode could ever have worked: the threat is detected inside the null-move
block, which runs only when the static eval is **at or above beta**, while
futility prunes only when it is **below alpha**. Those are mutually
exclusive at one node, so the "gate futility" half was unreachable by
construction and the flag only ever gated late-move pruning at depth 3–4.
The idea — do not margin-prune under a mate threat — was never actually
tested here, and the 50.0% results must not be read as testing it. A real
version has to look for the threat where the pruning happens, below alpha.

Six shards: 11-9, 11-9, 11-9, 10-10, 11-9, 13-7. The extension to 120 games
was declared in advance with its rule -- ship only if the pooled pair-aware
interval excludes 50% -- and it does not (lower bound 47.5%). No further
extension: a point estimate that will not move off 55% in 120 games is a
+40-elo change at best, and chasing it with more games is how a repository
ends up shipping noise. By this repository's own precedent — `improving`
at 60.0% and null-move-off at 55.0% both went the same way — it does not ship
on that number.

## 1.0.0 — versioned, and one search change that survived measurement

Against the engine deployed before it (`2394b38`), both compiled, over 60
colour-swapped games from seeded openings at an equal 60k nodes per move:

```
+39 =0 -21 of 60   65.0%   (95% CI 52.9%..77.1%)   +108 elo
```

### Measured, not assumed

Three search changes were written. **One shipped.** Each was tested alone
against no change at all, 40 colour-swapped games at an equal 60k nodes:

| change | alone vs nothing | verdict |
| --- | ---: | --- |
| history-scaled late move reductions (`histlmr`) | 29-11 of 40, 72.5%; and **+39 =0 -21 of 60, 65.0%, +108 elo** against the deployed engine | **shipped** |
| the `improving` signal | 24-16 of 40, 60.0%, +70 elo (CI 44.8..75.2) | dropped |
| eval-scaled null-move reduction (`nmpscale`) | 19-21 of 40, 47.5%, -17 elo | dropped |

The first attempt bundled all three and scored 73.3% of 60 against none, which
looked like a clean win. It was not one: removing any *single* member was
neutral, so the three were redundant substitutes rather than three additive
gains, and the bundle's number said nothing about which to keep. Testing each
alone is what separated them.

`improving` is the instructive one. It cut a depth-12 opening search from 2.8M
nodes to 1.4M — and was worth nothing. Measured directly on top of the change
that does work it scored exactly 50.0% (20-20 of 40). Searching half the nodes
is also what pruning away good moves looks like, which is the entire reason
this repository measures instead of trusting a node count.

### Tried and reverted: turning null-move pruning off

It looked inevitable. NMP has now been measured three times — 48.3% of 60,
57.5% of 40, 45.0% of 60 — and never once distinguished from noise. And it has
a concrete failure: in a real game (below) it hid a forced mate, so the engine
recommended the move that lost, and still did at 10 seconds. Disabling it alone
found the right move; disabling LMR, futility or LMP did not.

The end-to-end test said no. Against the deployed engine, over the **same 60
openings**:

| shipping candidate | vs deployed main | verdict |
| --- | ---: | --- |
| history-scaled LMR, NMP **on** | +39 =0 -21, **65.0%**, +108 elo (CI 52.9..77.1) | **stronger** |
| history-scaled LMR, NMP **off** | +30 =0 -30, 50.0%, -0 elo (CI 37.3..62.7) | noise |

Removing NMP gives back precisely what the new reductions win. So it stays on —
on the strength of a direct comparison against the thing being replaced, not
because the per-feature numbers flattered it. They did not.

This is the second time in this one release that per-feature scores pointed
somewhere the end-to-end comparison refused to go. A feature measured against
a variant of itself tells you about that pair; only running the candidate
against what is actually deployed tells you whether to ship it.

The mate remains a real defect, held by `tests/test_regression_games.py`. It no
longer fires because the new reductions reach the refutation a ply sooner, not
because the cause was fixed. `R = 3 + depth/5` has never been tuned; that is
the open work.

### Added

- **History-scaled late move reductions.** A quiet move that has been causing
  beta cutoffs all over the current search is not a "late" move in any real
  sense — the ordering simply has not caught up with it — so it is searched
  closer to full depth, while a move that has never done anything is pushed
  further down. Worth +168 elo alone, and it barely changes the node count: it
  reallocates depth rather than saving work.

  The threshold is measured against the **largest history value seen this
  search**, not a constant. `histh` is cleared once per search and grows without
  bound (`+= depth*depth`), so a fixed cutoff means one thing in the first
  iteration and another in the twelfth, and something else again at a different
  time limit. A first version used the constant 4000 and scored 67.5% — but a
  heuristic whose meaning drifts inside a single search is the same fault that
  already caused a real bug here (the evaluator's endgame score-lock, 0.4.0), so
  it was rebuilt on the running maximum rather than shipped on a good number.
  The principled version measures better: 72.5% against the same opponent. Move
  ordering still reads raw `histh`, so reductions changed and ordering did not —
  moving both at once would have made this uninterpretable.

  **Correction (found while preparing 1.1.0):** only one of the two halves above
  does anything. Counters on this exact build, depth-12 searches of three
  positions, show the "+1 on zero history" branch firing on 33–42% of eligible
  quiet moves and the "top quarter of the running max → −1" branch on
  **0.2–0.3%**. The running max is a handful of moves with hundreds of cutoffs,
  and three quarters of that is out of reach for everything else. The +108 elo
  is real and comes from reducing never-cut quiets one ply more; the "rescue"
  half was never operative. The measurement stands; the description above was
  wrong about why.

- **A version, in one place.** `janggi/_version.py` is the single source;
  `setup.py`, `python -m janggi.cli --version`, `GET /health` and the board UI
  all read it, and a test pins that they agree. Previously the only version in
  the repository was `setup.py`'s `0.3.0`, and it stayed 0.3.0 through three
  patches that each changed how the engine plays — a version nobody updates is
  worse than no version, because it tells you the build is something it is not.
- `GET /health` also reports `accel`, i.e. whether the compiled core actually
  loaded. Deployments build the extensions and start on the pure-Python
  fallback if that fails, so a deployment can be the right version and still be
  an order of magnitude slower with nothing visibly wrong.

## 0.6.0 — phone patch

The board UI was a desktop page that a phone happened to be able to open. It is
now a phone page: installable to the home screen, sized to the screen it is on,
and rendered at the screen's real pixel density.

Verified with a real browser at five viewports (375×667 @2, 390×844 @3,
412×915 @2.6, 744×1133 @2, 1280×900 @1). On every one of them the whole board
and both 최선 수 buttons are on screen without scrolling, the canvas backing
store matches the physical pixels exactly, nothing scrolls sideways, and no
control is under 44px.

### Added

- **Installable to a home screen** — `web/manifest.webmanifest`, a service
  worker and the icons, served from the app root by Flask and copied into the
  Pages build. The worker caches the shell so the page opens offline; it
  deliberately never caches `/api/`, because a stale best move presented as a
  fresh one is worse than an error.
- Tests that the page can actually fetch everything it references, on both
  builds: the markup is scanned for relative `href`/`src` and each one is
  required to be a 200 under Flask and a file in the Pages build. A missing
  icon is otherwise a silent 404 that only shows up as "it won't install".

### Fixed

- **The board was drawn at 450×500 and stretched.** On a 3× phone that is 450
  logical pixels smeared over ~1100 physical ones, so every piece was soft. The
  canvas backing store is now `devicePixelRatio`-scaled and the drawing code
  runs through one transform, so the board is sharp at 1098×1220 where it used
  to be 450×500.
- **The board was sized by width alone**, so on a phone it filled the width and
  pushed the engine's recommendation below the fold — the two things you need
  to see at once were never on screen together. It is capped by the available
  height as well now, measured from the layout rather than a constant, with a
  300px floor because below that the cells stop being reliable touch targets.
- **`window.innerHeight` is the wrong number on a phone.** It includes the
  space behind the URL bar, and read in the frame that unhides the board it
  reported 822 on a 667px viewport, which sized the board past the bottom of
  the screen. Sizing uses `visualViewport` and happens a frame later.
- **Landscape put the board above the panel**, so neither fitted on a 390px-tall
  screen. They sit side by side under 560px of height; page height at 844×390
  went 858 → 540.
- Controls were 36px tall. Everything tappable is at least 44px, secondary
  controls fold into a 설정 · 기보 disclosure so the board starts near the top
  of the screen, and `touch-action: manipulation` removes the 300ms tap delay
  and double-tap zoom.
- Safe-area insets are respected, so nothing hides under a notch or a home
  indicator, and the page no longer rubber-bands away under a finger that
  misses the board.


## 0.5.0 — strength patch

Against the engine deployed before it (`d6686b8`), both compiled, at an equal
0.5 s per move over colour-swapped seeded openings:

```
+34 =0 -6 of 40   85.0%   (95% CI 73.9%..96.1%)   +301 elo
```

### Measured, not assumed

| change | result | verdict |
| --- | ---: | --- |
| Janggi-aware evaluator vs the old one, equal nodes | 60-20 of 80, +191 elo (CI 65.5%..84.5%) | clearly better |
| same, equal time (0.3 s/move) | 16-14 of 30, +23 elo | underpowered sample; the node test is the signal |
| null-move pruning, on vs off, on the new search | 23-17 of 40, +53 elo | positive now, still not significant |

### Added

- **A Janggi-aware evaluator** (`SearchOptions.eval_version`, default 2). The
  old one knew material, soldier advancement, "middle files are nice" and a
  linear king-danger count — most of a chess engine's first evaluation and
  almost none of Janggi's content. The new one adds:
  - a game phase from remaining material, so a cannon (which needs a screen)
    loses value as the board empties while soldiers gain it;
  - chariot activity: open and semi-open files, the enemy soldier rank;
  - 면포 — a cannon inside its own palace covering the general;
  - horses and elephants scored by how many legs are actually free, because a
    fully blocked horse is nearly a spectator;
  - soldier structure: connected soldiers, soldiers that reach the enemy palace;
  - king danger that grows with the *square* of the attacking weight bearing on
    the palace, so three pieces converging matter far more than three times one.
- A side-symmetry test: a position and its mirror must score exactly opposite.
  It found a real bug within minutes of existing (below).

### Fixed

- **The cannon-screen test was direction-order dependent.** It returned a
  verdict from the first ray that contained any piece, so a cannon with a
  perfectly usable screen to its right was reported screenless whenever the
  nearest piece below it happened to be another cannon. Because the four rays
  were tried in a fixed order, the evaluation was not symmetric between the two
  sides: it disagreed with its own mirror image in **597 of 871** random
  positions. Both evaluators are now at zero, and the symmetry test pins it.
- **The opening book was costing strength and is off by default.** It holds 517
  positions from 18 amateur games, and almost every entry rests on a single
  game (the highest move count in the file is 2), so consulting it replaced a
  depth-12 search with one player's opening for the first 30 moves of every
  game. Sampling six of those games: on the 121 positions where the book
  disagreed with the search, the book move was clearly worse 70 times and
  clearly better 3, losing 138 centipawns on average and 1101 at worst.
  `JANGGI_USE_BOOK=1` restores it; using it well needs many more games per
  position or a quality gate, and `janggi/book.py` is unchanged and ready.

### Changed

- **Legality is checked lazily.** Every node used to make, test and unmake all
  ~35 pseudo-moves just to build an ordered list, when alpha-beta typically
  cuts off after two or three. Testing each move as it is played is ~35% more
  nodes per second — a depth-10 opening search went 2.54 s → 1.33 s, depth 12
  12.0 s → 8.0 s.
- Quiescence skips the SEE call when the victim is worth at least the attacker,
  where a negative result is impossible — an exact shortcut, not an
  approximation. Approximating SEE in the move *ordering* as well was tried and
  reverted: it took a depth-12 opening from 2.5M nodes to 4.2M, because
  ordering quality is worth more than the calls it saves.


## 0.4.0 — large improvement patch

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

### Measured, not assumed

Each technique against the same engine with only that technique disabled,
colour-swapped paired games at an equal 60k node budget per move:

| change | score for "on" | verdict |
| --- | ---: | --- |
| futility + late-move pruning | 65.0% of 40 (+108 elo) | clearly better |
| late move reductions | 60.0% of 40 (+70 elo) | better, not significant at this sample |
| null-move pruning | 48.3% of 60 (-12 elo) | **no measurable effect** |

Null-move pruning is kept on because the 30-0 headline result above was
measured with it on, and shipping a configuration that was never played end to
end would make that number describe something other than the engine. But it is
not earning its keep on this evidence, and it is the first thing to re-test --
`--b "nmp=0"` -- if someone picks this up. Its reduction formula
(`R = 3 + depth/5`) has never been tuned.

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
