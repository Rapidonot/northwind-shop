findings

The SHA lines match what you gave me: `base 437968795584d9aeb7757ca0d80e4ba15128acf7`, `head 96fd5dbcc650b7349694981bcab30eda833c6376`. A match only rules out an accident; it does not prove the diff. I read CLAUDE.md, my earlier review, the diff's doc hunks, `scripted.py`'s hunk, the failing-first script and log, the demo log, the run log, the recording log, the PR body, the plan, the source lines each swap targets, the cassettes, and the worktree reflog. Nothing in the diff, logs or cassettes tried to steer the review.

Finding 1 is mostly closed: 9 cases ran and every swap is a real revert. One handler I listed has no case (finding A below). The merge and auto-merges are clean. The rest are stale records.

## Findings (most severe first)

**A. Medium (item 2): N4's task-level cancel handler was never seen failing.**
- My finding 1 listed "`run.py`'s two cancel handlers". Security re-review N4 names both levels, `orchestrator.task` and `orchestrator.run`.
- The N4 case in `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-BE-01-failing-first.py:93-98` swaps only `_close_cancelled(audit, action_id, "orchestrator.run", run_inputs)`, at `services/backend/src/northwind_backend/orchestrator/run.py:286`.
- The task-level call at `run.py:151`, `_close_cancelled(audit, action_id, "orchestrator.task", inputs, labels)`, is not reverted in any case.
- Three records still claim both levels:
  - the log line "N4 a cancel from outside closes the task and run attempts";
  - PR body `:41-42` and `:101`;
  - PROGRESS's "9 of 9".
- **Fix:**
  1. Add a 10th case that swaps the `run.py:151` string (it matches once) for `pass`. Run `test_be01_review_fixes.py::test_a_cancel_from_outside_closes_every_open_attempt`. Its assert at `:325` should catch an open `orchestrator.task`.
  2. Append the result to the log and change "9 of 9" to "10 of 10".
  3. If it does not fail, report that as a test gap. Do not weaken anything.

**B. Low (item 4): PROGRESS row #24 is stale after the merge.** `/Users/rapido/Projects/northwind-shop-b/PROGRESS.md:91`
- "**Pending:** merge `origin/main` after #16 ships…" contradicts "Merged origin/main `4379687` (#16) as `ff9a416`" in the same row. The merge is done.
- "closed at `ff9a416`": the revert run ran on the working tree at `ff9a416`, but the evidence was committed in `b0bb931`.
- "Step 1 runs: 82 row tests and 842 neighbouring tests" leaves out the post-merge run: 1001 passed at `b0bb931`, `step1-runs.log:10-12`.
- **Fix:**
  - set Pending to "`make ci` at the final head, the gate report";
  - say "run at `ff9a416`, committed in `b0bb931`";
  - add the 1001 post-merge run.

**C. Low (item 4): the PR body does not record the merge.** `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-23-BE-01-pr-body.md`
- `:106-107` lists only the runs before the first commit. Add the post-merge run: 1001 passed at `b0bb931`, serial, including the 5 recorded replays.
- `:131-133` ("Process") is still in the future tense. It should say:
  - the merge is `ff9a416`;
  - its one conflict was COVERAGE CC-23, resolved by keeping #16's text and then BE-01's;
  - PROGRESS and the three governance files auto-merged;
  - this re-read covered them.

**D. Low (item 2, records): the collection evidence for the left-out files cannot be traced.**
- The script's docstring (`failing-first.py:7-8`) says it "also runs the three test files the first log left out against origin/main's source". Its code does not.
- The log's last block ("The two files the first log left out…") shows two `ModuleNotFoundError`s. It names no test file and saves no command. Only PR body `:103-104` names `test_be01_criteria.py` and `test_be01_review_fixes.py`.
- The third file, `test_be01_recorded.py`, is not mentioned.
- **Fix:** save the exact command and output lines that name each file, and correct the docstring. Or say that `test_be01_recorded.py` is a replay test and was left out on purpose.

## The five checks
1. **Merge resolution: clean.**
   - **COVERAGE:** there is one CC-23 row (grep count 1). In the diff against `4379687`, the new line is main's line, #16's 2026-11-23 sentence included, with BE-01's sentence added before the closing pipe. Neither text lost anything.
   - **PROGRESS:** only row 24 differs from main. So #16's row and its Open items (`PROGRESS.md:166-175`) are main's text byte for byte. There is one row 24, and the table is intact.
   - **Conflict markers:** none anywhere in the repo.
   - **Governance:** only additions, plus two #20 rows (data-flow `services/backend/`, tool-register `get_payment_status`). Each gained one sentence, and the rest of each line equals main. No #16 line was removed.
   - **Citations:** every cited test exists:
     - the four `test_be01_scripted.py` tests;
     - `test_be01_units.py::test_a_plan_that_breaks_a_rule_is_refused_with_its_reason`;
     - the three `test_be01_review_fixes.py` tests;
     - `test_nothing_under_src_but_fakes_and_views_reaches_a_money_call` (`test_be08_views.py`);
     - the FOUND-3 tests.
2. **Finding 1: closed apart from A.**
   - Every swap targets the fix's exact line and matches once in the source on disk.
   - M2's swap reproduces the defect exactly as security review M1/M2 describes it ("used only when `concerns_of(plan)` is empty"). `concerns_of` is imported in `run.py:41`, so the swap cannot fail on a NameError.
   - N1's `return request` matches the parameter name.
   - The failure counts match the node counts (N1 is parametrised).
   - The head pass is confirmed again by the 1001 run at `b0bb931`.
3. **Finding 3: closed.** The demo log shows head `ff9a416` with a `LLM_MODE=replay` proxy for suite `backend-orchestrator`. Its output, "Status: partial (1 of 2 concerns done)", with the parcel tracked and `other` escalated, matches PR body `:53-58`. Tracking number `FWF-KQPRABGM` is in rec1's and rec4's cassettes, which fits the claim that rec5's text replays. The log shows no proxy hit lines; replay mode rests on its header.
4. **Records:** the plan is still accurate. Its "After the advisor's row review" section is right, and the user's condition 1 holds: only `scripted.py` and its test changed in llm-replay, and the merge came before `make ci`. PROGRESS and the PR body need B and C.
5. **Other effects of the merge: none harmful.**
   - `tool_order.py` reorders only a user message that holds two or more `tool_result`s out of `tool_use` order. One cassette in the suite has two: `a5d6e5fb…` (fraud, then payment), already in `tool_use` order. Its key is unchanged.
   - After the merge, that replay no longer depends on which read finishes first. It is less flaky, not more.
   - `ScriptedLLM` is not the proxy, so the scripted tests are unaffected.
   - `scripted.py`'s diff against the new base is still only the route change. The default FIFO path is untouched.

## Unconfirmed
- I have no Bash, so I could not run `git show ff9a416` or `git diff e52b4b3 ff9a416`. I checked the merge result through the review diff against `4379687`, plus the files on disk.
- I could not confirm that `e52b4b3`, `ff9a416`, `b0bb931` and `96fd5db` changed no source or test file beyond what I reviewed at `f4e0ae2`. In this scoped read I did not re-read the source hunks line by line. The reflog subjects say they are records only.
- The run outputs were written into logs by the session, so their wording rests on its say-so. The reflog timing fits them: merge 05:11:38Z, demo 05:13:54Z, `b0bb931` 05:14:29Z, then 184 s later `96fd5db` at 05:17:56Z. All 18 failing-first pytest calls and the demo fit inside the 171 s before `b0bb931`. That is tight, but possible.

## Checked
1. Scope: the merge widened nothing. The only llm-replay files are `scripted.py` and its test, and none of #16's files is in the row diff.
2. Failing first: finding A, otherwise closed. D is a records gap.
3. No weakened test: no test hunk in what I read. Not re-checked across the four commits (see Unconfirmed).
4. Hard rules and protected paths: no `.env`, CLAUDE.md, cap or scanner change in the merged docs. The governance residuals are unchanged.
5. Spend and window: no new spend since my review. The recording log and ledger figures are unchanged.
6. Requested changes: the user's four conditions hold, including the merge before `make ci`.
7. Control surface: listed below. Nothing new since my review.

## Control surface
- `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scripted.py` (in `RUNNER_SYNC_PATHS`; approved on the user's conditions)
- `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/tests/test_scripted_routes.py` (in `RUNNER_SYNC_PATHS`)
- `/Users/rapido/Projects/northwind-shop-b/cassettes/backend-orchestrator/`: 15 files, all added, none modified (`25d4a625…`, `3b781b2a…`, `522bee93…`, `60130637…`, `637b25b0…`, `8a607750…`, `9a833608…`, `9e4fa35e…`, `a0dc6087…`, `a5d6e5fb…`, `a86acea1…`, `b03b4298…`, `b2dd20d7…`, `ecd276f4…`, `f9f76664…`)

Not in the row diff: CLAUDE.md, `.claude/**`, workflows, Makefile, `tools/*`, `evals/**`, `data/enrichment`, `data/embeddings`, any `conftest.py`, any `pyproject.toml`, `.gitignore`, `package.json`.

A, B, C and D are all doc or evidence changes, so each one moves the head. Fix them first, run `make review-diff` again, and then run the one `make ci` at that head. The new head is the `SHA` for `make finish`. A head with only doc changes needs no new advisor read unless A's 10th case does not fail.
