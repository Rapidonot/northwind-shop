# PROGRESS

Spec: docs/PRD.md · Status: todo | in-progress | partial | done | deferred
Update this file at the end of every task (status, date, evidence, notes).

## Phase 1: thin vertical slice (portal + chat + synthetic catalog + basic RAG + evals)

Goal: shopper browses, asks grounded questions with citations, sees live price/stock,
adds to cart. RAG quality is measured by a starter eval set (~100 queries) with a CI gate.

Order: foundations → RAG-01 → eval baseline → **a crude end-to-end chat (item 4)** so the
whole path works early → retrieval quality, MCP servers and portal polish afterwards.

Phase 1 cuts across PRD phases P0–P2 and does **not** pass Gate 1 by itself; Gate 1 also
needs RAG-02/04–07 and the 300-query set. RAG-09 (partial) and RAG-11 are pulled forward
from PRD P5 because hard rules 2 and 7 are cheaper to build in than to retrofit.

| # | ID | Scope in Phase 1 | Status | Evidence |
|---|----|------------------|--------|----------|
| 0 | FOUND-0 (no PRD ID) | Walking skeleton: uv workspace, pnpm portal shell, docker compose (pg16+pgvector), Makefile, CI running lint+test, .gitignore/.env.example, deny-.env permission | done | 2026-11-03: `make ci` passes locally (ruff, mypy strict, eslint, tsc, pytest 1 unit + 1 integration, vitest 3); `/api/healthz` reports db ok + pgvector 0.8.7 through the Vite proxy, and db unavailable with the stack down. Fresh clone passes `make setup && make up && make ci`. GitHub CI green: https://github.com/Rapidonot/northwind-shop/actions/runs/41657414233 (PR #1). Template is `env.example` (no dot, so the .env deny rule doesn't block it). `.claude/settings.json` was added by the user (Claude may not write its own permissions); Claude's Read of `.env` and Write of `.env.probe` were both refused by it |
| 1 | FOUND-1 (no PRD ID) | Synthetic data generator (seeded): ~200 products / ~800 variants (apparel+home), reviews (some containing injection text), size & care guides, returns/shipping policies per region, FAQs, a few internal-only docs; live price/stock tables | todo | |
| 2 | RAG-01 | Versioned, idempotent ingestion; metadata on 100% of chunks (category, brand, size, colour, material, price band, region, updated, visibility); no exact prices in chunk text | todo | |
| 3 | RAG-12 (partial) | Harness: recall@k, MRR, groundedness, answer relevance. Starter set of ~100 labelled queries, including unanswerable and adversarial ones. Every item has `reviewed_by: human \| model`; ≥25 are human-reviewed, and all unanswerable and adversarial items must be among them (a schema test enforces both). Record the dense-only baseline; add `make eval-gate` and a CI job. Rest: grow to 300 queries, calibrate the judge on human labels | todo | |
| 4 | SA-02 (crude) | **Early end-to-end slice, ugly is fine.** Bare portal page with a chat box → gateway SSE endpoint → assistant (Agent SDK) calling `retrieve_context` as an in-process tool (dense-only) → streamed answer with source IDs shown as citations. No prices or stock quoted: there are no live tools yet, so the assistant points to the product page instead (hard rule 2). One smoke test: Playwright asks a question and sees streamed text plus at least one citation | todo | |
| 5 | RAG-03 | Hybrid dense (pgvector) + FTS fused by RRF; beats dense-only and keyword-only on recall@5. The crude slice switches to hybrid | todo | |
| 6 | RAG-11 | Shopper callers never get internal-only docs; regional policies match the shopper's region; red-team queries in evals/adversarial | todo | |
| 7 | (CC-10 partial) | mcp-catalog (search_products, get_product, get_variant, check_stock, get_price) + mcp-rag (retrieve_context, index-manifest resource); structured errors; contract tests. The assistant moves from the in-process tool to mcp-rag | todo | |
| 8 | RAG-09 (partial) | Price/stock/promotions only via tools; a test changes price & stock and the assistant quotes the new values. Rest: 10-minute catalog→index freshness (with BE-03) | todo | |
| 9 | SA-01 (partial) | Pages: home, search results, product detail, cart, chat panel; Playwright covers each without error. Rest: checkout, order history | todo | |
| 10 | SA-02 (complete) | Polish the crude slice: inline product cards, a visible source link on every product claim, first token < 2 s (measured) | todo | |
| 11 | RAG-08 | Grounded answers with citations; says so or asks when evidence is thin; zero fabricated attributes on the adversarial subset. Groundedness measured on the starter set (95% target enforced at Gate 1) | todo | |
| 12 | CHK-01 | Cart tools add/remove/change qty with live stock check; out-of-stock add refused with alternatives | todo | |
| 13 | SA-03 (partial) | Intents: product question, comparison, recommendation, availability, policy; ≥10 labelled conversations each, pass rate reported. Rest: delivery, order status, return | todo | |
| 14 | NFR-08 | AI disclosure visible in every chat session; journey test asserts it | todo | |

**Eval review rule (RAG-12):** Claude writes eval items with `reviewed_by: model` and never
sets `human`. Only the user does that, after writing or checking the item. Item 3 is done
only once ≥25 items are human-reviewed, including every unanswerable and adversarial item.
Until then it stays `in-progress`, with a list of items waiting for review.

Also covered by the hard rules from day one: NFR-02 (secrets in env, .env deny rule).
Claude Code features exercised along the way (record in COVERAGE.md): CC-01, CC-04, CC-10 (partial).

## Deferred (by PRD phase)

- **P1 / Gate 1 (RAG quality):** RAG-02, RAG-04 (Must), RAG-05, RAG-06, RAG-07, RAG-10, RAG-13; RAG-12 rest (300 queries, judge calibration)
- **P2 (portal & assistant):** SA-01 rest (checkout, order history), SA-03 rest, SA-04, SA-05, SA-06 (Must), SA-07, SA-08, CHK-06, CHK-07
- **P3 (backend crew):** BE-01 – BE-08, CHK-05
- **P4 (payments):** PAY-01 – PAY-08, CHK-02, CHK-03, A2A-04, A2A-07
- **P5 (A2A & partner):** A2A-01, A2A-02, A2A-03, A2A-05, A2A-06, A2A-08, CHK-04, RAG-09 rest
- **P6 (hardening):** NFR-01, NFR-03 – NFR-07, NFR-09 – NFR-17
- **Claude Code matrix:** CC-02, CC-03, CC-05 – CC-09, CC-11 – CC-30 tracked in COVERAGE.md per PRD phases

## Open questions (from PRD §11, still open)

- Payment provider sandbox + MCP server (needed by P4)
- Can a2a-protocol.org be reached to confirm the release, card discovery path and method names? (P4/P5)
- Agent-payment extensions / mandates on A2A: later version?

Resolved 2026-11-03: embeddings = local fastembed + PostgreSQL/pgvector; runtime agents = Python Agent SDK;
portal = Vite + React; follow-up backlog = this file.

## Log

- 2026-11-03: PRD converted to docs/PRD.md (figures in docs/images/); CLAUDE.md and Phase 1 scope written.
- 2026-11-03: FOUND-0 walking skeleton: uv workspace + gateway healthz, Vite/React portal shell, compose Postgres+pgvector, Makefile, GitHub CI, COVERAGE.md.
