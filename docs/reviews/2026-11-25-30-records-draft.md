# Rows #30 and #22: records drafts (blanks filled at `/req-done`)

Kept here, not in `PROGRESS.md` or `COVERAGE.md`, so the CHK-06 merge gets no extra conflict.

## PROGRESS row #30 (SA-04), status `____`, notes cell
Branch `req/30-clarify-intents`, with row #22; not routine. Plan `docs/reviews/2026-11-25-30-plan.md`, approved by the user
with changes. SA-04 ____/20 (criterion 18). Labels frozen before any scorer. Prompt rule, D8 remainder 10 → ____, H3, H4,
M1, M2, M6 fixed; step 0 changed 6 of 356 recorded answers. W1 to W6 $____ ; promoted by the user. Replies stand alone
(SA-05). `docs/reviews/2026-11-25-30-*`.

## PROGRESS row #22 (SA-03 partial), status `partial`, notes cell
Built with row #30. Five intents, 10 conversations each, deterministic scorer: product question ____, comparison ____,
recommendation ____, availability ____, policy ____. Left: delivery, order status, return (CHK-06/07).

## PROGRESS Open items
- **D8 remainder:** ____ (close, or restate the count and owner).
- **Over-blocking:** IC-024 and IC-040 (W1), and the W6 count ____. Owner: window B's output-check prerequisite row before
  RECORD-ALL, with the split-range finding.
- **Split ranges and the stock rule:** W6 count ____. If above 0, owner: window B's output-check prerequisite row before
  RECORD-ALL.

- **Hedged planted claims and steers (N-2, N-3):** W6 report ____ sentences, read by the advisor: ____. Owner of the
  RECORD-ALL rerun: window B's RECORD-ALL prerequisites row.

## PROGRESS log line
- 2026-____: rows #30 (SA-04) and #22 (SA-03 partial) `/req-done` at `____`: **____.** `make ci` exit ____ at that head
  (____ unit, ____ Vitest, ____ integration; `eval-gate` ____). Windows W1 to W6 $____ (ledger $____ → $____ of $90).
  Labels frozen first, 21 `human`. Strict xfails 19 (the user's decision). **Not run by Claude:** `make up`; browser runs
  in the user's terminal.

## COVERAGE, CC-14 (custom subagents) and CC-16 (chaining)
- 2026-11-25 (#30): a fresh general-purpose subagent drafted 70 labels from the guide and fixtures only, before any
  implementation; `test-writer` (482 tests) found a fail-open hedge the author's 11 mutants had missed; then
  `security-reviewer` (____).

## Gate report skeleton
### What's done
SA-04 ____ (____/20). SA-03 partial (rates above); left: delivery, order status, return.
### Evidence
Base `____`, head `____`; `make review-diff REVIEW_DIFF=$TMPDIR/northwind-review-30.diff`; `make llm-spend` $____ → $____;
`make ci` ____ at ____ ; added cassettes ____ ; failing-first `docs/reviews/2026-11-25-30-failing-first.txt`; security ____;
advisor ____.
### Open findings
____. Control surface: as in the PR body. Needs `gate-change` and `baseline-change`; green means only the PR's own gate.
### Terminal commands
Read each command before pasting it. `make e2e-scripted` and `make e2e` (`DATABASE_URL=…/northwind_16`), `/req-done`, then
`make -C ~/.northwind/runner finish CHECKOUT=$HOME/Projects/northwind-shop SHA=____ BODY=docs/reviews/2026-11-25-30-pr-body.md`
