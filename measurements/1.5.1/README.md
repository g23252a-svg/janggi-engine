# 1.5.1 measurements

`r3_*` are `janggi.match` logs (pool with `python -m janggi.match --pool`):
A is 1.5.1's weights loaded as the candidate table 2 (`--table2`,
`evalw=2`), B is 1.5.0's shipped table 1 (`""`). `r3_60000_*` and
`r3_300000_*` are 20-game shards at 60k / 300k nodes a move, `r3_1000000_*`
one 20-game shard and `r3_1M10_*` three 10-game shards at 1M nodes.

`game_2026-10-19_fsf_scores.json`: every move of the 2026-10-19 game scored by
Fairy-Stockfish (5 s a move, `janggimodern`). The two `*_replay_150_vs_151`
files: that game's 53 CHO positions and the 2026-10-18 game's 42 HAN positions
searched by 1.5.0 ("hand") and 1.5.1 ("tuned") at 1.2M nodes, each choice
scored by Fairy-Stockfish (2 s).
