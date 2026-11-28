# Row #18: advisor-reviewer's scoped re-read of the pre-`make ci` fixes (verbatim)

Diff: `/tmp/claude-502/northwind-review-18.diff` (`base 3b0e7bf…`, `head 463555c758d5b5e4680fb183048da4230ba36bba`), with
the delta `git diff 22a65bc 463555c`. The report follows unchanged. The session's fixes for N1–N7 come after it.

---

findings

All 14 earlier findings (H1, M1–M5, L1–L8) are fixed as the user decided. The fixes introduce one new medium and six new lows. The medium is a containment claim that no record backs. The lows are wording in the records. The new pin test is correct and nothing outside the findings changed.

The SHA lines match: `/tmp/claude-502/northwind-review-18.diff` starts with `base 3b0e7bf3a9341635b6c77ea369257acdeae679df` and `head 463555c758d5b5e4680fb183048da4230ba36bba`. I read CLAUDE.md, my previous report with the user's decisions, the requests file, the whole delta (`/tmp/claude-502/northwind-review-18-delta.diff`, 10 files) and the end of the failing-first log. I also checked them against the code, the ledger, the cassettes and the runbook. I have no git tool, so I could not run `git diff --stat` myself.

## New findings (most severe first)

**N1 (item 6, medium): one part of the claim that closes row #32's containment item is in no record.**
- `PROGRESS.md:363` and `docs/reviews/2026-11-27-18-m5-open-items.md:36` say EV-090, EV-097 and EV-086 pass containment in all four runs, `eval-judge-repeat` included.
- Wrap-up §4 (lines 118-121) covers only r1, r2 and r3. The W13 record never mentions containment or these items. No results file for these runs is in the repo.
- This is the suite where #32's EV-090 flip happened (`docs/reviews/2026-11-17-32-judged-plan.md:550`). The user approved the closure on this evidence.
- Change wanted: add the per-item containment result for `eval-judge-repeat`, with its source path, to the W13 record. If it cannot be shown, drop `eval-judge-repeat` from both notes and tell the user.

**N2 (items 5 and 6, low): the L1 fix says the `a2a-05-partner` removals followed the clear-first rule, but they did not for run 1.**
- `docs/reviews/2026-11-27-18-pr-body.md:58-60` and `docs/reviews/2026-11-27-18-w11-a2a-05-partner.md:7-9` say the removals "follow" the rule.
- The runbook (`docs/reviews/2026-11-26-18-runbook-draft.md:84-85`) says "Remove the suite's old cassettes before its window, not after it."
- The old cassettes were removed after run 1 had recorded. That is why the recorder refused run 1's fixtures and run 2 needed its own window.
- Change wanted: say the rule was missed at run 1 and applied before run 2. The PROGRESS L3 note ("cleared before W11's fixtures were written") is accurate as it stands.

**N3 (low): the PR body still lists N2 as open.**
- `pr-body.md:112` lists "N2, the guard before each window (#18)" under open items.
- PROGRESS now closes N2 (the outcome note, and the runner wiring is won't-do under 0021).

**N4 (low): the M3 chain stops at `22a65bc`, but the head is now `463555c`.**
- `pr-body.md:73-74` and wrap-up §6 (lines 166-170) tie the final replay to `d83f61c` and then `22a65bc`.
- `463555c` also changes `services/mcp-catalog/tests/test_18_step0_stock.py`. That file has no `llm_suite`, `llm_proxy` or `scripted_llm`, so no model input changed.
- Change wanted: add that one link to both records.

**N5 (low): one guard time does not match its record.**
- `m5-open-items.md:25` gives W7's guard as 10:29:25Z.
- The W7 record (`docs/reviews/2026-11-27-18-w7-assistant-cart.md:5`) says 10:29:24Z.

**N6 (low, wording only): the pinned list is called "the list a model can see", but it holds one tool no model sees.**
- The wording is at `PROGRESS.md:168`, `pr-body.md:96` and the test's name.
- `TOOLS` without `PORTAL_SPECS` still includes `eligible_products`. That tool is scoped to `shopper-query`, which is "Never a model" (`services/common/src/northwind_common/tool_scopes.py:111-115`).
- The pin is a superset of what a model sees, so it is stricter, not weaker. It still equals the user's chosen value. No test change is wanted.

**N7 (low, layout): the H1 bullet is in the wrong section of the PR body.**
- `pr-body.md:93-100` sits under "## Reviews", after the "Final `advisor-reviewer`: ____." placeholder.
- It belongs under "What this does" or "Evidence".

## Per finding

**H1: fixed.**
- `services/mcp-catalog/tests/test_18_step0_stock.py:268-281` pins `TOOLS` without `PORTAL_SPECS` against `fc86e9e8…`.
- `:284-295` holds that `set(PORTAL_SPECS) == tool_scopes.PORTAL_ONLY_TOOLS`, and that no agent except `shopper-portal` names any of them. I checked this logic against `tool_scopes.py:62-118` and `portal.py:203`.
- Failing-first, at the end of `docs/reviews/2026-11-26-18-step0-failing-first.log`:
  - the old pin fails at `22a65bc` (`2b2bc06c…`), 1 failed and 23 passed;
  - after the fix, 25 passed;
  - mutants M-h1 (changed `check_stock` description), M-h2 (`get_price` moved into `PORTAL_SPECS`) and M-h3 (the old all-`TOOLS` pin) are all killed;
  - the log says `tools.py` was restored to `3172127e…`, which matches freeze amendment 2 (`docs/reviews/2026-11-27-18-freeze.md:82`).
- `PROGRESS.md:168` now says exactly what is pinned (see N6 for its wording).
- Narrowing the pin was the user's stop-5 decision. It is not a silent weakening.

**M1: fixed.**
- The guard line for run 1 is now in `w11-a2a-05-partner.md:3-4` and `pr-body.md:35-36`.
- The line itself comes from the transcript, which I cannot see.
- The ledger agrees with it:
  - W7's call settled at 10:33:13Z;
  - there was no ledger activity between then and run 1's first reserve at 10:39:25Z;
  - run 1's nine calls ran from 10:39:25Z to 10:39:53Z;
  - their settled costs add up to $0.0504407, which matches $0.050441.
- The window-open time of 10:38:57Z appears in no artifact I can read.

**M2: fixed** at `pr-body.md:44-45` and `w11-a2a-05-partner.md:79-80`, with the wording that was asked for.

**M3: fixed** at `pr-body.md:73-74` and wrap-up §6 (lines 166-170).
- The `git diff --stat d83f61c 22a65bc` content agrees with `22a65bc`'s commit subject. I could not check it independently.
- See N4 for the missing link to `463555c`.

**M4: fixed** at `PROGRESS.md:78`.
- Every missing part from my list is now in the row.
- The spend adds up: $0.018884 + $0.050441 + $3.269368 + $3.287111 + $1.653420 = $8.279224.
- The cassette counts add up: 1,597 added and 285 removed.
- The status is still `in-progress`, which is correct before `/req-done`.

**M5: fixed as the user ruled, except N1.**
- Every #18 outcome note is in PROGRESS: lines 168, 172, 193, 196, 216, 218, 219, 220, 268, 283, 305, 361-363 and 397.
- #5 (N-1) and #10 (`backend-replay`) are won't-do under 0021, as the user ruled. The other 11 are as proposed.
- I spot-checked the EV-307 check: the r3 judge cassette (`cassettes/eval-answers-r3/8ea78c26….json`) holds four `product` claims, one `site` claim and the "does not state it is stain resistant" claim, as the session reported.
- `evals/tests/test_a2a05_partner.py:53-54` holds 3 and `3a89123f`, as #4 says.

**L1: fixed** at `pr-body.md:57-60`, but see N2.

**L2: fixed** for W11 (`:11-12`), W12c (`:4-5`) and W13 (`:5-6`). W11's run 2 line also covers run 1, which used the same in-process catalog.

**L3: fixed** at `pr-body.md:17-19`. The figures match `services/assistant/tests/test_output_check.py:589-592` (`5 * few`, `4 * L * len`, `8 *`, `60.0`).

**L4: fixed** at `pr-body.md:105-107`.

**L5: fixed** in wrap-up §5 (lines 141-142).

**L6: fixed.**
- The W11 record's "Not committed" notes now point to `b7cbccd` (lines 21 and 67).
- In the W12c record, the "Not committed" note now points to `d6d51ab` and the garbled correction is rewritten (lines 14-20).

**L7: fixed** at `pr-body.md:122-127`.

**L8: fixed** at `pr-body.md:27-29`.

Nothing outside the findings changed. All 10 delta files are records for these findings or the approved pin test.

## Checked
1. **Scope:** the delta is records plus the stop-5 pin test. It enters none of the other window's paths.
2. **Failing-first:** for H1, the old pin was seen failing at `22a65bc` and M-h1 to M-h3 were killed (from the log).
3. **No weakening without approval:** the narrowed pin is the user's stop-5 yes, and the new test makes up for it. Nothing else was loosened.
4. **Hard rules and protected paths:** the delta has no `.env*`, `CLAUDE.md`, `.claude` or hard-rule path. No secrets.
5. **Spend and window:** the delta has no new spend. W11 run 1 now has its guard line, and the ledger agrees with it. N2 concerns the order of the clear-first rule.
6. **Requested changes:** every decision is applied. The gaps are N1 to N7.
7. **Control surface:** the delta touches no control-surface file (`services/mcp-catalog/tests` is not in `RUNNER_SYNC_PATHS`, `Makefile:199`). The full diff's list is below.

## Control surface (full diff, base `3b0e7bf` to head `463555c`; unchanged since my last read)
- `cassettes/a2a-05-partner/`: 9 added, 9 removed
- `cassettes/assistant-cart/`: 1 added, 1 removed
- `cassettes/eval-answers-r2/`: 656 added
- `cassettes/eval-answers-r3/`: 656 added
- `cassettes/eval-judge-repeat/`: 275 added, 275 removed (no cassette modified)
- `evals/src/northwind_evals/answerers.py` (needs `gate-change`)
- `evals/fixtures/a2a-05-partner/answers/*.json` (9 modified)
- `services/mcp-catalog/pyproject.toml` (matches `**/pyproject.toml` in `RUNNER_SYNC_PATHS`)
- `uv.lock` (`RUNNER_SYNC_PATHS`)
- `services/rag/tests/test_rag07_tw.py` (`services/rag` is in `RUNNER_SYNC_PATHS`)
- Not in the diff: CLAUDE.md, `.claude/**`, workflows, Makefile, `tools/*`, `evals/baselines`, `known_failures.json`, any `conftest.py`, `.gitignore`, the root pyproject, the `package.json` files.

No text in the diff, records or cassettes tried to steer the review.

---

## The session's fixes (2026-11-27)
- **N1:** the W13 record now gives `eval-judge-repeat`'s per-item containment, with its source file and sha256: 73
  zero-tolerance items, 0 failing; EV-090, EV-097, EV-086, EV-092 and EV-307 pass. The claim in PROGRESS and the M5
  file stands.
- **N2:** the PR body and the W11 record say the clear-first rule was missed at run 1 and applied before run 2.
- **N3:** N2 removed from the PR body's open items.
- **N4:** the `22a65bc..463555c` link (records plus the pin test, no model input) added to the PR body and wrap-up §6.
- **N5:** W7's guard time is 10:29:24Z, as in the W7 record.
- **N6:** PROGRESS:168 and the PR body say the pin covers every tool a model can be offered, plus `eligible_products`.
  That is a superset, so it is stricter. The test name is unchanged, as the advisor asked.
- **N7:** the H1 bullet moved to the end of "What this does".
