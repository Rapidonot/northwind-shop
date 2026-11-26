# BE-04 (row #25) advisor-reviewer, row review at 9a604d5, verbatim

Verdict: **findings** (1 high, 2 medium, 2 low). The SHA lines match (base b5e2d508e9855721a234b2d2cd2d308727e3d784, head 9a604d5d673fc9d33773b1f310a0c3bd4c08db31). Not a pass even after the high is fixed: the Postgres side (race, contract, `make ci`) was not visible at all.

1. **High, item 2:** several security fixes have tests that were never shown failing. The log shows tests failing before their fix for M12, the `US-` postal bug, the wiring pin, M1/L2 and N1–N3, but nothing for:
   - M2: `test_be04_wiring.py:318` `test_a_capture_that_fails_after_the_commit_says_the_stock_needs_restocking` and `:362` `test_a_stock_view_never_reaches_the_model`;
   - L1: `test_be04_wiring.py:290` `test_a_rerun_that_finds_its_hold_no_longer_live_escalates_and_never_authorizes`, which guards a hard-rule-9 path: money authorized against no hold;
   - L6: `test_m12_lines.py:187` (`MAX_QUANTITY`) and `:197` (`MAX_LINES`), which came after the logged 11;
   - L3: the changed expected code `reservation_conflict` in `test_be04_contract.py:238-244`;
   - L4: the reworded `inventory_unavailable` message.

   The "After the security fixes" section shows only replays passing. Fix: show each failing on the code before its fix (revert the fix, or a named mutant per fix) and add the output to the log.
2. **Medium, item 7 (and stop 8):** two control-surface files changed without being flagged. `services/backend/pyproject.toml:6` adds `psycopg[binary]>=3.3.7`, and `uv.lock:738-744` changes with it. Both are in `RUNNER_SYNC_PATHS` (`Makefile:199`: `':(glob)**/pyproject.toml'` and `uv.lock`), so they load into the runner clone. The content is fine: no new `[[package]]` block, only the backend's dependency and specifier lines. But neither is named as control surface in the plan, the PR body or the PROGRESS row, and the advisor's own plan review wrongly said the plan touched none. They need to go to the user explicitly.
3. **Medium, item 6:** the requested pin that every other order is byte-identical was never committed as a test. No test compares version 3's orders with version 2's; `test_found3_generator.py:132` only checks that the committed data equals a fresh build at this head, so the docstring at `generate.py:251-253` claiming it pins this is not true. The property does hold at this head (only the O000476 and O000936 lines change, and the manifest's tracking and label hashes are unchanged), but it has no durable pin.
4. **Low, item 6:** commit before capture is a design change no reviewer or user approval covers. Revision 2 said commit "after a successful capture", and the user's change 3 said "Keep commit() at capture"; revision 3 and `orders_place.py:711-714` commit just before capture. The reasoning is sound and safer for money, and both revision 3 and the PR body disclose it, but no advisor review of revision 3 and no explicit user approval exist. Ask the user to confirm.
5. **Low, item 4:** `test_be08_views.py:399` and `_replay.py:38` still say "stop 5, awaiting the user's approval"; the user approved both, so they should say approved.

**The caller's three choices:**
- Commit before capture: see finding 4; acceptable in substance, but it needs the user's confirmation.
- N3 mapped onto existing entries: accepted. A lost hold maps to `out_of_stock`, `hold_committed` to `not_finished` and `reservation_conflict` to `invalid_line`; `test_be04_wiring.py:410-418` checks none is `port_failure`; `invalid_line` has its own test. `invalid_line`'s action ("correct the order lines with the shopper") is a loose fit for a key conflict: say so in the PR body.
- The $0 pin comparing real stock with very deep stock: acceptable as the unit-level half. It shows a successful reservation changes no tool result and that 0 reservations are refused, but it does not compare old code with new, and the PR body should say so plainly. The model-side half is the 0-miss replay.

**Unconfirmed:**
- None of the 7 Postgres race tests or the Postgres contract tests have run, the read-then-write sensitivity test included, so the acceptance criterion's failing-first evidence and every Postgres claim (M1, L2, L5, U1) rest on reading only.
- The 0-miss replays and the 5,989-hash identity are reported in the log, not seen; the diff contains no `cassettes/` file, which matches. `make ci` must confirm the rest.
- What window B currently owns couldn't be found. Past window B rows (BE-01, BE-07) worked in `services/backend/**`, and this row edits `orders_place.py`, `views.py`, `audit.py` and `escalation.py`.
- Whether only single test files ran, with no `make check` or `make ci`, can't be seen from the evidence.

**Checked:**
1. Scope: within BE-04 + M12. `check_stock`, `mcp-catalog`, `cart.py` and `add_to_cart`'s description are unchanged; open items are logged with owners (`PROGRESS.md:20`).
2. Failing-first: partly shown (finding 1; the race test unconfirmed).
3. Weakened tests: none. `test_be07_units.py:604` was accepted by the user with its own test; `test_be05_labels.py:56` (version 2 to 3) is expected under option B; `test_be08_views.py` and `_replay.py` only add checks (approved, stop 5); the `reservation_conflict` expectation is the row's own new test.
4. Hard rules and protected paths: rule 9 (keys and `dry_run`), rule 10 (envelopes distinct), the audit by reference with no postal code (`views.py:2491-2555`), the stock field out of tool results. No rule broken; `services/backend/**` correctly treated as not routine.
5. Spend and window: $0, no model call added, nothing recorded.
6. Requested changes: 1–5 present, except finding 3 (the missing pin) and finding 4 (an unconfirmed deviation). The uv.lock condition is met; CHK-01 logged as optional for #18; release on cancel logged for #19; `invalid_line` has its own test; `apps/portal/public/products/800/` is not in the diff.
7. Control surface: two files changed and were not flagged (finding 2). No `cassettes/**`, `evals/**`, `conftest.py`, `.claude/**`, `CLAUDE.md`, `Makefile`, workflow, `tools/` or `package.json` file is in the diff; `data/backend/**` is not on the control surface.
