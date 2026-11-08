# 0012: Hybrid retrieval, and keyword search over chunk text only

Status: accepted, 2026-11-07 (RAG-03, row #8; plan `docs/reviews/2026-11-07-RAG-03-plan.md`, approved by the user).

## Decision

- **Agents get hybrid retrieval.** `retrieve_context` takes the top 50 candidates from a dense list (pgvector, cosine)
  and from a keyword list (Postgres full-text search). If `k` is larger, it takes `k`. It fuses them with reciprocal
  rank fusion at k = 60, using exact fractions, with ties broken on `chunk_id`.
  - Both lists come from one statement under the same audience and region filter.
  - `dense_search` and `keyword_search` remain as the comparison arms for evals.
  - The parameters were fixed in the plan before anything was measured. Changing one means a new `retriever_id` and a
    new decision.
- **The keyword query ORs its lexemes.** It is built from `plainto_tsquery('english', q)`, with `&` swapped for `|`.
  With AND, a natural question matches almost nothing, and "beats keyword-only" would be trivially true.
- **Full-text search indexes `text`, never `context` or `embed_text`.** The column `fts` is generated from `text` by
  migration 0003. Decision 0010 makes context ranking-only, and the embedding is already its one channel. Matching
  keywords against generated sentences would give planted or paraphrased text a second, literal route into a top 5,
  which is exactly the risk row #8c's conditions are about. Indexing context too would need a new decision, with #8c's
  measurements.
- **The source rule becomes an allow-list (the user's option 2).** `TRUSTED_SOURCE_TYPES` (product, variant,
  document) and `UNTRUSTED_SOURCE_TYPES` (review) sit next to `CONTEXT_SOURCE_TYPES`, which is unchanged.
  - The prompt's rule is built from the trusted set.
  - `render_sources` shows reviews, as before, and refuses a type in neither set. `search_sources` turns that refusal
    into a structured failure.
  - Tests hold that every `SourceType` is in exactly one set, and that `CONTEXT_SOURCE_TYPES` is a subset of the
    trusted one.

## Evidence

`make eval-compare-retrievers` (no LLM) ran once with these parameters, on the shopper's region-filtered path, over
the 64 retrieval-scored items. recall@5:

| Arm | recall@5 |
|---|---|
| dense | 0.750 |
| keyword | 0.883 |
| hybrid | 0.954 |

Full table in `docs/reviews/RAG-03-retrieval-report.md`.

## Consequences

- Every saved context is stale, because the retriever ID changed. That means a snapshot, a full `eval-live` and a
  promote.
- The baseline and context files keep their `dense-only.*` names for now (open item).
- The shared index gets the `fts` column the next time anything ingests. Main's code lists its columns explicitly,
  so the column does not affect it.

## Retrieval is deterministic across platforms (2026-11-08, PR #30)

PR #30's Linux CI printed every retrieval metric equal to macOS, but its top-k hash differed and
EV-029 went stale. One combination of single adjacent dense swaps reproduces Linux's hash exactly:
EV-029 (ranks 13/14, 1.96e-7 apart), EV-014 and EV-082 (2e-5 apart). So the embeddings themselves
differ by platform, and pgvector's float32 arithmetic (error up to 3.7e-7 against exact) could flip
the closest pair on its own. The diagnosis and both plan reviews are in
`docs/reviews/2026-11-08-RAG-03-drift.md`. The user's decisions there are D1 to D4.

- **Frozen vectors (b).** `data/embeddings/<embedder>.jsonl` holds the vectors every saved context was
  ranked with: each fixture chunk's, exported bit-exact from the shared index, and each eval and smoke
  query's, embedded one per call as retrieval does. Keys are a digit-free sha256 of the text, as in
  `data/enrichment`, because hex digests trip the scanner's Luhn check. `CachedEmbedder` serves them at
  ingest, in the eval CLI and in the smoke test's retriever. Any other text, including follow-up searches
  the model writes and every live shopper query, is embedded by the model as before (D4).
- **Exact re-rank (c).** The SQL picks a dense pool twice the candidate depth, with each row's stored
  float32 vector. Python ranks the pool by an exact cosine distance (float32 inputs, `fsum`, IEEE `sqrt`
  and division, so the same bits on every CPU), then by `chunk_id`. If the pool's edge is within
  `RERANK_MARGIN` of the last kept distance, the same statement runs again with no dense limit.
  A zero vector is infinitely far, so nothing raises on the shopper path. Over the 113 frozen queries
  there were no fallbacks, and the median `retrieve_context` time was 44 ms.
- **`COLLATE "C"` (e)** on every `chunk_id` tie-break, matching `rrf_fuse`'s code-point order. Today's
  `en_US.utf8` orders every chunk ID the same way, so nothing moves, but a locale can no longer change
  an order.
- **The gate's stale line** names what moved, by chunk ID only: saved and live IDs, no saved context, a
  retriever change, or a replay miss.
- **`retriever_id` is unchanged, `RETRIEVER_VERSION` stays 2 (D2).** The reason is measured: on macOS,
  the fixed retrieval returns the same top 10 for every eval item as the promoted baseline's, with top-k
  sha256 `99135472f1545431c10e4b3bdab76bca3cf9eff9d233eb356d8f0486a8368074`. The user's condition: local
  `make ci` prints that hash, 100/100 replayed, stale 0, on the shared index and on a fresh scratch
  ingest. If either differs, stop. Bumping the version instead would make every saved context stale and
  need a re-promote.
- **The control surface (D3).** `data/embeddings/**` joins it in `CLAUDE.md`, `tools/ship.sh` and the
  advisor proposal. `make eval-integrity` refuses a changed or removed key, or a deleted frozen file,
  against the base, committed or on disk. `make scan-cassettes` scans the file as raw text.
- **The model vouches for every added vector** (security review, finding 1). A reader cannot check 384
  numbers by eye, and the add-only rule protects only keys that already exist. So `make eval-integrity`
  re-embeds the text behind every key a branch adds, with the local model, and refuses a key that no
  fixture, eval or smoke text has, or a vector more than 1e-4 in cosine distance from the model's.
  `make embed-cache` refuses to write such a vector. Measured: batch shape moves a vector by at most
  6.2e-7, and two different fixture texts are never closer than 1.4e-3. `RERANK_MARGIN` is 1e-4, above
  the textbook worst case for pgvector's float32 sum (about 2.3e-5), not only the 3.7e-7 measured.
- **Residuals (security review of the drift fix).** The vouch measures how far a vector moved, not which
  way, so a nudge aimed inside 1e-4 can still move near-tied ranks. Tightening it needs the x86_64
  distance, which only CI can measure: the integrity step prints the largest it sees (open item, owner
  hardening). `Guard / eval-integrity-main` runs main's copy, which loads no PR code and no model, so it
  enforces add-only after merge but never vouches. A PR that edits the vouch and plants a vector together
  is visible only through `make ship`'s control-surface listing (`evals/src`, `services/rag`,
  `data/embeddings`).
