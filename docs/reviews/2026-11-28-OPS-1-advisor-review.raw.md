# OPS-1 advisor-reviewer report, verbatim (base 3b0e7bf, head 34ae255; diff /tmp/claude-502/northwind-review-OPS-1.diff)

**findings**

There are no critical or high findings: 4 medium and 8 low, plus 4 things I could not confirm. Both SHA lines in /tmp/claude-502/northwind-review-OPS-1.diff match what you gave me (base 3b0e7bf3…, head 34ae2556…). The working tree is clean at that head, so I read new files from the checkout and the hunks for modified files from the diff. I skipped the PNG sections and re-read CLAUDE.md first.

## Findings (most severe first)

1. **Item 1, medium: C6 is only partly built, and the PR body does not say so.** File: `services/gateway/src/northwind_gateway/staff/views.py:192-213`.
   - The plan says the escalations (`CaseState`) are added and tests "a Pay (demo) escalation in a test world appears with its real reason".
   - `demo_rows` hard-codes `"reason": None` (:209). It also ignores the `escalation="placement_error"` that `checkout.escalate_unfinished` sets (checkout.py:345-346).
   - `CaseState` is never read; the security review (I3) says the same.
   - `test_a_pay_demo_order_joins_the_orders` (test_ops1_views.py:110) checks `escalated` but not the reason.
   - So an escalated Pay (demo) order shows no reason and is missing from `by_reason`.
   - The PR body only says "Pay (demo) orders are added".

2. **Item 1, medium: the C2 and C7 tests in the plan were not written, and this is not disclosed.**
   - C2's "AST check finds no write method referenced in `staff/`" does not exist. Only the snapshot half is there (`test_ops1_tw_gate.py:208`).
   - C7's "a handoff, a pay and an escalation driven through the real routes appear in their views" does not exist. Every view test overrides `views.sources` with planted stores, and no OPS-1 test calls `/api/checkout` or `/api/handoff`.
   - So two pieces of real wiring are only checked for an empty world (`test_ops1_flag.py:85`):
     - `app.py:80-86` (`_checkout_stores` / `use_checkout`);
     - the store registration in `handoff.py` (`_stores` set in `handoff_desk`).
   - The planned files `test_ops1_readonly.py`, `test_ops1_orders.py`, `test_ops1_stores.py` and `staff.tw.test.tsx` were folded into other files. That is fine, but the gaps above went with them.

3. **Item 2, medium: there is no failing-first evidence for the main security tests.**
   - The log covers only the 13px look fix and the 13 security-fix tests.
   - Nothing shows the C1 gate tests (`test_ops1_flag.py`, `test_ops1_tw_gate.py`) or the C8 screen tests (`test_ops1_screen.py`, `test_ops1_tw_screen.py`) failing first.
   - The PR body does not claim they did, so this is missing evidence, not a false claim.

4. **Item 4, medium: `test_ops1_frozen.py` will block later rows after it merges.** Lines 25-38 and 80-89.
   - The pin diffs the merge base with `origin/main` against the tree for the whole freeze list.
   - Once OPS-1 is on main, any later row that rightly edits the assistant, `services/mcp-*`, backend agents, `llm.py`, `tool_scopes.py`, `chat.py` and so on will fail its local `make ci`.
   - It skips in GitHub CI (shallow clone), so the failure shows only in the local evidence run.
   - Since OPS-1 ships only after RECORD-ALL, the freeze it guards is already over when it lands.
   - Neither decision 0028 nor the PR body says when the pin ends. The user needs to decide this before ship, and any change to the pin is stop 5.

5. **Item 4, low: the same race as L2, on a second cached factory.** File: `services/gateway/src/northwind_gateway/demo_orders.py:74-77`.
   - `demo_orders()` is `@cache`. The staff `sources()` dependency (views.py:90) is sync, so it runs in the threadpool.
   - Before this row every caller was on the event loop (`orders.py:140,154` is async, and so is checkout).
   - An unbounded `functools.cache` overwrites on a race, so a Pay's history entry could land in an instance that is then dropped.
   - The window is tiny (no I/O on construction), but the L2 fix covers only `checkout_desk()`.

6. **Item 4, low, for human attention: the CHK-01 order-route allow-list cannot see the new `/api/staff/orders` route.**
   - `test_chk01_gateway_criteria.py:97-105` reads only `app.openapi()`.
   - `/api/staff/orders` is registered for GET, HEAD, POST, PUT, PATCH, DELETE and OPTIONS with `include_in_schema=False` (`staff/routes.py:24,49-54`), so the pin's `order` regex never sees it.
   - Leaving it out of the schema is right for L1 (it keeps it out of `/openapi.json`), and every non-GET method answers 404.
   - Still, a route with "order" in its path now sits outside an allow-list the user approved as exact. Nobody has noted that.

7. **Item 1, low: the checkout sample trace uses an order number the checkout can never produce.**
   - `staff/traces.py:88` uses `O001001`, but the checkout numbers from `FIRST_ORDER_NUMBER = 900_001` (checkout.py:94).
   - The traces docstring (:6) says "Their references are real".

8. **Item 1, low: the plan's C5 was not updated after the look phase.**
   - C5 says "one per entry point" and "each trace starts at its entry point". After the look phase there are five entry points.
   - "Talk to a person" and "Escalated order (command)" have no trace of their own. The handoff trace starts at `shopper-chat`, as the user asked in 2(b).
   - This was not recorded as a plan change.

9. **Low: PROGRESS row #35 is out of date.**
   - Its test list omits `test_ops1_security_fixes.py`, `test_ops1_tw_gate.py`, `test_ops1_tw_screen.py` and `test_ops1_tw_counts.py`.
   - It does not mention the security review or its outcome.
   - The new Open item does cover L4's remainder.

10. **Low: the L2 database probe proves less than "no connection".**
    - `test_ops1_security_fixes.py:146-159` shows that importing `app.py` succeeds while Postgres is unreachable and that the desk is the in-memory one.
    - It would also pass if a connection were tried and the error swallowed.
    - Reading `checkout.py:308-326` and `app.py:57-60` confirms there is no database work at import: the desk is all in-memory, and `database_url` is used only in lifespan and health.
    - This meets the user's request as written; I am noting the test's limit only.

11. **Low: decision 0028 §4 overstates "nothing executed".** It says the model is "read by AST (nothing executed)". `agent_map.py:10-12` (corrected after I2) says `find_spec` imports the parent packages.

12. **Low: the judge node does not come from `judge.py` as C3 planned.** It is added from `hasattr(llm, "JUDGE_MODEL")` (`agent_map.py:432`). `test_the_judge_module_uses_the_judge_constant` covers the module separately.

## Unconfirmed
- **Exact look spec (2(b)):** the measured spec (box coordinates, separators, edge list and labels) is not in the evidence. I could confirm only the viewBox 1160×778, separators at 292/416/532/696, type sizes 13/12/11/10, edges at 1.25 and 2, and the 3px MCP bar.
- **Tests at head and the Pay tests on their own (request 3):** there is no passing-run output for any listed test file at 34ae255. The checkout unit tests, `test_chk01_gateway_criteria.py`, mcp-catalog's `test_chk01_criteria.py` and `test_chk01_assistant_criteria.py` passing alone is the PR body's word only.
- **Lint:** there is no evidence of ruff, mypy, eslint or tsc at head.
- **Item 3, between commits:** I can see only the net diff, so I cannot tell whether a new test was weakened between the row's own commits.

## What checked out
- **C1–C10 mapping:**
  - C1 (gate): `routes.py`, `flag.py`, `test_ops1_flag.py`, `test_ops1_tw_gate.py`, `test_ops1_security_fixes.py`.
  - C3 and C4 (map, entry points): `agent_map.py`, `test_ops1_agent_map.py`, `mapLayout.test.ts`.
  - C5 (traces): `traces.py`, Python and Vitest trace walks.
  - C8 (screen): `screen.py`, `test_ops1_screen.py`, `test_ops1_tw_screen.py`, and the Vitest no-innerHTML check.
  - C9 (look): `staff.test.tsx` (header, no "Signed in as", 404 shows not-found, lazy chunk).
  - C10: design only, in decision 0028 §7.
  - Every Python test is `req("OPS-1")` and every Vitest describe is `[OPS-1]`.
  - C2, C6 and C7 are partial (findings 1 and 2).
- **Requested changes:**
  - 1(a): patterns copied, pinned equal, the scanner-flags-implies-withheld test uses the same canaries, and the no-import pin is there.
  - 1(b): the full freeze list is pinned.
  - 1(c): held. The PR body and PROGRESS both say no `make ci` and no ship until RECORD-ALL.
  - 1(d): D1–D3 followed.
  - 2(a): SVG elbows, grey only for eval, no dimming by default, "+ cart write tools" from `CART_TOOL_NAMES` pinned equal to `CART_WRITE_TOOLS`, and the Answer end box.
  - 2(b): only the measurable parts of the spec (see Unconfirmed), plus the order of the damaged-parcel trace.
  - 2(c): the exact labels.
  - 2(d): O000800 is pinned to S0188 and delivered, the eight file names are ASCII, and no preview file is left.
  - 2(e): tools at 10px monospace with underscore wrap. The width-fit test failed first at 13px, and the final test matches the logged one line for line.
  - 3: answered. The PR body lists the five `app.py` changes and why.
  - 4: no `make ci` or `make check` was claimed.
- **Security fixes:** L1, L2, L3 and L5 match the review's suggested fixes. L4 is fixed in part, and the rest is logged with owners. All 13 tests failed first, with line numbers matching head.
- **Freeze list and control surface:** no frozen file, no control-surface file and no `RUNNER_SYNC_PATHS` file is in the diff. SA-01's checkout-importer pin still sees only `app.py`.

## Checked
1. Scope: findings 1, 2, 7 and 8. Nothing went outside the row's paths.
2. Failing first: the look fix and the 13 security-fix tests, yes. The C1 and C8 security tests have no evidence (finding 3).
3. No weakened tests: no existing test changed, and the failing-first tests match head. Changes between commits are unconfirmed.
4. Hard rules and protected paths: none broken. Findings 4, 5 and 6.
5. Spend and window: not applicable, no recording. The PR body says $0.
6. Revised plan: the approval and look-phase changes are recorded in the plan. Request 3's `app.py` change is recorded in the PR body, as the user asked.
7. Control surface: none changed.

## Control surface
None. No file in the diff is on the CLAUDE.md control surface or in `RUNNER_SYNC_PATHS`.

Files:
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/views.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/traces.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/routes.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/agent_map.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/demo_orders.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/app.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_ops1_frozen.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_ops1_security_fixes.py
- /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_chk01_gateway_criteria.py
- /Users/rapido/Projects/northwind-shop-b/docs/decisions/0028-demo-staff-console.md
- /Users/rapido/Projects/northwind-shop-b/PROGRESS.md
