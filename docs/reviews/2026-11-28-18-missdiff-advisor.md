# Row #18: advisor-reviewer's scoped read of the miss-diff fix and a37e7cb (verbatim, then the fixes)

Diff: `/tmp/claude-502/northwind-review-18.diff` (`base 3b0e7bf…`, `head d0f6aad9b258eb4e09c6c58c75d5570b8c08be7c`), with
the change `git diff 7668376 d0f6aad`. The report follows unchanged.

---

**findings**: four low findings, nothing at medium or above. All four are about wording in the records or one mutant that was not rerun. None blocks `make ci` on your go.

The SHA lines in /tmp/claude-502/northwind-review-18.diff are `base 3b0e7bf3a9341635b6c77ea369257acdeae679df` and `head d0f6aad9b258eb4e09c6c58c75d5570b8c08be7c`. They match the SHAs you gave me. The change (delta2, `7668376..d0f6aad`) touches exactly 8 files: PROGRESS.md, the failing-first log, the PR body, wrap-up §7, the new security report, and `cassette.py`, `proxy.py` and `test_18_miss_diff_bounded.py` under `services/llm-replay`. I read a37e7cb's two fixes as they stand in the full diff and on disk. I re-read CLAUDE.md first.

## Findings (most severe first; all low)

1. **Item 6, low. The records state the cause of CI's failure more firmly than the evidence shows.**
   - Where: /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-27-18-pr-body.md:59-62 ("held the proxy for 18-24 s … and the chat turn behind it timed out") and /Users/rapido/Projects/northwind-shop/services/llm-replay/tests/test_18_miss_diff_bounded.py:8-9 ("In CI's e2e job two handoff journeys' Care misses held the second suggestion's chat turn past its 90 s timeout").
   - What was measured: the mechanism, locally, with one Care miss against `assistant-smoke` (`/tmp/claude-502/w13script/proxy_block.py`, uncommitted).
   - What is not shown: that this mechanism caused CI's two failures. That holds only if CI's e2e passes at d0f6aad.
   - Wrap-up §7 gets this nearly right: "Cause, measured at $0" and "Not established: why main passed".
   - Suggest: in the PR body and the gate report, call it "the measured mechanism; CI's e2e at the new head confirms it".

2. **Item 2, low. Mutant M-m2 (the character cap removed) was last run before the line cap existed.**
   - Where: /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-step0-failing-first.log:335 ("rerun after the mypy fix: killed"). After the L1-L3 fixes, the log reruns only M-m1, M-m5 and M-m6.
   - From the code, it should still be killed: `big_text` is one string, so `pretty()` gives only a few lines, the line cap does not catch it, and `unified_diff` runs, which the test forbids.
   - That is reasoning, not a run. Rerun M-m2 at d0f6aad and log it. The user named this mutant.

3. **Item 6 (records), low. The H-1 open item claims more than was measured.**
   - Where: /Users/rapido/Projects/northwind-shop/PROGRESS.md:177. It says "one miss no longer stalls other visitors' chats".
   - What the evidence covers:
     - one real Care miss against the 18-cassette `assistant-smoke` suite;
     - the 1 s hit test, which uses a `time.sleep` stand-in. Sleep releases the GIL, so the test proves the diff is off the loop, not that CPU work leaves hits unharmed.
   - What is not bounded: before the cap applies, `_nearest` still runs `canonical()` on every candidate (cassette.py, `by_length`), and `miss_diff` loads the whole suite. That work grows with the suite's size.
   - Not measured: a large suite (for example `backend-replay`, 5,959 cassettes) or several misses at once.
   - The demo opens only `assistant-smoke`, so the claim holds for today's case. It should say so, since H-1 inherits it.

4. **Item 6, low (informational). The user's wording named `asyncio.to_thread`; the code uses `anyio.to_thread.run_sync`** (proxy.py:460).
   - This follows security L3. Under uvicorn's asyncio loop it does the same job, and M-m1 is killed on the new line.
   - Sharing anyio's thread limiter with the ledger calls is harmless: the replay branch and the ledger never run in the same proxy mode.
   - The wrap-up §7 and the log record the change. The PR body does not name it as a departure from the user's wording, so the gate report should.

## Per requirement (the user's instruction)
- **1. Off the loop, and bounded:** met.
  - The diff runs in a thread (proxy.py:460).
  - The nearest cassette is chosen by `real_quick_ratio` over all candidates, then `quick_ratio` on a shortlist of 8, never `ratio()`.
  - Only the nearest is diffed. It is capped at `MAX_DIFF_CHARS` 128,000 and `MAX_DIFF_LINES` 20,000; above either, the output is "(diff skipped (too large): …)" (cassette.py).
  - The miss is logged and counted before the `await` (L2).
- **2. Failing-first, and the named mutants:** met.
  - Before the fix, 4 of 5 tests failed. The hit test failed on behaviour (3.01 s against a 1.0 s bound), as did the nearest-selection test. The cap and shortlist tests failed on missing constants.
  - M-m1 (the diff back on the loop) and M-m2 (no cap) were killed. M-m3 to M-m6 were also killed.
  - The L1 and L2 tests were never run against the pre-fix code itself; mutants M-m5 and M-m6 reproduce that code and both fail.
  - See finding 2 for M-m2.
- **3. Hashing, matching, cassettes and model input unchanged:** met as far as the diff shows.
  - The delta does not touch `scrub.py`, any cassette, or the hit path (proxy.py:429-445 unchanged). The only change sits inside the replay branch, after the miss is decided.
  - The $0 replay with 0 misses on every suite, and the sha256 values `44098d7d…` and `f551e2db…`, come from the session's records. I cannot hash the files, so I could not confirm those sha256s match the committed d0f6aad. Run `shasum -a 256` on both files to check.
- **4. Order of steps:** being followed.
  - The security read found no medium or above. L1-L3 were fixed, and no re-review is required (CLAUDE.md: a re-review only if a medium or higher was fixed).
  - This read is the scoped advisor read. `make ci` waits on the user's go.
- **No timeout raised, no reordering, browser reproduction skipped:** met. No Playwright, e2e or config file is in the delta. The new test's `httpx` timeout of 30 s is its own, not a raised one.
- **The H-1 open item:** logged at PROGRESS.md:177, owned by H-1. It covers the visitor's experience after about Dec 22. See finding 3.
- **Control surface and runner-sync at ship:** stated in the PR body (lines 59-68 and 142), in PROGRESS row 18 and in wrap-up §7. `services/llm-replay` is in `RUNNER_SYNC_PATHS` (Makefile:199). The gate report is not written yet; it must say the same.

## a37e7cb
- `services/assistant/tests/test_rag10_rounds.py:40-42`: adding `eval-answers-r2` and `-r3` to `ASSISTANT_SUITES` makes the test check more, not less. Before, those suites tripped the "an assistant turn in another suite" assertion. Now every turn in them is held to 3 or fewer searches, and `set(per_suite) == ASSISTANT_SUITES` requires both suites to be present. Sound.
- `docs/governance/data-flow.md` (the Internal-only docs row): both W13 cassettes it now cites exist on disk, and the two old ones it dropped are gone. The count of 18 is right: a plain grep for P0009's product name finds exactly 18 cassettes, 2 of them in `eval-judge-repeat`. The 18 = 5 per suite × 3 + 2 + RT-006, and the 10 responses add up the same way. Sound.

## What the records say is proven and what is not
- Wrap-up §7 says why main passed is not established, and lists the evidence and the caps.
- Findings 1 and 3 are where the records claim more than that.
- The PR body's Evidence section (around line 97) still describes the replay before the fix. The replay on the fixed proxy appears at line 65 and in wrap-up §7. That is consistent, not a finding.

## Checked
1. Scope: pass. The fix and its records only, as the user approved at stop 8. Nothing touches the other window's paths.
2. Failing-first: findings (low, finding 2). Everything else was seen failing, directly or through mutants that reproduce the old code.
3. No weakening: pass. No test loosened, no timeout raised, no journey reordered. a37e7cb widens its test.
4. Hard rules and protected paths: pass. No secret, `.env` or card path. The control-surface change is approved (stop 8).
5. Spend and window: pass. $0, ledger $41.545038 before and after, no window.
6. Requested changes: findings (low, findings 1, 3 and 4). Every numbered requirement is met; the gate report is still to come.
7. Control surface: flagged below. It is in the PR body, and runner-sync is noted.

## Control surface
- In this change: `services/llm-replay/src/northwind_llm_replay/proxy.py`, `services/llm-replay/src/northwind_llm_replay/cassette.py` and `services/llm-replay/tests/test_18_miss_diff_bounded.py`. They are in `RUNNER_SYNC_PATHS`, so runner-sync runs at ship.
- In the full diff, `3b0e7bf..d0f6aad`:
  - `cassettes/**`: added in `a2a-05-partner`, `assistant-cart`, `eval-answers-r2`, `eval-answers-r3` and `eval-judge-repeat`, plus removals;
  - `evals/src/northwind_evals/answerers.py`;
  - `evals/fixtures/a2a-05-partner/answers/*` (9 files);
  - `services/mcp-catalog/pyproject.toml`;
  - `uv.lock`;
  - `services/rag/tests/test_rag07_tw.py`.
- a37e7cb touches no control-surface file.

---

## The session's fixes (2026-11-28)
- **1:** the PR body and the test's docstring call it the measured mechanism, which CI's e2e at the new head confirms
  or refutes.
- **2:** M-m2 rerun at `d0f6aad`: 1 failed, 6 passed (killed). Logged.
- **3:** the H-1 item now says what was measured: one Care miss against the 18-cassette `assistant-smoke` suite.
  `canonical()` still runs on every candidate and the suite is loaded whole, so cost grows with suite size. A large
  suite and several misses at once were not measured.
- **4:** the PR body names `anyio.to_thread.run_sync` in place of the user's `asyncio.to_thread`, after security L3.
- **sha256 check:** `proxy.py` `44098d7d…` and `cassette.py` `f551e2db…` at `d0f6aad` match the replay record.
