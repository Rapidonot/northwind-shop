# RAG-08 + NFR-03 (partial), row #13b: evidence

Branch `req/RAG-08b-adapter`, base `origin/main` `1e1cfd9`. Plan: `docs/reviews/2026-11-12-RAG-08b-plan.md`.
Failing-first and mutants: `docs/reviews/2026-11-12-RAG-08b-failing-first.log`. Security review (raw, with
triage): `docs/reviews/2026-11-12-RAG-08b-security-review.md`.

## Ledger

- Start of row: **$2.950179 of $90** (`make llm-spend`, 2026-11-12). No call made by this row so far.

## The enrichment cache and R01221 (the user's stop-5 decisions, 2026-11-12)

**Option (a) was impossible.** The user approved one `make enrich` window for R01221's chunk. Before asking for the
window, a no-LLM dry run of `northwind_rag.enrichment.plan_work` over `build_chunks(data/fixtures)` against
`data/enrichment` returned **0 parents, 0 chunks**.

The reason: `plan_work` skips any chunk whose `source_type` is not in `northwind_rag.sources.CONTEXT_SOURCE_TYPES`,
and reviews are not in it. Reviews get no context (decision 0010, "Reviews get no context"). The 15 first-run trap
reviews have cache entries only because the first enrichment run predates that rule. `make enrich` makes no call
for a review, so no window could give R01221 an entry, accepted or rejected. No window was opened and nothing was
spent.

**(b1), approved:** `evals/tests/test_enrichment_cache_scan.py::test_no_planted_review_passed_anything_into_its_sentence`
keeps exact equality over the 15 first-run traps: the answer key's `injection_reviews` minus the appended ones
(`northwind_data.planted.APPENDED_REVIEWS`). It also asserts that R01221 has **no** cache entry, so an entry
appearing later fails the test.

## NFKC pins (the user's stop-5 approval, 2026-11-12)

`services/assistant/tests/test_render_nfkc.py` now exempts exactly R01221's body (B1) and its chunk (B2):
- its rendering equals the NFKC-folded, escaped form, with one `<source` and one `</source>`;
- it differs from the pre-row-13 rendering;
- every other string and chunk stays byte-identical.

Pre-approved for later: `evals/tests/test_render_nfkc_identity.py` gets the same exemption, for the R01221 chunk
only, when `make eval-snapshot` puts R01221 into a saved context.

## Retrieval diff (plan §5.2, the user's A3)

Pending: the database is not reachable yet.
