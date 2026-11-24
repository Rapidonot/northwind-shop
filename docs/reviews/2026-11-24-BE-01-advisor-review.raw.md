findings

The SHA lines match what you gave me: `base 85aae5244db8aa9c87268c1d77f50af607727b1d`, `head f4e0ae2b2c0ab0c06d2163664de5037276cf314e`. A match only rules out an accident; it does not prove the diff. I read CLAUDE.md, decision 0021, the plan, both logs, the PR body, all four review records, every non-cassette hunk, and the cassettes for scope. Nothing in the diff, logs or cassettes tried to steer the review. The rec5 injection text is test data.

## Findings (most severe first)

**1. High (item 2): none of the review-fix tests was seen failing first.** `docs/reviews/2026-11-23-BE-01-failing-first.log:1-13`
- The log covers only three files, 45 tests: `test_be01_scripted.py`, `test_be01_units.py` and `test_scripted_routes.py`. It is timed 16:40:28Z, before the security fixes.
- Nothing shows `test_be01_review_fixes.py` failing: 17 tests pinning M1 (the unsafe hard-rule-2 fix), M2, M3, L1, L2, N1, N4, N6, N7 and R5. `test_be01_criteria.py` is missing from the log too.
- Two of these fixes change code that already exists on main: `agents/orders_check.py` (the intent/order check) and `agents/runtime.py` (the `CancelledError` outcome). Decision 0021 rule 3 requires failing-first evidence for behaviour changes.
- A collection error on main's source does not count. It proves a module was missing, not that the fixed behaviour was absent.
- The PR body (`docs/reviews/2026-11-23-BE-01-pr-body.md:81-82`) says "all three new test files fail at collection". The row adds six new test files.
- **Fix,** once tests may run again and before `make ci`: in a scratch worktree at the head, revert one fix at a time and run its test. The fixes are:
  - the `orders_check` intent check;
  - the `runtime.py` cancel handler;
  - `run.py` carrying `plan.failures`;
  - `plan.py`'s `uncovered_id` check and `canonical()`;
  - `aggregate.bound_to_its_order`;
  - `request_problem`'s length check on the escaped text;
  - `run.py`'s two cancel handlers.

  Append each failure to the log and correct PR body lines 81-82. If this is not done, the PR body and the gate report must say plainly that these tests were never seen failing.

**2. Medium (process, item 7): the pending merge moves the head away from what I reviewed.**
- CLAUDE.md "Running a row" and 0021 rule 8 require `make ci` at the head the advisor read, with a re-read if the head moves. Your next steps (merge, then targeted tests, then one `make ci`) leave out the re-read.
- #16 carries window A's llm-replay changes, so the merge may resolve conflicts in `scripted.py`. That would be new, unreviewed control-surface code.
- The four shared docs will conflict too: PROGRESS, data-flow, tool-register and threat-model.
- **Fix:**
  1. Merge, and check the git output for the five error strings.
  2. Run `make review-diff REVIEW_DIFF=$TMPDIR/northwind-review-BE-01.diff` again.
  3. Send me a scoped re-read that lists every conflict resolved.
  4. Run `make ci` once at that head, and use that head as `SHA` in `make finish`.

**3. Medium: the demo claim is not backed by anything.** `pr-body.md:53-54` says it "prints the one answer on the fakes, in replay through the proxy", and PROGRESS row #24 lists "a demo CLI".
- No test calls `__main__.main()`, and no log shows a run.
- `fixture_ports` (`orchestrator/__main__.py:36-46`) seeds no payment and no fraud assessment. The cassettes were recorded on `World.make()`, which has captured payment `pi_000001` and an assessment.
- So in replay, the demo's Orders-subagent requests most likely match no cassette. Only a request identical to a recorded one, such as rec5's text, could replay. This is unconfirmed, and 0021's aim is a feature you can demonstrate.
- **Fix:** run the demo once in replay on a recorded request and save the output to `docs/reviews/`. Or reword the claim to say which requests replay, and that any other request needs the scripted model or a recording window.

**4. Low (item 7, records): the control-surface list leaves out a file.**
- `services/llm-replay/tests/test_scripted_routes.py` is under `services/llm-replay`, which is in `RUNNER_SYNC_PATHS` (`Makefile:189`), so it is control surface.
- The plan (`plan.md:121-125`) and the PR body (`pr-body.md:55-60`) name only `scripted.py`. "It is the only llm-replay change, with its own test" (`:57`) describes two files as one.
- This is inside your condition ("only it and its test"), so it is a gap in the list, not a scope breach.
- **Fix:** list both files under control surface in the PR body and the gate report.

**5. Low: the recording evidence is stated but not saved.**
- `COVERAGE.md:31` says "Recorded … and replayed", but no replay of `test_be01_recorded.py` is on record. The step-1 runs came before the recording, and no tests have run since.
- PROGRESS's "5 of 5 scenarios matched" and the ledger figures $23.251319 → $23.263600 have no saved pytest output and no saved `make llm-spend` output.
- I added up the 15 cassettes' `cost_usd`: $0.0122807, which matches the stated $0.012281. I also confirmed in the cassette that rec5's plan was `tracking` plus `unsupported/other`.
- **Fix:** reword COVERAGE to "replay pending (`make ci`)" until it runs. Save the record run's summary and both `make llm-spend` outputs under `docs/reviews/`. The gate report needs them.

**6. Low: the records do not explain the two recording sessions.**
- rec1–rec4 were recorded at 17:35:57–17:36:15Z. rec5's two cassettes were recorded alone at 17:40:54–56Z. The records just say "the user's windows".
- The spend matching the cassette total suggests no calls were thrown away. Still, the test docstring makes a re-record after a non-matching outcome a stop 7.
- **Fix:** add one line to PROGRESS row #24 or the PR body saying why rec5 was recorded separately, and that it was not re-recorded after a failed outcome.

**7. Low: the test-writer record is not raw.** `docs/reviews/2026-11-23-BE-01-test-writer.raw.md:3` says it was "condensed from its hand-back", but the file is named `.raw.md`. **Fix:** save the verbatim hand-back, or rename the file and say it is a summary.

**8. Low: changes beyond the plan are not listed as deviations.**
- `orders_check.py` was edited, though the plan (`plan.md:18`) says it stays "unchanged". `runtime.py` was edited too. Both edits come from security fixes.
- `errors.py` also adds `invalid_request` and `invalid_plan`; the plan named only `not_supported` and `deadline`.
- `plan.md:101-102` still says "Code plan: none", which contradicts approved change 1.
- data-flow and tool-register edit existing #20 rows (the `services/backend/` row and `get_payment_status`), and COVERAGE edits the shared CC-23 row. The plan said "Only our own rows change here" (`plan.md:75`), so the conflict risk with #16 is higher than planned.
- PROGRESS row #24 does not mention the `orders_check.py` change.
- **Fix:** add one "also changed" line to the PR body and the gate report, and note it on the plan's control-surface line.

**9. Low: small wording errors in the records.**
- `pr-body.md:41-42` credits the run- and task-level cancel closes to `agents/runtime.py`. They are in `orchestrator/run.py` (`_close_cancelled`).
- PROGRESS row #24 says "Every medium was fixed except R1/R2", but R2 is low.
- "A test for each review fix" (`pr-body.md:80`) is not quite true: R4 (check the length before normalising) has no test of its own.

**10. Low, unconfirmed (your condition 3): the step-1 run went beyond this row's tests.**
- The step-1 run (`docs/reviews/2026-11-23-BE-01-step1-runs.log:5-6`) ran 842 tests outside the row before W6 was confirmed closed.
- Until then, condition 3 allowed "only targeted serial runs of this row's tests". The record says you approved "step 1 (targeted serial runs)" after window A's replay ended, but not whether that covered these tests.
- **Fix:** record in the gate report exactly what you approved for step 1.

**11. Low: some later changes had no security read, and nothing says so.**
- R3–R5's fixes, `test_be01_recorded.py` and the 15 cassettes came after the last security pass. 0021 rule 2 allows that for lows, but the PR body should say so.
- `scan-cassettes` has not run on the new suite yet.
- **Fix:** add one line to the PR body. The gate report must show the `make ci` scan result and your key-fragment scan from the runner.

## Checked
1. **Scope:** it matches BE-01 and stays inside the approved plan, apart from finding 8. It stays out of #16's files: no assistant, gateway, mcp-catalog, `services/common` or `test_internal_text.py`. No `evals/**`, no `llm.py`, no root `pyproject.toml`, no new package. Your PROGRESS line on the dropped `services/orchestrator` move is there word for word (`PROGRESS.md:91`).
2. **Failing first:** finding 1.
3. **No weakened test:** no pre-existing test file changed. test-writer's `not_permitted` → `not_run` correction keeps the `not_permitted` check in the audit (`test_be01_criteria.py`, the foreign tracking-number test). The N3/R3 changes rename a test and fix a citation; no assertion was dropped. I could not see the row tests' earlier versions; re-review 2 says every earlier test was kept.
4. **Hard rules and protected paths:** no `.env`, CLAUDE.md, PRD, cap or scanner change. Card data is refused before the model. Everything is read-only, and the planner's only tool is `{submit_plan}`. No run ID, time or deadline appears in any cassette (I grepped for them). Recorded requests use fixture IDs only (O000001, O000003, pi_000001).
5. **Spend and window:** the plan stated suite, under $0.10 and `RUN_BUDGET=1`, with stop 2, before recording. The actual $0.012281 matches the cassettes. The `RUN_BUDGET` actually used is not recorded.
6. **Revised plan:** all four changes and every later decision are in `plan.md:120-139`. The determinism test (`test_the_same_request_twice_makes_the_same_model_input`) compares request hashes per agent. The FIFO pin is there. So are the threat-model row, the two accepted deviations, L4 in the PR body only, and R1/R2 accepted as a residual. Every test cited in COVERAGE, data-flow, threat-model and tool-register exists in the diff.
7. **Control surface:** flagged in the records, but the list is incomplete (finding 4). No `evals/src` change, so no `gate-change` label is needed.

**For the gate report:**
- base `85aae52` and the post-merge head;
- the exact `make review-diff REVIEW_DIFF=…` command;
- `make llm-spend` before and after;
- the full control-surface list below;
- CLAUDE.md's `services/orchestrator` layout line, left for you;
- the AST pin `services/backend/tests/backend_audit/test_be08_views.py:43`, `glob("orchestrator/src")`, listed and not edited (it is in the plan only, not the PR body);
- the two deviations, L4, and the R1/R2 residual;
- the `scan-cassettes` result.

## Control surface
- `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scripted.py` (in `RUNNER_SYNC_PATHS`; approved on your conditions)
- `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/tests/test_scripted_routes.py` (in `RUNNER_SYNC_PATHS`; not listed in the plan or PR body)
- `/Users/rapido/Projects/northwind-shop-b/cassettes/backend-orchestrator/`, 15 files, all added, none modified:
  - `25d4a625…`, `a0dc6087…` (rec5)
  - `3b781b2a…`, `60130637…`, `a5d6e5fb…`, `a86acea1…`, `b03b4298…`, `b2dd20d7…` (rec1)
  - `9a833608…`, `f9f76664…` (rec2)
  - `522bee93…`, `8a607750…`, `9e4fa35e…`, `ecd276f4…` (rec3)
  - `637b25b0…` (rec4)

Not changed: CLAUDE.md, `.claude/**`, workflows, Makefile, `tools/*`, `evals/**`, `data/enrichment`, `data/embeddings`, any `conftest.py`, any `pyproject.toml`, `.gitignore`, `package.json`.
