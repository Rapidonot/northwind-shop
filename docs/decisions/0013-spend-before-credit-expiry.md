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

**2026-11-12, HARDEN-1:** `CLAUDE.md` now says it: full runs with a generous `RUN_BUDGET` until ~2026-11-24, then
the slice default. The `$90` cap sentence is unchanged (decision 0016).

## After the credit expires

From about 2026-11-24, the slice default in `CLAUDE.md` applies again, unless the user decides otherwise.

## Amendment, 2026-11-22: credit extended to Dec 22 (the user's decision)

Recorded by row 12b. The credit now expires around **2026-12-22**, not 2026-11-24. RECORD-ALL (#18) moves to
about Dec 13–16, and the Tier B rows that call the model run before it (`PROGRESS.md`, "Ordering"). Until
Dec 22, full runs with a generous `RUN_BUDGET` stay the default. After it, the slice default in `CLAUDE.md`
applies again. The $90 `LLM_BUDGET_USD` cap is unchanged. `CLAUDE.md` still reads "~2026-11-24, decision
0013": it is control surface, so the user edits that line. 2026-11-23: the user made that edit
(`89c8227`, on row 12b's branch).
