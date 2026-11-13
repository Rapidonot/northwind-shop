# HARDEN-1: failing-first log and the M1 measurement

Branch `req/HARDEN-1-runner`, base `origin/main` `1e1cfd9`. Spend before the row: $2.950179 (`make llm-spend`).

## M1 measurement: the bundled CLI against a scripted refusal (2026-11-12)

The rig (`exp_refusal.py`, kept in the session scratchpad; the committed test is the same rig):

- `ScriptedLLM` stands in for the runner and Anthropic. It serves one refusal SSE first. Its `message_delta` has
  `stop_reason: "refusal"` and `stop_details.category: "cyber"`, the shape of RT-016 seq 0 at `d7b9216^`. Five
  plain answers follow.
- The proxy runs in record mode in front of it.
- The real bundled CLI (`claude_agent_sdk` 0.2.178) runs through `agent_runtime(extra_env=…)` and
  `agent_options()`, with model `claude-sonnet-5-5`, `max_turns=1` and no tools.
- No network and no spend.

Each row ran at least twice; every repeat gave the same result. "Reached upstream" is what the scripted server
received, which stands for what the runner would receive.

| CLI env added | Requests that reached upstream | `fallback-credit` sent? | Turn result |
|---|---|---|---|
| none (today's `child_env`) | 2: the refusal, then a retry with `fallback-credit-2026-06-01` | **yes**, and recorded | answer from the retry |
| `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1` | 2: the refusal, then a plain retry, allowed betas only | **no** | answer from the retry |
| `CLAUDE_CODE_DISABLE_REFUSAL_RETRY=1` (or `=true`) | 1, **already carrying** `fallback-credit`. Then 4 local `refused-unpriced` for fallback model `claude-sonnet-5`, stripping betas each time | **yes, on the first request** | error |
| both of the above | 2, as with no flags | **yes** | answer from the retry |
| `DISABLE_REFUSAL_FALLBACK=1` + `NO_MODEL_FALLBACK=1` | 2, as with no flags | **yes** | answer from the retry |
| `CLAUDE_CODE_NO_MODEL_FALLBACK=1` | 2, plain retry | no | answer from the retry |
| `CLAUDE_CODE_REFUSAL_FALLBACK_CATCH_ALL=0` | 2, as with no flags | yes | answer from the retry |

Findings:

1. **The defect reproduces on a scripted server**, so the end-to-end test can fail first. Today's CLI sends
   `fallback-credit-2026-06-01` on the retry after a refusal.
2. **The plan's premise was wrong.** It assumed both flags together make the refusal the turn's only request.
   Measured, both together behave like no flag, and `DISABLE_REFUSAL_RETRY` alone is worse.
3. **No setting tried ends the turn on the refusal itself.** The best one, `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`
   alone, keeps the beta off every request. A refusal then ends as **two** recorded requests: the refusal, and the
   CLI's plain retry with its "stopped by a safety classifier" note. Both are deterministic and replay by hash.
4. **The switches combine in ways you cannot guess from their names**, so the flag is pinned by an end-to-end test
   on the bundled CLI, and the proxy tripwire is what fails closed.

### Second measurement, after `advisor-reviewer` (F1, F2): replay, and refusing the retry

The rig (`exp2.py`, scratchpad) has the same scripted refusal and `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`. It runs
one record run, then two replay runs on the same cassettes.

- **A, the flag alone:**
  - Record: 2 requests, both `claude-sonnet-5-5`, hashes `6a6ba1e17fcc` and `45aa2e3fea4a`, both recorded.
  - Replay 1 and replay 2: both `hit`, the same two hashes, 0 misses.
  - The turn ends with the retry's text, `success`, in all three runs.
  - Upstream saw 2 requests in total.
- **B, the flag plus a local 400 for the CLI's post-refusal retry.** The proxy matched the CLI's note "stopped by a
  safety classifier" in the request body; this was a monkeypatch in the rig, not committed code.
  - Record: the refusal is recorded (`6a6ba1e17fcc`). Then the CLI's retry is refused locally 3 times (the CLI
    retries the 400 itself).
  - Replay 1 and replay 2: the refusal is a `hit`, then the same 3 local refusals, 0 misses.
  - The turn ends in all three runs as the CLI's error result "API Error: 400 …".
  - Upstream saw **1** request in total.
- Scope of both: one refusal scenario on the bundled CLI 0.2.178, `max_turns=1`, no tools, and a scripted server
  shaped like RT-016's recorded refusal. It is not shown that a real API refusal drives the CLI identically, nor
  how a tool loop behaves.

## Failing-first runs

**How each run was set up.** The new and changed tests ran with the three source files set back to `1e1cfd9`:
`llm.py`, `proxy.py` and `pytest_plugin.py`, put aside with `git stash push -- <paths>` and restored with
`git stash pop`, both clean. `git diff 1e1cfd9 -- services/common/src services/llm-replay/src` was 0 lines
during each run.

The command:

`LLM_MODE=replay uv run pytest -p no:cacheprovider -q -rA services/llm-replay/tests/test_harden1.py
services/common/tests/test_llm_factory.py -k "harden1 or refusal or fallback or child_env"`

**Run 1:** 19 failed, 3 passed. One test failed for the wrong reason: `test_plugin_fails_a_test_that_sent_a_fallback_beta`
counted outcomes wrongly (the inner test passes; the error is at teardown). The test was fixed, and run 2 replaces
this run as evidence.

An earlier attempt at run 2 stashed nothing (zsh passed the paths as one pathspec), so it ran against the fixed
code: 22 passed. It is not evidence and is listed only so the log is complete.

**Run 2 (the evidence): 19 failed, 3 passed, 147 deselected.**

| Test | Unfixed result and reason |
|---|---|
| `test_proxy_never_forwards_a_fallback_beta` ×6 (2 betas × record, replay, live) | FAILED. Record and live: `assert 200 == 400`, because the beta was forwarded. Replay: `['miss'] == ['refused-fallback']`, a miss that invites a re-record |
| `test_the_prefixes_are_exactly_the_two_fallback_families` | FAILED, no such constant |
| `test_ordinary_betas_still_pass_the_tripwire` | FAILED, no `fallback_attempts`. A guard: it fails only because the attribute is new |
| `test_plugin_fails_a_test_that_sent_a_fallback_beta` | FAILED, `nomatch: '*fallback beta*HARDEN-1*do not re-record*'` |
| `test_refusal_ends_as_two_recorded_requests_that_replay_with_no_miss` | FAILED: `no_fallback_beta(upstream_betas(…))` is False. **The real CLI sent `fallback-credit-2026-06-01` upstream** |
| `test_with_the_flag_dropped_the_tripwire_fails_closed_the_same_way_every_run` | FAILED, no `fallback_attempts` |
| `test_proxy_log_scrubs_metadata_user_id_and_the_scan_passes` | FAILED: `'<the 16-digit sandbox test number>' not in '{"auth": …'`, **the Luhn-valid run was in the log** |
| `test_an_unparsed_body_is_logged_as_a_placeholder_never_raw` | FAILED: `[None] == ['<missing>']` |
| `test_child_env_is_built_from_scratch` (FOUND-2, the key set updated) | FAILED, the new key is missing |
| `test_child_env_sets_only_the_refusal_fallback_flag` | FAILED |
| `test_extra_env_cannot_undo_the_refusal_setting` ×4 | FAILED, no ValueError |
| `test_runner_allow_list_and_refusal_cap_are_unchanged` | PASSED. A pin: it must hold before and after |
| `test_the_scanner_still_fails_the_unscrubbed_line` | PASSED. Shows the scanner is unchanged |
| `test_a_scrub_that_raises_never_logs_the_raw_body` | PASSED. A guard: unfixed code already fails before logging |

**With the fix: 169 passed** (the whole of `test_harden1.py` and `test_llm_factory.py`).

## Step 0 (the flag, every CLI replay), 2026-11-12

| Suite | Result |
|---|---|
| `make test` | 5,092 passed, 22 xfailed, **1 failed**: `test_child_env_is_built_from_scratch`, the exact key-set pin, expected and updated above. No cassette miss, no stale |
| pytest half of `make e2e` | **not measured**: Postgres down (`Connection refused` on 127.0.0.1:5432), 41 failed at setup, 0 cassette misses logged |
| `make test-integration` (with the 1,000-order replay) | **not measured**, the same: 273 errors at database connect, 0 misses |
| `make eval-gate` | **not measured**: `make ingest` could not connect to the database |

Docker is terminal-only (stop 3). The three unmeasured suites need `make -C ~/.northwind/runner up` from the user,
then a rerun.

## Addition 3: the runner saves no body (confirmed from the code and by a test)

- `RunnerGate.report()` (`services/llm-replay/src/northwind_llm_replay/runner.py:454-480`) reads only `outcome`,
  `status`, `model`, `first_byte_ms`, `cost_usd`, `usage` and the ledger totals. It prints one line through
  `out` and writes no file.
- The runner's `Proxy` has no `log_path` (`runner.py`, `ProxyConfig(mode="live", …)`), and `Proxy.state.calls`
  drops `body` (`proxy.py`, `_log`).
- `test_the_runner_reports_nothing_from_the_body` (×2, `refused-beta` and `live`): the printed lines hold no
  Luhn run, no `user_id` and no prompt text. The only new file is the ledger's own `ledger.lock`, and it holds
  no Luhn run. This is a guard, so it passes before and after the fix.

## S1 to S4: failing first

- S1: `test_review_diff_default_differs_per_checkout` FAILED on the old Makefile (both checkouts got
  `northwind-review.diff`). `…_is_stable_for_one_checkout` and `test_an_explicit_review_diff_still_wins` are pins:
  they passed before and after.
- S2: `test_the_control_surface_paragraph_names_the_entry[evals/adversarial/**]`,
  `test_control_surface_names_the_path[evals/adversarial/**]` and
  `test_control_surface_lists_the_path[evals/adversarial/rag11_red_team.jsonl]` all FAILED before the edits.
- S4: `test_slice_wording_follows_decision_0013_and_keeps_the_default_after_expiry` FAILED before the edit.
- S3: a guard, not a failing-first test. `make enrich` with no window writes no ledger, before and after; only
  where the check looks changed.

## The user's M1 additions (answer A, 2026-11-12)

**Double refusal measured first** (scratchpad rig `exp3.py`, the committed flag, record plus two replays):

| Scripted refusals | Requests | Fallback beta | Result |
|---|---|---|---|
| 2 | **2** (both refusals, recorded); replays 2 hits, 0 misses | none | the CLI's error result "…safeguards flagged this session…", every run |
| 3 | **2** (the third refusal is never requested) | none | the same |

So the CLI stops after its one plain retry, with no loop. `DOUBLE_REFUSAL_REQUESTS = 2` is pinned.

**Failing first**, with the three new or extended tests at `f0e5ffc` plus the test edits, before the counting code:

- `test_refusal_ends_as_two_recorded_requests_that_replay_with_no_miss`: FAILED, `'ProxyState' object has no
  attribute 'refusals'`. Its new `runner_refusals(rig) == []` assertion ran before that line and passed: the
  runner's own gate accepts both CLI requests.
- `test_a_double_refusal_ends_after_a_pinned_number_of_requests`: FAILED, the same reason.
- `test_the_proxy_counts_refusals_it_sees_and_changes_nothing_else` ×2 (SSE and JSON): FAILED, the same reason.

**With the code:** 174 passed (`test_harden1.py` and `test_llm_factory.py`). The whole `services/llm-replay/tests`
suite: 967 passed.

Counting covers replay hits, buffered responses (JSON, and SSE through the runner) and the sandbox proxy's
streamed SSE. The response bytes, the outcome and the cassette are unchanged (the counting test compares them).
`ScriptedLLM` now also records each request's query string, so the gate check sees what the runner would.

## Step 0, the rest: measured at `24b9448` with the stack up (2026-11-12)

The first attempt after the user's `make up` still got `Connection refused` on 5432, and nothing was listening on
any probed port. A later probe connected. The suites:

| Suite | Result |
|---|---|
| `make ingest` | 2,351 chunks unchanged, 0 embedded, `deleted: 1` (see below) |
| pytest half of `make e2e` | **43 passed**, 0 cassette misses |
| `make test-integration` (with the 1,000-order replay) | **282 passed**, 0 cassette misses |
| `make eval-gate` | **passed**, all 7 metrics equal to the baseline (recall@5 0.954, MRR 0.881, groundedness 0.981, relevance 0.935, false_refusal 0.016, behaviour_ok 0.980, citation_ok 0.960), top-k `99135472…`, **100/100 replayed, stale 0** |

So the flag (and the counting code) changes no recorded model input. Observation, not caused by this row (no data
or RAG file changed): both `make ingest` runs reported `deleted: 1`, a chunk in the index that is not in the
fixtures. It is logged as a low open item for #13b/RAG, the owners of the index.

## S2 scope addition, `evals/contexts/**`: failing first

Before the list edits: `test_the_control_surface_paragraph_names_the_entry[evals/contexts/**]`,
`test_control_surface_names_the_path[evals/contexts/**]` and
`test_control_surface_lists_the_path[evals/contexts/refinements.ndjson]` all FAILED (3 failed, 499 deselected).

## Security review pass 1 fixes: failing first

- L1: `test_counting_never_raises_on_an_odd_delta_and_the_stream_is_still_recorded` ×3 (a string, a list and a
  number `delta`) FAILED before the fix.
- L2: `test_a_checkout_path_with_a_quote_is_refused_and_runs_nothing` FAILED before the fix. `make` exited 0, and
  **`touch MARK` ran**: the file was found inside the quoted checkout directory, so the injection was real.
- With the fixes: 104 passed (`test_harden1.py`, `test_harden1_extra.py`, `tests/hooks/test_advisor_reviewer.py`).

## `make ci`, 2026-11-12/13

- At `2a710a9`: **failed at lint** (`ruff` E501 on a comment this row added in `tests/ship/test_ship.py:963`), so no
  tests ran. Fixed in `64982c4`.
- At `64982c420faee74d097770f8c49aeeaf944d0659` (the head the advisor's row review read): **exit 0**. Lint clean
  (ruff, format, mypy 266 files, eslint, tsc); `scan clean: 0 finding(s)` (regex only); 5,815 passed, 325
  deselected, 22 xfailed (the known ones); 54 Vitest; 282 integration; `eval-integrity passed against origin/main`;
  `eval-gate passed`, all 7 metrics equal to the baseline, `answers replayed 100/100, stale 0`.
- 64 tests carry `req("HARDEN-1")` at `64982c4` (counted by collection over `services/llm-replay/tests`,
  `services/common/tests`, `tests/hooks`, `tests/ship` and `evals/tests/test_enrich.py`). All ran in that
  `make ci`. The S3 test was tagged after it (advisor row review, low 3).
- `make llm-spend` after: **$2.950179** (unchanged).

## Advisor row review fixes: failing first

- **Medium 1:** the tightened `test_a_scrub_that_raises_never_logs_the_raw_body` FAILED on `64982c4`
  (`assert 500 == 400`: the request failed and no line was logged). With the fix (`refused-scrub`, the placeholder
  `<unlogged:unscrubbed-body>`, a structured 400) the whole `services/llm-replay/tests` suite passes, 1,000 tests.
- **Low 2, `test_harden1_extra.py`** (test-writer's 30 tests):
  - Against the base source (`1e1cfd9`, four source files set back): a collection error, because it imports names
    this row adds. So per-test proof comes from three mutations of the head code instead, each restored after:
    - tripwire off (`fallback_betas` returns `[]`): **10 failed**, 20 passed;
    - `stop_reason` always `None`: **9 failed**, 21 passed;
    - the raw body logged: **1 failed**, 29 passed.
  - Every mutant is killed. The tests that survive a given mutant test other code (for example the `stop_reason`
    shapes that expect `None`).
