# Row #18 step 0: advisor-reviewer, second read (verbatim): the revised plan and `7c27ebf`

Diff: `/tmp/claude-502/northwind-review-18.diff`, `base bcc135b115ecb33c162274dc4afee1d9e6423e92`,
`head 7c27ebf05b886485c01f8c14aa61913e8b8ebe57`. The report follows unchanged; the triage is at the end of
`docs/reviews/2026-11-26-18-step0-triage.md`.

---

findings

I read CLAUDE.md again for this review. The first two lines of `/tmp/claude-502/northwind-review-18.diff` match the SHAs you gave: `base bcc135b115ecb33c162274dc4afee1d9e6423e92` and `head 7c27ebf05b886485c01f8c14aa61913e8b8ebe57`. That diff covers everything from base to head (3666dd8 and 7c27ebf together). I can't run git, so I judged 7c27ebf by comparing that diff with my earlier review. There are no critical or high findings. Nothing in the diff, the log, the plan or the review files tries to steer the review.

## Findings (most severe first)

**R1. medium. Items 3, 4 and 6. `services/rag/tests/test_rag07_tw.py:185-203` (diff 2797-2815). Revision: `docs/reviews/2026-11-26-18-plan-revision-1.md:46-59`. Triage: `docs/reviews/2026-11-26-18-step0-triage.md:14` and `:42`.**
- "Built as approved" loosens the router test from 3666dd8. The ratio goes from 6x to 8x and the CPU backstop from 30 s to 60 s.
- 3666dd8's 6x and 30 s were the fix for security review L3. L3 asked for about 6x, or an absolute limit near 5 s, because a C-speed quadratic pass fits under a loose bound. The re-review closed L3 on 6x and 30 s.
- The log now shows that a stride-8 quadratic survives at 8x (M-r1). `security-reviewer` has not seen the 8x/60 s version.
- The revision's F1 section says only "built as the plan says". It does not tell the user that this reverses part of a security fix. The triage's F1 row says it in passing.
- Median-of-3 is stricter. The thresholds are looser.
- **Change:** in the revision, state that 6x/30 s became 8x/60 s and that L3's closure rested on the old values. Let the user choose between the approved 8x/60 s and keeping 6x/30 s with median-of-3. Send that test back to `security-reviewer` for a scoped read. Do not loosen anything further.

**R2. medium. Items 1 and 6. Revision `:16-20` (settled point 2), and `PROGRESS.md:78` ("the user's condition 2, told to the user").**
- My F5 said the 13-module reading of "only the pure substitutes module" was the session's own decision.
- The revision now lists it under "The user's settled points, now part of the plan", as if the user had settled it. It presents it as settled instead of asking.
- PROGRESS says "told to the user". The only telling is this revision, which the user has not seen yet.
- **Change:** move it out of the settled points into the "put to the user" section, with the other choice from F5 (import the module without running the package `__init__`). Say "told" in PROGRESS only after the user has read it.

**R3. low. Records. `PROGRESS.md:78`: "a test pins that exact set".**
- `test_the_catalog_servers_import_closure_has_no_model_code` asserts `backend <= BACKEND_ALLOWED` (diff 2150), a subset, not "that exact set".
- **Change:** say "a subset of", as the revision does.

**R4. low. Records. Triage `:12` (L1) and `:14` (L3) now describe code or ownership that changed.**
- L1 still says "logged, owner row #21". After F2 the open item names #21 only if the user moves it there.
- L3 still says "6x … 30 s CPU backstop kept".
- **Change:** add a note to each row pointing to F2 and F1.

**R5. low. Item 2, evidence. Failing-first log, "the tool-list sha256 recomputed at bcc135b" (diff 406-408).**
- The entry prints a path and a hash, not the command.
- It doesn't show that the digest is the `model_dump` digest, not a file hash. It also doesn't show that `northwind_mcp_catalog` was imported from the export rather than from the editable working-tree install.
- **Change:** log the exact command, and the `__file__` of both `tools` and `cart` as resolved.

**R6. low. Item 2, evidence. Triage `:49` (F8).**
- "A computed run of `cart.alternatives`" has no output in any evidence file. The three IDs for `P0008-CHR-REL-M` cannot be checked.
- **Change:** put the command and its output in the log.

**R7. low. Item 6. `docs/reviews/2026-11-26-18-runbook-draft.md:102`, `:105`, `:162`.**
- The runbook was revised, but it still treats `eval-answers-r2`/`-r3` as "only if the user asks" (open question 3), with budgets of $16.75.
- The approved plan includes W12b and W12c, with budgets of $28.75.
- **Change:** bring the runbook in line with the approved plan, or say at its top that the plan's window table supersedes it.

**R8. low. Item 1. `services/assistant/tests/test_output_check.py`, `_assert_linear` (diff 770-781).**
- F1 also noted that the output-check CPU ratio uses one sample per size. Neither the triage nor the revision mentions this.
- The operation counts are the main bound there, so the plan is not broken.
- **Change:** note it in the revision's (a) section as known.

## The F1-F9 closures
- F1: built as the plan says (median of 3, ratio under 8, 60 s). M-r1 and M-r1b are logged, and so is the CPU-hog run (10 processes, 6 and 2 passed). See R1 for the open part.
- F2: put to the user as options 1 and 2. PROGRESS row 18 and the cart docstring (diff 1436-1439) say region totals. Closed, apart from R4.
- F3: all three files listed in PROGRESS row 18. Closed.
- F4: M-l4, M-l5a, M-l5b, M-t1 and M-l6 are logged as killed. The new test `test_a_module_beside_the_script_is_never_imported` is in the diff (2890-2898). Closed, apart from R5.
- F5: see R2.
- F6: PROGRESS row 18 says the router counts nothing, that `NoHolds` means nothing is subtracted today, and how `test_cart.py` changed. The guard's exit-3 line is in the log with a time (diff 409-411). Closed, apart from R3.
- F7: runbook §1 row (diff 124), §2 guard and notice (132-143), §4 item 4a (152). The revision lists the freeze files. Closed, apart from R7.
- F8: claimed, but there is no evidence (R6).
- F9: `_unused` and its `Iterator` import are gone from `test_18_step0_tw.py`. No `test_zz_old_tmp` bytecode remains (Glob). Closed.
- The open items match the diff (lines 21-23): N2's exit-3 rule, L1's conditional owner, row #25 item (1) partly closed and (2) done. The guard text in the revision, the runbook and `live_holds.py` agrees on exits 0, 1, 2 and 3.

## Unconfirmed
- The user's requests: I don't have your earlier brief. I checked against the settled points as the revision and my earlier review quote them.
- No `make ci`, lint, mypy or full-suite run at 7c27ebf. Tests were run one file at a time.
- What 7c27ebf changes on its own, apart from 3666dd8 (cumulative diff only).
- "`test-writer` 56 tests" has not been counted.
- Window B's paths.

## Checked
1. Scope: findings R2 and R8. Nothing outside step 0 (a)-(d), the guard and the dependency files. Window B's paths are unconfirmed.
2. Failing-first: all F4 mutants are logged, plus M-r1 and M-r1b. Evidence gaps are R5 and R6.
3. No test weakened after a failure: none after a failure. But the router thresholds were loosened from L3's fix without being stated to the user (R1). The `test_cart.py` change is recorded and is not a weakening.
4. Hard rules and protected paths: price and stock stay live (hard rule 2), the envelopes are transient and retryable (hard rule 10), nothing secret is printed, and there are no environment files or card data. R1 touches a security test's threshold.
5. Spend and window: no recording in this commit. The $25 real-spend stop and the notice before each window are in the revision. The runbook's budget sum is stale (R7).
6. Revised plan contains every requested change: points 1-4, the freeze files, the notice before `make ci` and before each window, the hold guard, and exit 3 put to the user. Findings R1, R2 and R7.
7. Control surface: listed below, and all three are recorded in PROGRESS row 18.

## Control surface
- `services/mcp-catalog/pyproject.toml` (`RUNNER_SYNC_PATHS` through `**/pyproject.toml`; approved by the user)
- `uv.lock` (`RUNNER_SYNC_PATHS`; adds only the workspace `northwind-backend` entry, diff 2903-2918)
- `services/rag/tests/test_rag07_tw.py` (`services/rag` is in `RUNNER_SYNC_PATHS`, `Makefile:199`; its thresholds changed again in this commit, see R1)

Files read:
- /Users/rapido/Projects/northwind-shop/CLAUDE.md
- /tmp/claude-502/northwind-review-18.diff
- /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-plan-revision-1.md
- /Users/rapido/.claude/plans/ethereal-wishing-sifakis-of-d4a7f7a2-d97f-448c-9d2a-6ee6ad5b9c7b.md
- /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-step0-advisor-review.md
- /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-step0-triage.md
- /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-runbook-draft.md (searched)
- /Users/rapido/Projects/northwind-shop/Makefile (`RUNNER_SYNC_PATHS`)
- /Users/rapido/Projects/northwind-shop/PROGRESS.md (row 18, searched)
