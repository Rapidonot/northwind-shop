# Row #27 PR 2 (CHK-07): record texts, drafted before the window

Drafts, except where a section says applied. `PROGRESS.md` and `COVERAGE.md` follow at `/req-done`. `____` is filled from a run, never
from memory.

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
>
> **Row #27 PR 2: untrusted content can steer a return request.** Accepted residual by the user's decision (2026-11-26), with no
> owner: own orders only, non-monetary, re-evaluated by the server, idempotency keys (threat model, Residuals).

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

## Decision 0015 and the threat model: applied 2026-11-25, with stage B
In `docs/decisions/0015-output-control.md` (row #27 note) and `docs/governance/threat-model.md` (boundary 3, Owners).
2026-11-26, the user: "untrusted content can steer a return request" is an accepted residual (threat model, Residuals),
with no P6 or NFR-03 owner; its Owners row is gone.
