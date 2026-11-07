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
