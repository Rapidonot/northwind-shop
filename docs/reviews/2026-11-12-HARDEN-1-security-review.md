# HARDEN-1 security review, raw output

Pass 1, `security-reviewer`, of `make review-diff` at base `1e1cfd91abb905c409f676210b25b8b5f73d1335`, head
`686ed4cf554ec44858dcf8378eed4c8c239498f8`. Saved verbatim below. In the original output, `4111…` was the full
16-digit sandbox number; it is shortened here (note 1).

---

I found nothing at medium or above in HARDEN-1. There are 3 low findings and 2 notes.

I reviewed `/tmp/claude-502/northwind-review-HARDEN-1.diff` (base `1e1cfd91abb905c409f676210b25b8b5f73d1335`, head `686ed4cf554ec44858dcf8378eed4c8c239498f8`). I checked it against CLAUDE.md "Hard rules" 1 to 11 and decision 0001, both read fresh. I also read the full current `proxy.py`, `llm.py`, `pytest_plugin.py`, `scrub.py`, `cassette.py`, `cli.py`, the Makefile, and the `evals/src` proxy users. The changed files and lines match the brief: `runner.py`, `scrub.py`, `scan.py`, `refused-paths.sh` and `cassettes/**` are untouched.

## Findings

**L1. Low. `/Users/rapido/Projects/northwind-shop/services/llm-replay/src/northwind_llm_replay/proxy.py:218`. Clause: decision 0016 §3 ("changes nothing else") and decision 0001 §3 (recording).**
- What is wrong: `reason = (data.get("delta") or {}).get("stop_reason")` raises `AttributeError` when a `message_delta` event has a `delta` that is truthy but not a dict (a string or a list). So the refusal counting is not observation-only for every input.
- How it fails, sandbox streaming path (`_note_refusal` at `proxy.py:621` in `_stream`): the whole stream has already been sent and `settled=True`. The exception then skips `_record` and `_log`, and the `finally` block writes no `stream-cut` line. A paid recording is lost and the call leaves no log line.
- How it fails, runner path (`proxy.py:564`): the exception comes after `_settle` and before `gate.leaks(text)`. The caller gets a 500 and `gate.report` never prints the call. This still fails closed: no response text is returned without the leak check.
- How it fails, replay (`proxy.py:416`): a cassette with that shape gives a 500 instead of a hit.
- Reachability: unconfirmed. The real API is not known to send this shape. ScriptedLLM or a hand-built cassette can.
- Fix: check `isinstance(data.get("delta"), dict)` before `.get`, and add a test row to `test_stop_reason_sse_ignores_malformed_data_lines` with `delta` set to a string and to a list. Optionally, call `_note_refusal` after `_record` and `_log` in `_stream`, so a counting fault can never cost a recording.

**L2. Low. `/Users/rapido/Projects/northwind-shop/Makefile:229`. Clause: hard rule 4 / decision 0005 (code that runs outside the sandbox).**
- What is wrong: `$(shell printf '%s' '$(CURDIR)' | cksum ...)` puts the path inside single quotes, so a checkout path containing `'` breaks out of the quotes. For example, `/x/a'; touch /tmp/p; '` runs `touch`.
- `$` is safe. Make inserts CURDIR's value without expanding it again, and single quotes stop the shell from expanding it.
- Why this is low: `REVIEW_DIFF ?=` is deferred and only used in the `review-diff` recipe (`Makefile:232`). Nothing in `tools/` runs `review-diff`, and `ship`, `finish`, `runner-sync` and `eval-promote` never expand it. The runner clone's own path is fixed. So it only runs in a checkout whose path someone chose, under the permissions of whoever ran `make review-diff` there, which for sessions is the sandbox.
- This is narrower than what is already there: `Makefile:192, 210, 221, 289` use `"$(CURDIR)"` in double quotes, where `$(...)` and backticks would run. That is outside this diff and not a finding.
- Fix: stop quoting a make value in the shell, for example `$(shell printf '%s' "$$PWD" | cksum | cut -d' ' -f1)`. Or refuse a path that contains `'` (`$(if $(findstring ',$(CURDIR)),$(error ...))`). Either way, add a test with a quote in the checkout path.

**L3. Low. `/Users/rapido/Projects/northwind-shop/services/llm-replay/src/northwind_llm_replay/cli.py:110-134`. Clause: decision 0016 §2 (a fallback attempt fails closed).**
- What is wrong: only `pytest_plugin.py:116` turns `state.fallback_attempts` into a failure.
- How it fails: under `make dev` / `make llm-proxy` in record or live mode, a CLI that sends the beta gets the local 400, strips the beta and retries. The retry is recorded with nothing but a `refused-fallback` log line, so a broken flag passes quietly outside pytest.
- `make record` runs pytest and is covered. `eval-live` and `enrich` use direct `anthropic_client`s, which do not add the beta.
- Fix: have `cmd_serve` print a stderr line on each `refused-fallback`, or log it as a residual in 0016.

## Notes (not findings)

- **Literal test card in a doc.** `docs/reviews/2026-11-12-HARDEN-1-failing-first.md:92` contains the literal `4111…`. It is the standard Visa sandbox card, allowed by hard rule 3, and already appears in 3 other `docs/reviews` files. `docs/reviews` is not in `EVAL_ROOTS` (`cli.py:27-34`), so the scanner does not see it. The test files build it at runtime (`"4111" + "1111" * 3`); the doc could quote it the same way for consistency.
- **Leftover `extra` gaps.** `child_env` lets `extra` override `ANTHROPIC_BASE_URL` and other `CLAUDE_CODE_*` keys. This predates the row, and the only caller passes `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (`llm.py:157`). It fails closed, since the placeholder key gets a 401 upstream. The other refusal switches are recorded as a residual in 0016.

## Your specific questions

- **Can the tripwire or the counting change what the runner forwards, counts or records?** The tripwire, no: it runs only when `self.cfg.gate is None` (`proxy.py:373`), so the runner keeps its own `refused-beta` and `MAX_REFUSALS`. It runs after the auth check, before the seq number is taken and before any ledger reservation. It forwards the same `headers` dict it inspected, so a duplicated `anthropic-beta` header cannot get past it. The counting adds only `entry["refusal"]`. `gate.report` reads fixed fields, the cassette is built from `key`, `text` and `usage` (`proxy.py:668-682`), and response bytes are unchanged. The one exception is L1.
- **Can the log scrub still write a raw body or the device ID anywhere?**
  - Log: no. It writes `key["body"]` (the same scrubbed body as the match key), the placeholder for unparsed bytes, or `None`. If scrubbing raises, it does so before anything is logged. Non-UTF-8 bytes raise in `json.loads` before any log write (this predates the row).
  - `state.calls`: drops `body` (`proxy.py:295`).
  - `fallback_attempts` / `refusals`: only hash, path, test_id, betas and mode.
  - Miss diffs: built from the scrubbed `key` against cassette match keys.
  - `gate.report`: prints no body (`test_the_runner_reports_nothing_from_the_body`).
  - Device ID: not in headers. Only names are logged, plus the values in `LOGGED_HEADER_VALUES` (`proxy.py:74`), which carry no device ID.
- **ship.sh:** the `evals/adversarial | evals/adversarial/*` case arm is correct shell. `*` matches `/` in `case`, so nested files are covered, and the continuation lines are intact.
- **Test secrets:** `FAKE_REAL_KEY` and `TEST_PAN` are built at runtime. The key does not match an API-key pattern. Every log or cassette with a Luhn run goes to `tmp_path`. The pytester run is in replay mode, so `log_path=None`.

## Checked, no findings

- Rule 1: no PAN or CVV reaches a prompt, the model or the logs. The Luhn test data only goes to tmp files, and the log scrub reduces exposure.
- Rule 2: not applicable to this change.
- Rule 3: synthetic data only. Only the sandbox test card and fake keys are used.
- Rule 4: no secret is printed, logged or committed. No `.env` is read. `test_enrich` now uses a temporary HOME, so the default key path cannot be reached.
- Rule 5: not applicable to this change.
- Rule 6: `llm.py` adds one env flag. Tool access is not changed by any content. Refusal text is only counted.
- Rule 7: not applicable to this change.
- Rule 8: no tool scoping changed in `llm.py` or anywhere else.
- Rule 9: not applicable to this change.
- Rule 10: the tripwire returns the structured envelope (`validation`, `isRetryable` false), and the tests check it.
- Rule 11: not applicable to this change.
- Child processes get the placeholder key only, and only the proxy holds the real key: unchanged (`llm.py:84`).
- The Makefile does not export the key: no new export (L2 is about the path, not the key).
- No key in CI secrets, and CI runs in replay: no workflow changed.
- The key file is never copied into a worktree: nothing in the diff copies it.
- Budget caps are enforced and not raised: the `$90` sentence and "never raised without asking" are kept (pinned by `test_slice_wording_follows_...`). `MAX_REFUSALS == 5` and `ALLOWED_BETAS` are pinned, and the fallback path makes no ledger reservation.
- Cassettes are still scanned for secrets and PANs: `scan.py` and `cli.py` roots are unchanged, `.llm-proxy-log` is still scanned, and `test_the_scanner_still_fails_the_unscrubbed_line` shows the scanner was not loosened.
- Prompt injection: no text in the diff, fixtures or docs tries to steer the review.

---

## Triage (pass 1)

| # | Sev | Done |
|---|---|---|
| L1 | low | **Fixed**: `stop_reason` checks `isinstance(delta, dict)`. Test rows with a string and a list `delta`, failing first |
| L2 | low | **Fixed, fail-closed**: the `REVIEW_DIFF` default refuses a checkout path containing `'` (`$(error …)`), only when the default is expanded. A test with a quote in the path, failing first. An explicit `REVIEW_DIFF=` still works there |
| L3 | low | **Logged** (residual in decision 0016, open item in `PROGRESS.md`; owner: RECORD-ALL prep): outside pytest (`make llm-proxy`, `make dev`), a fallback attempt is only a `refused-fallback` log line |
| Note 1 | – | **Fixed**: the doc no longer spells out the 16-digit number |
| Note 2 | – | Not this row. It predates it and fails closed. Noted only |

---

# Scoped re-review 1 of 2, raw output

Of `make review-diff` at base `1e1cfd91abb905c409f676210b25b8b5f73d1335`, head
`cca0e6324ad0a6c636a8cba610ef5998756fcc8e`. Scope: `0e1b6bb`, `121471d` and `cca0e63`.

Scoped re-review 1 of 2 for HARDEN-1 (diff `/tmp/claude-502/northwind-review-HARDEN-1.diff`, base `1e1cfd91abb905c409f676210b25b8b5f73d1335`, head `cca0e6324ad0a6c636a8cba610ef5998756fcc8e`). I looked only at what changed since `686ed4c`: `0e1b6bb`, `121471d` and `cca0e63`.

**There is nothing at medium or above, and no new finding at any severity.** L1 and L2 are closed. L3 is now logged as a residual. Note 1 (the PAN literal in the doc) is gone from the doc.

## L1: closed
- `/Users/rapido/Projects/northwind-shop/services/llm-replay/src/northwind_llm_replay/proxy.py:217-221`. The code now reads `delta = data.get("delta")` and only calls `delta.get("stop_reason")` when `isinstance(delta, dict)`. The outer `isinstance(data, dict)` check and the `isinstance(reason, str)` check are still there.
- The JSON path at `proxy.py:202-208` was already shape-safe.
- Nothing left in `stop_reason` can raise `AttributeError` or `TypeError` on a malformed value.
- The new test at `/Users/rapido/Projects/northwind-shop/services/llm-replay/tests/test_harden1.py:515-535` drives a non-dict `delta` through the proxy in record mode. It checks that the response bytes are unchanged, the outcome is `recorded`, and nothing is counted as a refusal. That covers the case where a recording was lost.
- Not a finding: `json.loads` on deeply nested input raises `RecursionError`, which `stop_reason` does not catch. `parse_sse_usage` and the usage parse (`proxy.py:552`) parse the same text first with the same exceptions, so the live and record paths fail exactly as they did before. On replay hits, `stop_reason` is the first parse of `hit.body`. A body like that would already have raised when it was recorded, so reaching it needs a hand-edited cassette, which `eval-integrity` refuses.

## L2: closed, and normal use is not affected
`/Users/rapido/Projects/northwind-shop/Makefile:230`:
`REVIEW_DIFF ?= $(if $(findstring ',$(CURDIR)),$(error ...),$(or $(TMPDIR),/tmp)/northwind-review-$(shell printf '%s' '$(CURDIR)' | cksum | cut -d' ' -f1).diff)`

- **Every expansion is guarded.** `?=` makes a recursive variable, so the whole `$(if ...)` runs again on each expansion. Both uses in the recipe (`Makefile:233`, the redirect and the `echo`) go through the guard. `$(if)` expands only the branch it picks, so when the path holds `'` the `$(shell)` never runs.
- **Other metacharacters stay literal.** Once `'` is refused, nothing inside single quotes can end the quoted string: `$`, backticks, `\`, `"`, `;`, spaces and newlines are all literal to the shell. Make inserts CURDIR's value without expanding it again, so `$(...)` inside a path name is not run by make either.
- **Commas in the path are safe.** Make splits function arguments on commas in the source text, before it expands `$(CURDIR)`, so a comma in the path cannot break `$(if)` or `$(findstring)`. The `$(error)` message contains no comma.
- **Normal paths and other targets still work.** The variable is deferred and used only in the `review-diff` recipe. No other target, and nothing in `tools/` (`ship`, `finish`, `runner-sync`, `eval-promote`), expands it, so a quoted path fails only `make review-diff` (and `make -n review-diff`). A path without `'` gives the same `cksum` result as before; the per-checkout and stable-path tests still pin that.
- **An explicit value bypasses the guard, which is fine.** `REVIEW_DIFF=` set on the command line or in the environment means `?=` assigns nothing, so neither the guard nor the `$(shell)` runs. The new test checks that this still works in the quoted checkout.
- **The test is sound.** `/Users/rapido/Projects/northwind-shop/tests/hooks/test_advisor_reviewer.py`, `test_a_checkout_path_with_a_quote_is_refused_and_runs_nothing`, uses the path `x'; touch MARK; '`. It asserts a non-zero exit, `quote` in stderr, and no `MARK` anywhere under `tmp_path`. The failing-first log says `touch MARK` really ran before the fix.
- **Not a finding (it predates the row and is unchanged):** `TMPDIR` from the caller's environment goes into the recipe's `"$(REVIEW_DIFF)"` inside double quotes, as the old default did. It is your own environment, and the runner never expands this variable.

## L3 and note 1
- L3: the residual is recorded in decision 0016 and PROGRESS.md. I accept that. It is not a weakened check.
- Note 1: the literal is removed from the doc. Nothing new was added that a scanner would flag.

## `evals/contexts/**` (0e1b6bb)
- **The `tools/ship.sh` case arm is correct shell.** `/Users/rapido/Projects/northwind-shop/tools/ship.sh:151` adds `evals/contexts | evals/contexts/*` inside the same `case` pattern list, between `evals/adversarial/*` and `conftest.py`. The `|` separators and the trailing `\` continuation are intact, and `*` in a `case` pattern also matches `/`, so nested files are covered.
- **The lists match each other.** `CLAUDE.md` (the control-surface paragraph) and `/Users/rapido/Projects/northwind-shop/tools/advisor-reviewer.proposed.md:44` match ship.sh. Both hook tests pin `evals/contexts/**`, and `tests/ship/test_ship.py` has the row `evals/contexts/refinements.ndjson`.
- `tools/refused-paths.sh` is unchanged, as scoped. No file under `evals/contexts` changed.
- The change only adds a path to the lists. Nothing is loosened.

## Checked, no findings (this delta only)
- Hard rules 1 to 11: the delta touches no prompt, tool, card, retrieval or disclosure path. Rules 1, 3 and 4 are covered by the points above (the doc literal is gone, nothing new would trip the scanner, the key is not touched). The rest do not apply.
- Decision 0001 key handling (the placeholder-only child env, no key export in the Makefile, no CI secret, no key file in a worktree): nothing in the delta touches these.
- Budget caps and `MAX_REFUSALS`/`ALLOWED_BETAS`: unchanged.
- Cassette scanning: `scan.py`, `cli.py` and the scan roots are unchanged.
- Prompt injection: nothing in the delta tries to steer the review.

## Triage (re-review 1)

Nothing new to fix. L1 and L2 are confirmed closed, and L3 is accepted as a logged residual. The second scoped
re-review was not needed.
