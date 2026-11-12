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
| `test_proxy_log_scrubs_metadata_user_id_and_the_scan_passes` | FAILED: `'4111111111111111' not in '{"auth": …'`, **the Luhn-valid run was in the log** |
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
