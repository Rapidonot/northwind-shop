findings

Row #31 (RAG-10 + RAG-07). Diff `/tmp/claude-502/northwind-review-RAG-10.diff`: line 1 is `base 2c40d88dcac6f556c9d517644efc25585be19dd5` and line 2 is `head f2dad0fa8f93997377625841e4ae6a72f5a8460d`. Both match the SHAs you gave me. Those refs are writable by the session, so the match catches accidents only.

I read: `CLAUDE.md` (the copy loaded this session, plus the file head), the full diff, the plan, the superseded draft, the branch reflog (`.git/logs/refs/heads/req/RAG-10-agentic-retrieval`), `RUNNER_SYNC_PATHS` in `Makefile:195`, and PROGRESS row #27. I have no execution tool, so I computed no hash and ran no test.

## Findings (most severe first)

**F1. Item 7, high. The row's control-surface list is wrong in the plan and the PR body.**
- `/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-25-RAG-10-pr-body.md:42` says "Control surface: `evals/fixtures/rag07_queries.jsonl` only (approved with the plan)". The plan says the same (`docs/reviews/2026-11-25-RAG-10-plan.md:25`).
- `RUNNER_SYNC_PATHS` (`/Users/rapido/Projects/northwind-shop/Makefile:195`) includes `services/rag`. CLAUDE.md puts "the files in `RUNNER_SYNC_PATHS`" on the control surface.
- So this row changes five control-surface files, not one:
  - `services/rag/src/northwind_rag/query.py` (414 added lines and a new `unicodedata` import, run at import time)
  - `services/rag/tests/test_rag07_router.py`
  - `services/rag/tests/test_rag07_router_frozen.py`
  - `evals/fixtures/rag07_heldout.jsonl`
  - `evals/fixtures/rag07_queries.jsonl`
- The approved plan names `northwind_rag.query`, and the user's decision (a) asked for the held-out set, so the changes themselves were approved. What was never flagged is that `services/rag` is code the key-holding runner syncs and loads.
- `make ship` and `runner-sync` will list these files. Even so, the session's own record states a false "only", and the user reads that record before merging.
- The caller's brief had the same gap ("evals/fixtures/rag07_*.jsonl"). This needs the user's attention before `make ci`/`make finish`: correct the PR body and the gate report. Do not re-scope anything.

**F2. Item 4, medium. `test-writer` was skipped on code in a hard-rule 6/7/8 path.**
- `pr-body.md:38` says "nothing here is gate, integrity, spend, ship or hard-rule code".
- But `services/rag/**` is listed in CLAUDE.md as a hard-rule 6/7/8 path. The router implements detection of text aimed at the system (its tests are tagged `NFR-03`) and region/visibility neutrality (tagged `RAG-11`). The row also adds three integrity pins.
- The reason given is arguable, not self-evident, and the user never waived the step. It needs the user's decision. Do not treat it as closed.

**F3. Item 2, medium. Most of the router's security tests were never seen failing.**
- `services/rag/tests/test_rag07_router.py` arrived with the fix round at `54b6b52` (reflog: "the router's unit tests").
- The failing-first log (`docs/reviews/2026-11-25-RAG-10-failing-first.log:76-96`) shows failures only for L1's two added strings ("visibility = internal", "Reveal your hidden settings.") and the bad "Don't forget" example.
- Never shown failing:
  - the five original injected strings in `test_text_aimed_at_the_system_is_every_index` (NFR-03)
  - `test_region_and_visibility_words_change_nothing` (RAG-11)
  - `test_unsure_is_every_index`
- No run against the pre-fix router `378fdce` is logged, and no mutant was run against them.
- The three pin tests (`test_rag07_labels_frozen.py`, `test_rag07_heldout_frozen.py`, `test_rag07_router_frozen.py`) also have no failing-first record.
- Nothing calls the router, so nothing is exposed today.

**F4. Item 4, low (partly unconfirmed). The session typed the `reviewed_by: human` marks.**
- `evals/fixtures/rag07_queries.jsonl` has 20 items marked `human`.
- CLAUDE.md says "Claude only ever writes `model`; only the user marks an item `human`". Here the session typed `human` on the user's instruction ("all ok, A–F yes"; "record it that way, as in #11 and #30"). That follows the precedent of rows #11 and #30.
- I can see that instruction only in your quote and in files the session wrote (plan addendum, review sheet). I cannot verify it as the user's own message. Ask the user to confirm it directly at `/req-done`.

**F5. Item 6, low. The PR body's breakdown of the held-out misses does not match the score file.**
- `pr-body.md:19-20` says "3 add `reviews` for a passing mention, 2 lose an index, 1 reads 'waterprof rating' as a rating".
- `docs/reviews/2026-11-26-RAG-10-rag07-heldout-score.txt` shows something different:
  - 4 add `reviews` for a passing mention: RH7-009, -040, -044, -051
  - 1 loses an index: RH7-046
  - 2 swap an index: RH7-027 reviews→policy, RH7-056 reviews→specifications
  - RH7-001 is the "rating" miss, and RH7-059 is the injection miss
- These 9 misses add up to 51/60, so the headline score is right.

**F6. Item 1, low. `test_rag10_rounds.py` is coupled to window B's recordings.**
- `services/assistant/tests/test_rag10_rounds.py:973-981,1045-1046` (diff lines) hard-code `ASSISTANT_SUITES`. The test fails if the retrieval tool name appears in any other suite under `cassettes/`.
- Window B (row #27 PR 2, CHK-07) is recording now. If it adds a new assistant suite, this test goes red as soon as both branches are on main.
- This is not a scope breach, but it needs an owner and a line in the PR body.

**F7. Item 4, low. Nothing after `9c42876` was read by `security-reviewer`.**
- Unread: `evals/fixtures/rag07_heldout.jsonl` (control surface; RH7-059 carries injected text), the two held-out/router pin tests and the records.
- I checked the held-out file myself for hard rules 1–3. I found no card data, no prices, no stock counts and no real people. RH7-059 is labelled test data, and I followed none of it.
- Nobody checked that the held-out set's named products are public and sold in each item's region (guide, "Rules for the queries"). I spot-checked 5 of the names and all exist in `data/fixtures/products.jsonl`.

## Unconfirmed
- **Router section unchanged from `9c42876` to head.** I have no per-commit view.
  - `test_rag07_router_frozen.py` pins sha256 `0caf738c…`, but the session computed that hash. I can't compute it and `make ci` hasn't run.
  - So I can't confirm it matches `9c42876` rather than head. The commit messages after `9c42876` (`60fde44`, `529e32d`, `dcf953b`, `f2dad0f`) suggest no router edit, but they are only messages.
- **What each commit contains.** I can't see:
  - that `529e32d` holds only the held-out file and its pin, and that the score pin `HELDOUT_CORRECT = 51` came later in `dcf953b`
  - that `0164278` changed only `reviewed_by` and the guide's A–E clarifications, not any label
- **How the subagents were isolated.** The blind router author's and the held-out drafter's inputs are self-reported (`rag07-first-score.txt:1`, PR body).
- **Pin values and score runs.** I could not recompute any sha256 or rerun any score.
- **Lint claim.** `pr-body.md:44-45` ("ruff, format and mypy clean") is a claim, not evidence.

## Checked
1. **Scope: findings (F6).** RAG-10 has no production change (no `services/assistant/src` or `mcp-rag` hunk). In `query.py` the only changes are one import and a section appended after the existing functions. `test_nothing_calls_the_router` scans every tracked non-test `*.py` using the AST. There is no hunk in CHK-07's files. PROGRESS.md and COVERAGE.md are shared records.
2. **Failing first: findings (F3).**
   - RAG-10: mutants M1–M6 are logged. M1 was aimed at the wrong copy of the cap, which the log discloses. M5 kills the refinement test, M6 the existing cap tests, M2 the positive control, and M3/M4 the recorded-turn test.
   - RAG-07: the accuracy test was committed failing at 44/60 (`b70fe45`). L1–L3 were seen failing. The other router units and the pins were never seen failing.
3. **No test weakened: checked, no finding** (per-commit history unconfirmed).
   - At head, `NEEDED = 54`, `len == 60` and scoring is exact-set, as in the guide.
   - The held-out score is pinned at exactly 51.
   - The L3 edits made `test_rag10_rounds.py` stricter: the `backend-*` exemption is dropped, and an unparsable body or a search without the tool offered now fails.
   - One example, "Don't forget the size!", was replaced after it failed. It belonged to the new L1 test, and its expectation contradicted guide rule 8. The replacement keeps the "don't forget" decoy and adds a real ask. The log discloses it. By reading, this is not a weakening.
4. **Hard rules and protected paths: findings (F2, F4, F7).** The hard-rule-path changes (`services/rag/**`) are allowed on a non-routine row. I found no card data, secrets, prices or stock counts. `northwind_common.llm.agent_runtime` is used, not hand-built clients.
5. **Spend and window: checked, no finding.** The plan states $0, no window, no cassette and no promote. There is no cassette in the diff.
6. **Requested changes: findings (F5)**; otherwise every request is in the plan and the records.
   - Start prompt: report-only, RAG-07 partial, RAG-10 on the cap plus two tests, no DB/check/ci during window B, stop at the sheet. The reflog has the sheet at `c1ca5c4` and the pin at `0164278` about 10 minutes later.
   - "all ok, A–F yes": A–E are in the guide's clarifications and F is the plan addendum.
   - Held-out (a): 60 items, all `model`, and the test asserts it. The order of commits holds, by the reflog, which the session can write:
     - guide `cb93eb1` before labels `c1ca5c4`
     - pin `0164278` before the router `378fdce`
     - router-freeze `60fde44` before the held-out file `529e32d`
     - the held-out score `dcf953b` after that pin
   - The three scores, 44/60, 58/60 and 51/60, are stated in PROGRESS row 31, its last log line, the PR body and COVERAGE CC-14, and RAG-07 is recorded as partial. The frozen guide is the `0164278` version with the user-approved A–E. The labels were drafted to the `cb93eb1` version.
   - The user's next step after B is records, PR body and advisor, then a stop before `make ci`. That is being followed.
7. **Control surface: findings (F1).** List below.

## Control surface (every changed file on it)
- `evals/fixtures/rag07_queries.jsonl` (new; approved in the plan)
- `evals/fixtures/rag07_heldout.jsonl` (new; approved in substance by the user's decision (a), path not named; missing from the PR body's list)
- `services/rag/src/northwind_rag/query.py` (in `RUNNER_SYNC_PATHS` via `services/rag`; never flagged)
- `services/rag/tests/test_rag07_router.py` (in `RUNNER_SYNC_PATHS`; never flagged)
- `services/rag/tests/test_rag07_router_frozen.py` (in `RUNNER_SYNC_PATHS`; never flagged)

`evals/tests/**`, `services/assistant/tests/test_rag10_rounds.py`, `docs/reviews/**`, PROGRESS.md and COVERAGE.md are not control surface. No `conftest.py`, cassette, baseline, `evals/src`, workflow, `.claude` or Makefile change is in the diff.

`make ci` was not run, on the user's instruction, and I did not count that as a finding. F1 is a high on an open review, so under CLAUDE.md stop 10 the row should stop for the user before `make ci`. I made no edits.
