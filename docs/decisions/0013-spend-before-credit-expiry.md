# 0013: Spend the API credit freely before it expires

Date: 2026-11-09 · Row: #9 (RAG-11) · Status: decided by the user (stated 2026-11-09, recorded at plan approval)

Plan and the user's answers: `docs/reviews/2026-11-09-RAG-11-plan.md`.

## Decision

The $100 API credit for the app's runtime calls (decision 0001) expires around 2026-11-24. Until then:

- The credit is spent freely. Full runs are fine instead of 10–20-item slices, and plans propose a generous
  `RUN_BUDGET`, with headroom over the turn-cap ceiling so a run is not cut off partway.
- Spending still happens only through `make record`, `make eval-live` and `make enrich`, in a runner window the
  user opened with `make llm-runner`. Claude never opens a window.
- `LLM_BUDGET_USD` stays at $90. Raising it still needs asking.

Everything else in decision 0001 holds: replay by default, the 20-call checkpoint, cassettes scanned, and no API
key in CI secrets.

## Why

Credit left on ~Nov 24 is lost. Slices saved credit that is better spent on full recordings while the credit
window is open. The window and the cap are the controls that keep spending safe, and they do not change.

## What does not change yet

`CLAUDE.md` ("How we work", LLM calls) still says "10–20-item slices during development, with full runs only at
milestones". This decision does not edit it. The open item "CLAUDE.md slice wording to match this decision" is
owned by hardening.

## After the credit expires

From about 2026-11-24, the slice default in `CLAUDE.md` applies again, unless the user decides otherwise.
