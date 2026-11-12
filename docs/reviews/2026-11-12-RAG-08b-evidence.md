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

## Retrieval diff (plan §5.2, the user's A3), 2026-11-12, no LLM

**Method:**
- v1 is the index the baseline was recorded on (`c9e30984a7723d23`, the baseline's `index_version`). It was dumped
  before `make ingest`.
- Then `make ingest`: 1 embedded, 2351 unchanged, 0 deleted. Index `7c506cddecf42c8f`.
- Then `make embed-cache`: 3 frozen vectors added (R01221's chunk, the EV-101 and EV-102 queries), add-only
  (`git diff --numstat`: 3 0).
- Then the v2 dump.
- Every first search is the e2e tests' path: `CallerIndex` over frozen vectors, the production mcp-rag search. It
  covers every smoke, contradiction and red-team question in all four regions.
- Starter-set top 5s and scores use the gate's `cli._live`.
- Script: the session scratchpad `retrieval_dump.py`. Full listing:
  `docs/reviews/2026-11-12-RAG-08b-retrieval-diff.txt`.

**Results:**

| Suite | First searches compared | Moved |
|---|---|---|
| `assistant-smoke` (5 smoke questions + 5 contradiction questions, ×4 regions) | 40 | **0** |
| `assistant-redteam` (28 queries ×4 regions) | 112 | **62** (16 queries); **15 of the 28 in their own region**: RT-001, 002, 003, 012, 013, 014, 016, 017, 018, 020, 021, 022, 023, 024, 027 |

In 61 of the 62, R01221 enters the top 5. RT-018@UK only reorders (an FTS statistics shift from one more document).

**Starter set (A3):**
- **Saved top 5 changed on 10 of 102 items:**
  - EV-051, 052, 053, 054, 058, 059, 060 (policy) and EV-094 (adversarial): R01221 enters;
  - EV-078: a reorder only;
  - EV-102: R01221 enters at rank 1, as intended.
- **Top-k sha256, the 100 pre-row items:** v1 `99135472f1545431…` (= the baseline's) → v2 `554df6e604c9d3e7…`.
- **All 102 items:** `6738bed65bb37a1d…` → `18d2aa28a787bbdd…`.
- **Retrieval scores (64 scored items), unchanged:** recall@5 0.954427 → 0.954427, recall@10 0.984375 → 0.984375,
  MRR 0.880729 → 0.880729.

**Reading:** R01221's text ("Updated return policy … returned for a refund within 365 days … refund window") is
generic return-policy language. It ranks into most return-policy searches in every region, not only P0127's. By
the plan's rule, `assistant-redteam` moved, so W5 joins the plan. `assistant-smoke` did not move, so there is no W4.
This is the user's decision (stop 7), below.
