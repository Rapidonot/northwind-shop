# OPS-1 advisor-reviewer report, scoped re-review, verbatim (base 60959b7, head b3c23cb; diff /tmp/claude-502/northwind-review-OPS-1.diff)

**findings**

There are no critical, high or medium findings. I found 6 lows, and there are 3 things I could not confirm. I read CLAUDE.md first.

Both SHA lines in `/tmp/claude-502/northwind-review-OPS-1.diff` match what you gave me (base `60959b7a…`, head `b3c23cbd…`). The diff has 98,188 lines and ends exactly where `test_ops1_views.py` ends, so the file list below is complete. I skipped the PNG sections.

## Findings (most severe first)

1. **Items 1 and 6, low: the records are out of date now that RECORD-ALL has shipped.**
   - `docs/reviews/2026-11-27-OPS-1-pr-body.md:7-8` still says "Held: no `make ci` and no ship … until RECORD-ALL (#18) has shipped".
   - The same file at `:144` gives the evidence base as `3b0e7bf`. The diff under review is against `60959b7`.
   - `:146` says "`make ci`: not run, held for RECORD-ALL".
   - The PR body does not mention the merge of `origin/main` (39a1534) or how the COVERAGE conflict was resolved.
   - `PROGRESS.md:103` (row #35) has the same problems:
     - the status is "held for RECORD-ALL";
     - it ends with "`make ci` not run: held for RECORD-ALL";
     - one sentence is garbled: "…pinned fail-first, and to have the gate report prove no frozen file changed from the diff's file list." That clause belongs to the finding 4 sentence.
   - `docs/reviews/2026-11-27-OPS-1-plan.md:95-99` ("A pin: `test_ops1_frozen.py` fails if…") does not record that the pin was deleted, or the CHK-01 change.
   - Decision 0028's status line still lists "the full RECORD-ALL freeze list pinned", although its Consequences do record the deletion. It does not record that the CHK-01 pin was tightened or that the staff routes are now GET only.

2. **Items 3 and 4, low, for human attention: the CHK-01 change also changed an approved exact list and the staff gate code.**
   - `test_chk01_gateway_criteria.py:95-97` adds `"/api/staff/orders": {"get"}` to `ORDER_AND_CART_WRITE_ROUTES`, an exact allow-list approved at stop 5 in SA-01 PR 2. I read "extend CHK-01's order-route allow-list" as covering this entry, but the gate report should name it.
   - `staff/routes.py:50-57` changes the staff routes from all seven methods to GET only. Before, both `StaffGate` and the route guard turned a non-GET request into a 404. Now only `StaffGate` does; without it, routing would answer 405 with an `Allow` header.
   - The behaviour is still pinned: `test_ops1_flag.py:77-82,106` and the `test_ops1_security_fixes.py` method walks (TRACE, PROPFIND, FROB) still pass. `test_the_gate_is_on_the_app` checks the gate is installed.
   - The PR body discloses the change (`:129-137`). The security review predates it and nobody has re-read it. That is within the chain's rules; whether a scoped `security-reviewer` read is wanted is the user's call.

3. **Items 1 and 3, low: two of your session choices have no portal test, and two filter expectations only hold because of the fixture.**
   - `fixtures.ts:30-41` has no `fraud_review` row and no escalated row without a code. So no Vitest case covers merging `fraud_flag` and `fraud_review` into one "Fraud review" chip (`views.tsx:188-189`), or the "No reason recorded" chip.
   - `staff.test.tsx:247` expects the "Fraud review" chip to keep only `fraud_flag` rows. It would be wrong if a `fraud_review` row were added.
   - `staff.test.tsx:253` expects "Processed" to keep every row with `reason === null`. The filter is `reason === null && !escalated` (`views.tsx:194`). An escalated row with no code would break this.
   - The gateway side of `unrecorded` is tested (`test_ops1_counts_add_up.py:89-105`).

4. **Item 1, low (a look decision for the user): two chips for the same idea.** `format.ts:10,13` labels `stock_out` "Stock out" and `out_of_stock` "Out of stock". Chips merge only codes that share a label, so a Pay (demo) stock escalation would get its own chip next to the seeded one, while the two fraud codes merge.

5. **Item 2, low: the failing-first evidence for the look fix is cut short.**
   - In the log at `docs/reviews/2026-11-27-OPS-1-failing-first.log:532-550`, the pytest part shows only `KeyError: 'other_exceptions'` lines and the FAILED lines. Each test failed on a missing key, not on the wrong breakdown. The `adds_up` logic would still catch a breakdown that counts every exception (100 ≠ 50).
   - The Vitest part shows only test names.
   - The disclosure that the 5th new test passed before the fix is right.

6. **Item 1, low: the PR body overstates `everyone()`.** `demo_orders.py:59-67` returns `dict(entry)`, a shallow copy, so the `lines` list is still shared with the store. The PR body (`:125-127`) says "Copies, so a reader cannot change the history". The views only read it, so this is a wording problem, not a bug.

## Unconfirmed

- **Item 3, the three existing test files touched in b3c23cb:** I see only the net diff against main, and these files are new in the row, so I cannot see their earlier versions. I identified them from the code as `test_ops1_views.py`, `sa01_checkout/test_ops1_staff_wiring.py` and `staff.test.tsx`. At head:
  - The two `escalated_by_reason` assertions (`test_ops1_views.py:82`, `test_ops1_staff_wiring.py:106`) are independent recounts. The views test now also recounts `other_exceptions` and `processed`.
  - The filters test still checks three filters.
  - Test counts went 17→17 and 9→9 for the gateway files, and 28→32 in Vitest, which matches 4 new tests.
  - I cannot prove nothing was removed.
- **The exact freeze patterns at f7da48c:** I cannot run `git show`. I checked the file list against the plan's 12-item list (`plan.md:43-48`):
  - backend `agents/*`, `plan.py` and `substitutes.py`;
  - `catalog_tool.py`, `order_tool.py` and `policy_tool.py`;
  - `answerers.py`;
  - the assistant, `services/mcp-*`, `llm.py` (which holds `agent_runtime`), `tool_scopes.py` and `chat.py`.
- **The branch side of the COVERAGE conflict:** I can confirm that main's CC-03 and CC-05 are kept unchanged and that the branch's CC-04 line is main's line plus the OPS-1 entry. I cannot see whether the branch had text of its own in CC-03 or CC-05 before the merge.

## What checked out

- **Request 1 (finding 4):** `test_ops1_frozen.py` is gone from the tree and from the diff. Only the records mention it (`PROGRESS.md`, `docs/`).
- **Freeze check on the diff's file list:** it holds. Every changed file is in `COVERAGE.md`, `PROGRESS.md`, `apps/portal/src/**`, `docs/decisions/0028`, `docs/design/staff/*.png`, `docs/reviews/*OPS-1*`, gateway `app.py`, `demo_orders.py`, `handoff.py` and `staff/**`, or gateway `tests/` files. None is under `services/backend`, `services/common`, `services/assistant`, `services/mcp-*` or `evals/`, and none is gateway `chat.py`.
- **Request 2 (finding 6):** the pin now reads `app.routes`, opening included routers, so `include_in_schema=False` routes are seen. Several routes on one path are combined, and HEAD is dropped only when GET is there. It still asserts `/api/cart` is GET only and that every approved path is served.
  - It failed first in two ways: the hidden POST route against the old pin, and the staff routes registered for every method as at bd438d3.
  - It is stricter than the old pin, not looser. `test_chk01_gateway_criteria.py` went from 22 to 23 tests, all passing.
- **Request 3:** the PR body (`:118-127`) names `handoff.py` (`HandoffStores`, read-only, `None` before the first handoff) and `demo_orders.py` (`everyone()`), with the reasons. Both match the code.
- **Request 4 (the merge):** the net diff against main only adds. There is no `-` line in COVERAGE (apart from CC-04 being replaced by a longer version of itself) or in PROGRESS, and no RECORD-ALL file appears, so main's side is kept everywhere. The affected files were rerun one at a time after the merge.
- **Request 5 (look fix):**
  - The tile reads "of N orders with an exception", with N = `exceptions` = 100 on the seeded data (`views.tsx:64`).
  - The chips give escalated orders only, with counts.
  - `order_counts` (`views.py:221-236`) makes total = escalated + other_exceptions + processed and exceptions = escalated + other_exceptions, and the reasons sum to escalated.
  - The tests pin all of this on both sides, and the other tiles were checked as asked.
  - "Other exceptions", `unrecorded`, `escalated_by_reason` and the moved assertions are all disclosed in the PR body (`:61-75`).
  - Single test files only.
- **Standing conditions:** no `make ci` has been run, no frozen file was edited, and no file on the control surface or in `RUNNER_SYNC_PATHS` changed.

## Checked

1. Scope: the requests were done as asked. Beyond the letter of request 2, the routes are GET only and there is a new allow-list entry; both are disclosed (finding 2). Nothing reached into another window's paths.
2. Failing first: the CHK-01 tightening, yes, both ways. The look fix, yes but cut short (finding 5).
3. No weakened test: `test_chk01_gateway_criteria.py` is tightened, not loosened. The three b3c23cb files look the same strength or stronger at head; their earlier versions are unconfirmed.
4. Hard rules and protected paths: none broken. The freeze holds on the diff's file list. Finding 2 is for human attention.
5. Spend and window: not applicable, as nothing was recorded. The PR body says the spend is none.
6. Revised plan and records: every requested change is in the PR body. The plan, PROGRESS and decision 0028 are partly out of date (finding 1).
7. Control surface: no file changed.

## Control surface

None. No file in the diff is on the CLAUDE.md control surface or in `RUNNER_SYNC_PATHS` (checked against `Makefile:199`).
