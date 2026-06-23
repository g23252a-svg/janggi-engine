# Rebuilding the opening book

As you accumulate game records (gibo JSON files), rebuild the opening book so
the engine plays verified moves instantly in the opening:

```bash
# put all your gibo .json files in gibo_data/ then:
python -m janggi.book build gibo_data/*.json -o data/opening_book.json
python -m janggi.book show data/opening_book.json
```

Commit the regenerated data/opening_book.json and push; Railway redeploys
and the server loads the new book at startup. The more games (especially with
the same formations and openings), the stronger the book.
