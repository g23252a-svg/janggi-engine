# Evaluator tuning against Fairy-Stockfish

How `janggi/eval_weights.py` (the default evaluator since 1.5.0) was made, and
how to make it again. Nothing here is imported by the engine.

[Fairy-Stockfish](https://github.com/fairy-stockfish/Fairy-Stockfish) is the
teacher: a much stronger engine (1.4.0 at 300k nodes scored 2 of 10 against
it at 100k). It is GPL-3 and is **not** part of this repository; download a
release binary (the largeboard build) and point `JANGGI_FSF` at it. These
scripts talk to it over UCI with the `janggimodern` variant, whose rules match
`janggi/board.py` move for move (Fairy-Stockfish's default `janggi` variant
forbids leaving the generals facing, which ours does not).

Run everything from the repository root with the compiled extensions built.

1. **Positions and labels** -- Fairy-Stockfish plays itself from random
   openings and formations, one move in ten replaced by a random legal move,
   and scores every position it reaches (10,000 nodes):

       JANGGI_FSF=/path/to/fairy-stockfish python tools/evaltune/gen_label.py \
           --mode fsf --games 400 --seed 1 --out data1.jsonl

   (`--mode ours` lets our engine choose the moves instead.)

2. **Features** -- keeps the quiet positions (not in check, best move not a
   capture, no mate) and measures the evaluator's features from the compiled
   core itself (`core_eval2_probe`), plus the piece-square counts:

       python tools/evaltune/featurize.py data.npz data1.jsonl data2.jsonl ...

3. **Fit** (needs numpy) -- Texel-style, in win-probability space, chariot
   pinned at 1300, a tenth of the games held out:

       python tools/evaltune/tune.py data.npz result.json --pst

   `result.json["pst"]` holds the scalars (`w`, the EW layout of
   `janggi/_core.pyx`, tempo in slot 45) and the 720-entry piece-square table
   (`["scalars"]` the fit without it). Write them into
   `janggi/eval_weights.py`, or play them against the shipped table without
   editing anything -- the match loads them into the candidate table 2:

       python -m janggi.match --table2 result.json --a "evalw=2" --b "" \
           --games 20 --nodes 60000 --log cand.jsonl --resume

4. **Outside yardstick** -- our engine against Fairy-Stockfish under our rules:

       JANGGI_FSF=... python tools/evaltune/fsf_match.py --games 10 \
           --nodes 300000 --fsf-nodes 100000 --log fsf.jsonl
