# 1.5.0 measurements

Match logs are `janggi.match` JSON lines (pool them with
`python -m janggi.match --pool <files>`); A is the candidate, B the opponent.

| files | A | B | budget |
| --- | --- | --- | --- |
| `pst60k_*` | round 1: re-weighted + piece-square table (table 1) | 1.4.0 (hand-set) | 60k nodes |
| `r2_60k_*`, `r2_300k_*` | the shipped weights, before the official-points lock fix | 1.4.0 | 60k / 300k nodes |
| `r2vr1_60k_*` | the shipped weights (table 2) | round 1 (table 1) | 60k nodes |
| `final_60000_*`, `final_300000_*` | the release build (`""`) | `evalw=0` (node-identical to 1.4.0) | 60k / 300k nodes |
| `fsf_v140_*`, `fsf_v150_*` | ours at 300k nodes (1.4.0 / shipped weights) | Fairy-Stockfish 14, `janggimodern`, 100k nodes | `tools/evaltune/fsf_match.py`, seed 20261018, same openings |

`game_2026-10-18_fsf_scores.json`: every move of the 2026-10-18 game scored by
Fairy-Stockfish (5 s a move, `janggi` variant): its best move and score, the
score of the move played, the loss. `game_2026-10-18_replay.json`: the 42 HAN
positions of that game searched by the hand-set and the tuned evaluator at
500k nodes, each choice scored by Fairy-Stockfish (2 s).
