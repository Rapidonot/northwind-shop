# Row 18a: advisor-reviewer, verbatim (base 81683c2, head e8568e4)

Saved as returned.

---

findings

I read CLAUDE.md first. The diff's SHA lines match the SHAs you gave me: `base 81683c2733a7a5ead1176d739758f981a6246082`, `head e8568e4ba94f9bd52288e575407cd6f4ef7eaa99`. As CLAUDE.md says, a match catches accidents, not tampering. I have no git tool, so I compared base against head only. I could not see edits made inside the row between commits. There are no critical or high findings: 3 medium, 4 low.

## Findings (most severe first)

1. **Item 6, medium: the user's D6 answer is not in the decisions file.** `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-decisions.md:124-144` ends with the D6 question, recommendation and "Waits on it". There is no D6 answer section, unlike D1–D5 at :108-122. The only record of the D6 answer is text the session wrote: commit subject 889ad79, `failing-first.log:164-165` and `pr-body.md:76,87,89`. That covers three things I cannot confirm from the user's own record:
   - (a) What was approved for F3. The recommendation (:135) says other fields get `invalid_argument`. The code sends an over-long `order_id` to `not_found()` (`services/mcp-orders/src/northwind_mcp_orders/tools.py:5447-5448` in the diff), to keep the lookalike pin. That is a fair reason, but the PR body does not list it as a deviation.
   - (b) The F4 scope.
   - (c) The new stop rule ("any low of any class is an accepted residual"), which appears only at `pr-body.md:89`.

   `pr-body.md:8` also still says "decisions D1–D5". Your brief says the file holds D1 to D6 with the answers; on disk it does not.

2. **Items 3 and 1, medium: PROGRESS row 18a is stale and disagrees with the PR body and the log** (diff line 30, `/Users/rapido/Projects/northwind-shop-b/PROGRESS.md` row 18a). It still says:
   - status `stopped (D6)` and "D6 waits on the user before `advisor-reviewer`";
   - "stopped at stop 9 … head `99b3a67`" and "Waiting on the user: D1–D5";
   - "63 mutants" (the log and PR body now total 71);
   - "three passes" (there are four);
   - `make check` at `418614f`.

   The D6 work (889ad79, e8568e4) is not recorded. PROGRESS is imported into CLAUDE.md, so the next session would read the row as blocked on D6.

3. **Item 7, medium: the `evals/baselines` change will land after this review, at a head nobody has reviewed.** By D3, the rescore and promote run after this review and before `make ci`. The diff has no `evals/baselines` hunk, so the baseline change is unreviewed. Once it is committed, `make ci` and `make finish SHA=` will run at a head other than e8568e4. Before `make ci`, the baseline commit needs its own `make review-diff` and an `advisor-reviewer` read. I cannot pass a control surface change I have not seen.

4. **Item 3, low: the survivor count in the PR body does not match the log.** `pr-body.md:51` says "Five survived at first: one was equivalent and removed, and four were closed by an added test." The log names three: P5 (equivalent, `failing-first.log:87`), S8 (:94) and A5 (:134). The total of 71 mutants does match the log; I summed every entry.

5. **Item 3, low: the step-0 report predates D5 (a) and D6.** `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-step0.md:16` describes the old clause-restart rule ("no comma and conjunction or new subject between them"), which D5 (a) removed. The re-runs after D5 and D6 ("0 of 448", 6 against base) are only in `failing-first.log:157,162,165`. The headline numbers agree, but the report itself is out of date.

6. **Item 3, low: there is no `make check` at the head.** `pr-body.md:61` cites `make check` at `4627104`, which is before the D6 code changes in `tools.py` and `output_check.py`. `make check` is never evidence, and `make ci` will run, so this is a record gap only. The `make llm-spend` totals before and after (plan "Verification", and the gate report) are not recorded anywhere yet.

7. **Item 1, low: D2 named a tool-description change that was not made.** D2 (decisions.md:38-39) says the change is in "`request_return` and its tool description". The code leaves the description as it was, on purpose: it is in every recorded request (`tools.py` comment, diff line 5404-5405). That is the right call, but it is not among the PR body's deviations.

## Item 2: the pin and test changes you listed
- **The 24 `D5` strict xfails** are all accounted for:
  - `test_18a_security_fixes.py`: 7;
  - `test_30_check_fixes.py`: 1;
  - `test_30_negation_cut.py`: 1;
  - `test_30_tw_gaps.py`: 8;
  - `test_rag09_output_check.py`: 2;
  - `test_rag09_security_fixes.py`: 5.

  Each sentence is in `WAS_SHOWN` in `/Users/rapido/Projects/northwind-shop-b/services/assistant/tests/test_18a_d5_pins.py:27-47`, pinned as replaced at every stock level, including a=0. Approved by D5 (a), and the user's pre-condition (D1's five still shown) is logged at `failing-first.log:157`. No assertion was removed: each is only marked.
- **The D2 pin** (`test_chk07_request_return.py`, diff 5591-5610) was changed by D2 (b). Refusing "42424242" follows mechanically from the approved rule. The session noted it after the answer (decisions.md:118), not in the question.
- **The wiring test** (`evals/tests/test_wiring.py`, diff 1873-1884) follows from part 2's approved CI change. The new assertion is the exact command, so it is not weaker.
- **The P3-1 pin** (`evals/tests/test_a2a05_partner.py`, diff 1527-1548) was reversed as plan part 6 asked. The served case moved to `test_a2a05_partner_18a.py`.
- **The lookalike pin** (`test_chk07_tw_tools.py`) is not in the diff: it was kept unchanged and is asserted again by `test_an_order_number_with_too_many_digits_is_the_same_not_found_as_an_unknown_one`.
- The decision behind F3's `not_found` is finding 1.

## Unconfirmed
- Edits inside the row to this row's own new test files, between commits (for example whether `test_18a_output_check.py` or `test_a2a05_partner_18a.py` expectations changed after a failure). The base-to-head diff cannot show this.
- The user's D6 answer (finding 1).
- The `evals/baselines` change (finding 3).

## Checked
1. Scope: matches plan parts 0–6, Addendum A and D1–D6. No `evals/src`, `evals/fixtures`, cassettes, Makefile, conftest or prompt changes. Under `services/backend`, only the test file changed. Findings 1, 2, 7.
2. Failing first: every part and every review finding (M1, L1–L7, N1–N10, R1–R6, F3, F4) has a failing-first entry in the log. Review 10's test cannot fail first, which the log says, and it is covered by a mutant.
3. No test weakened against base: the xfails are approved and pinned the other way, and the widened tests (costs, embeddings, wiring) only read more. Evidence mismatches are findings 2, 4, 5 and 6; in-row history is unconfirmed.
4. Hard rules and protected paths: the hard-rule paths touched (`cards.py`, `services/assistant/**`, the backend test only) are named in the plan or Addendum A. Only sandbox test cards appear; there are no secrets and no `.env` access. No security re-review followed the D6 low fixes, which CLAUDE.md does not require for lows.
5. Spend and window: $0 and no window are stated in the plan before any work, and nothing was recorded.
6. Requested changes: parts 0–6 and D1–D5 are present. D6 is present in code, but the user's answer is not recorded (finding 1).
7. Control surface: listed below. `evals/baselines` is pending and unreviewed (finding 3).

## Control surface (flagged for human attention)
- `.github/workflows/ci.yml` (`ci-change`)
- `services/llm-replay/src/northwind_llm_replay/cassette.py` (RUNNER_SYNC_PATHS)
- `services/llm-replay/src/northwind_llm_replay/scan.py`
- `services/llm-replay/tests/test_18a_tw_scan_gaps.py`
- `services/llm-replay/tests/test_canary_and_costs.py`
- `services/llm-replay/tests/test_scan_18a.py`
- `services/llm-replay/tests/test_scan_embeddings.py`
- `services/llm-replay/tests/test_scan_paths_18a.py`
- `services/llm-replay/tests/test_tool_result_order.py`
- `services/common/src/northwind_common/cards.py` (RUNNER_SYNC_PATHS; also a hard-rule-1 path)
- `services/common/tests/test_18a_cards.py`
- `services/common/tests/test_18a_tw_cvv_gaps.py`
- `evals/baselines/**`: not in this diff; it comes from the user's promote (D3) and must be reviewed at its own head.

Not touched: `evals/src`, `evals/fixtures`, `cassettes/**`, `Makefile`, `tools/*`, `.claude/**`, `CLAUDE.md`, conftest files, `.gitignore`, `pyproject.toml`, `package.json`.

Files read: `/tmp/claude-502/northwind-review-18a.diff`, `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-plan.md`, `-decisions.md`, `-failing-first.log`, `-step0.md`, `-pr-body.md`, `-security-rereview-3.raw.md`, `/Users/rapido/Projects/northwind-shop-b/services/assistant/tests/test_18a_d5_pins.py`, `/Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner_18a.py`, `/Users/rapido/Projects/northwind-shop-b/Makefile` (RUNNER_SYNC_PATHS), `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/cli.py` (EVAL_ROOTS).
