# Row #27 PR 2 (CHK-07): record texts, drafted before the window

Drafts only. Nothing here is applied: #30 edits decision 0015, and `PROGRESS.md` and `COVERAGE.md` follow at `/req-done`. `____`
is filled from a run, never from memory. Applied after the merge of main.

## PROGRESS row #27 (status cell, then appended to the evidence cell)
Status: `____` (`done` only if CHK-07's recorded scenarios pass; else `partial`).

> **PR 2 (CHK-07), `/req-done` ____ at `____`: ____.** Branch `req/CHK-07-returns` from `7f0f8d9`, `origin/main` `____` merged by
> the user as `____`; window B, not routine. Plan `docs/reviews/2026-11-25-27b-plan.md` (Addendum A), decision 0024. Built:
> `services/mcp-policy` (`evaluate_return_eligibility`, the returns and shipping policy resources), `request_return` on
> mcp-orders, D9's plain wording in `get_order`, `shp` for northwind-policy. 68 labelled cases (`reviewed_by: model`), re-labelled
> independently: 58 agreed, 10 went to the user, RC-061 changed. M-1 is tested on the recorded answers, not enforced at runtime
> (the user). `make ci` exit ____ at that head: ____ unit passed, ____ skipped, ____ xfailed; ____ Vitest; ____ integration;
> `eval-integrity` ____; eval-gate ____ on ____ metrics, ____/____ replayed, ____ stale. Window: `assistant-orders`, ____ calls,
> $____ (ledger $____ to $____), ____ cassettes added, ____ removed, ____ fix rounds. Baseline: $____ rescore, promoted by the
> user (`answerer_id` ____). PR body `docs/reviews/2026-11-25-27b-pr-body.md`. Left: ____.

Open items: the M-1 entry (line 166 at `7f0f8d9`) is replaced by:

> **Row #27's M-1 (Medium): live-only, prompt-held, by the user's decision (2026-11-25).** Recorded answers are tested
> (`services/assistant/tests/test_chk07_recorded_status.py`). No owner: after Dec 22 the app replays only.

Log line:

> ____: row #27 PR 2 CHK-07 `/req-done` at `____`: **____.** Return eligibility from the policy server, the shopper's own return
> request, plain status wording. `make ci` exit ____ at that head (____ unit, ____ Vitest, ____ integration; eval-gate ____).
> `assistant-orders` $____, ____ cassettes; baseline promoted by the user at $____. **Not run by Claude:** ____.

## COVERAGE, CC-10 (appended; "the policy-catalog resource (mcp-policy)" and "mcp-orders" leave the "Left" list)
> Row #27 PR 2, ____: `services/mcp-policy` serves `evaluate_return_eligibility` and the returns and shipping policies as
> resources, one per region, read by `shopper-portal` for its token's region only; a server with a policy document that lost a
> fact refuses to start. mcp-orders gains `request_return`. Contract harness on four servers (`tests/mcp/test_contract.py`,
> `tests/mcp/test_cc10_contract_errors.py`, `services/mcp-policy/tests/`). `make ci` exit ____ at `____`. Stated gap: no warranty
> document exists in data v3, so no warranty resource. Left: mcp-catalog's category-tree and attribute-dictionary resources
> (Q3).

CC-10's status cell: `____` (the PRD's acceptance is "contract tests pass; policy catalog is exposed as a resource"; the user
decides whether Q3's two resources keep it `partial`).

## Decision 0015, row #27 note: replaces the last two sentences of "What the class does not catch"
> Owner (the user, 2026-11-25): row #27's PR 2 (CHK-07). **PR 2 outcome, the user's decision (2026-11-25): the gap stays
> at runtime.** It is live-only and prompt-held: a live turn could still say "Order O000418 has been confirmed". After Dec 22
> the app replays only. A runtime check was planned (closed sentence shapes) and not built. Instead every recorded
> `assistant-orders` answer is tested (`services/assistant/tests/test_chk07_recorded_status.py`): a sentence that states an
> order's status with its number must match that order's `get_order` status or scan history, and no answer shows a status
> code. On PR 1's recordings that test failed on exactly the two answers with a raw code. The confirmation class is
> unchanged. `get_order` now gives plain words (`status_text`) and `ORDERS_RULES` asks for them (D9).

## Threat model, the "order's status or delivery date" open row: owner cell
> The user, 2026-11-25: accepted as live-only and prompt-held; recorded answers are tested (row #27 PR 2, decision 0015). The
> N1-R option (B) decision stands: no confirmation-class exemption.

Two threat-model rows to add: a model or injected text asks for a return (own orders only, non-monetary, decided again by the
server; a write with untrusted text in context, the cart's residual), and a false policy claim ("90 days") changes an outcome
(it cannot: no argument carries policy or date).
