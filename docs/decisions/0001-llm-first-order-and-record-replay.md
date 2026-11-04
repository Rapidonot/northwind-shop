# 0001: LLM-first delivery order and LLM record/replay

- Status: accepted
- Date: 2026-11-03
- Deviates from: PRD §9 (phase order P0 → P7)

## Context

The lab has $100 of Claude API credit that expires around 2026-11-24, about three weeks out.
PRD §9 runs the phases in order: data and RAG, portal and assistant, backend crew, payments,
A2A, hardening. In that order most LLM-dependent work (the backend crew in P3, the adversarial
suite in P6) would start after the credits are gone. Without API access afterwards, any test or
eval that calls Claude would also stop passing.

## Decision

1. **Order by LLM dependency, not by PRD phase.** Everything that calls Claude (Shopper
   Assistant, eval judge, RAG-02 enrichment, RAG-04 query understanding, backend crew) is
   scheduled as early as its dependencies allow. Payments (P4), A2A (P5) and hardening (P6) come
   after. Tier A is what must finish by Nov 19; RECORD-ALL runs Nov 20–22 whatever the state of
   the rest; Tier B is done live only if time is left. The order lives in PROGRESS.md.
2. **Model input is frozen before the big recording.** Non-LLM changes that alter what the model
   sees (RAG-03 hybrid retrieval, RAG-11 access filtering, the move to MCP tools) are done inside
   the window, because they would otherwise invalidate recordings made earlier.
3. **One record/replay layer for every LLM call (FOUND-2).** A local proxy speaking the
   Messages API sits behind `ANTHROPIC_BASE_URL`. The Agent SDK's CLI subprocess and every
   direct `anthropic` client are built through one factory that points at it.
   - Modes: `LLM_MODE=replay` (default, forced in CI), `record`, `live`. A `scripted_llm` pytest
     fixture serves canned responses for plumbing tests that need no cassette.
   - Matching: sha256 of the canonical request JSON after scrubbing volatile values (metadata,
     timestamps, UUIDs, paths, session and idempotency IDs). A miss fails and prints a diff against
     the nearest recorded request. `LLM_REPLAY_MATCH=sequence` serves recordings by test ID and
     order; it is for plumbing tests after expiry only and marks results stale.
   - Cassettes: plain JSON under `cassettes/<suite>/`. Only content-type, anthropic-version and
     anthropic-beta headers are stored. A scanner fails the build on API-key patterns or
     Luhn-valid PANs (hard rules 1 and 4).
   - Spend guard: the proxy prices every live response from `usage`, appends it to a gitignored
     ledger, and refuses upstream calls past `LLM_BUDGET_USD` (default 90) or `LLM_RUN_BUDGET_USD`.
   - No API key goes into the CI secret store, so CI cannot spend.
4. **Time-box.** The FOUND-2 spike (does the Agent SDK CLI honour `ANTHROPIC_BASE_URL`, and do
   identical runs produce identical scrubbed requests?) gets at most one day. If the requests are
   not stable by then, the Agent SDK is recorded at its message-stream level instead, and the
   proxy stays for the direct SDK clients. FOUND-2 gets at most three days in total.
5. **Judge model:** Sonnet 5.5.
6. **Budget checkpoint.** After the first 20 live calls, measured costs from the ledger replace
   the estimates in PROGRESS.md. If the projected total exceeds $90, live calls stop until the
   user agrees on cuts.

## Consequences

- After the credits expire, replay proves that **unchanged** LLM behaviour still passes.
  Changing a prompt, tool schema, retrieval output or model ID turns the affected cassettes into
  misses, and re-recording them needs API access. Those evals are reported as stale, not as passed.
- Retrieval metrics (recall@k, MRR) stay live because embeddings are local (fastembed).
- Backend-crew tests run on deterministic port fakes (payment, fraud, carrier). When the real
  adapters arrive in P4/P5, PAY-01's shared contract suite shows they behave like the fakes, so
  the recorded crew behaviour stays valid.
- Gate 1 is passed later, in Window B, on replayed answers plus live retrieval metrics.
- A2A-05 is handled by decision 0002.
- `make test`, `make ci` and `make eval-gate` run in replay. `make record` and `make eval-live`
  are the only targets that spend.

## Open items

- **Billing route (resolve before the first live call).** This decision does not record whether Claude Code, and the `claude` CLI subprocess the Agent SDK starts, is billed to the user's subscription or to the API key whose $100 credit this plan budgets. The answer decides what the proxy ledger and `LLM_BUDGET_USD` cover. Tracked at the top of the FOUND-2 row in PROGRESS.md.
