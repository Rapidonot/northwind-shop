# data/: synthetic dataset (FOUND-1)

`make data` regenerates `data/fixtures/` from `northwind_data` (templates + pinned Faker, no LLM).
Same seed gives byte-identical files. The fixtures are committed; a test fails if they are stale.

| File | Ingestible | What |
|---|---|---|
| products.jsonl, variants.jsonl, reviews.jsonl, documents.jsonl | yes | catalog, reviews, size/care guides, regional policies, FAQs, internal-only docs (`visibility`) |
| prices.jsonl, stock.jsonl | **no**: live tools only | exact prices per variant and region, on-hand stock per node (hard rule 2) |
| regions.json | no | currencies, thresholds, fees, stock nodes |
| manifest.json | no | DATA_VERSION, seed, Faker version, file hashes, ingestible list |
| _answer_key.json | **never** | every planted trap and its ID (eval and red-team ground truth) |

Rules:
- Files starting with `_` are never ingested. RAG-01 must pass every file through
  `northwind_data.ingest_guard.assert_ingestible`.
- If you change generated output, bump `DATA_VERSION` in `src/northwind_data/version.py`.
  `make data` refuses otherwise, because recorded cassettes would stop replaying.
- No currency amounts or stock counts in any text field (`northwind_data.textscan`).
