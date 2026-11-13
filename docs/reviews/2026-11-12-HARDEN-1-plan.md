# HARDEN-1 plan: runner and replay hardening before RECORD-ALL

Date: 2026-11-12 · Branch: `req/HARDEN-1-runner` from `origin/main` at `1e1cfd9` · Not routine (stop 1 applies)

HARDEN-1 has no PRD ID and no PROGRESS row yet. The user's start prompt is the scope and the criteria. This row
adds its own row to Tier A, after G1 and before #18, in its first commit. Dependencies (inferred): #17a done; #13b
and the other Tier A rows before #18 are not done. The user said to go on.

## Criteria (from the start prompt)

Must, as the first commits:

- **M1. Fallback-credit retry.** A classifier refusal ends as one recorded, deterministic outcome. The
  `fallback-credit` beta never gets through. `MAX_REFUSALS` is not raised, and `ALLOWED_BETAS` is not widened.
  Failing-first tests use a scripted refusal, with no live trigger.
- **M2. Proxy log scrub.** `metadata.user_id` is scrubbed in `.llm-proxy-log/`, as cassettes already scrub it.
  The test uses a Luhn-valid hex `user_id`. The scanner is not loosened.

Should:

- **S1.** A per-worktree default for `REVIEW_DIFF`, derived from the path, like `LLM_PROXY_PORT`.
- **S2.** `evals/adversarial/**` added to the control surface wherever that list lives.
- **S3.** `evals/tests/test_enrich.py`'s ledger assertion reads a temporary ledger, not the shared one, and proves
  the same thing.
- **S4.** CLAUDE.md's "10–20-item slices" wording reconciled with decision 0013.

Out (logged in PROGRESS "Open items" for after RECORD-ALL): a per-module test server for test speed, merging the
two error modules, and moving `audit.py` to common. #13b's files are not touched (`services/assistant`,
`services/rag`, `services/mcp-rag`, `evals/src`, `evals/fixtures`, `evals/contexts`, `data/`).

## What I found

**M1, the mechanism** (RAG-11 evidence, RT-016; the bundled CLI, `claude_agent_sdk` 0.2.178):

1. seq 0: the model ends with `stop_reason: "refusal"` (`stop_details.category: "cyber"`). That 200 is recorded.
2. The CLI's refusal retry resends with `anthropic-beta` plus `fallback-credit-2026-06-01`. The runner refuses it
   (`refused-beta`, 400) and counts it toward `MAX_REFUSALS`.
3. On the 400, the CLI's own `retry:fallback-credit-strip` path drops betas and tries again (seq 2: refused again,
   counted), then sends seq 3 with no extra beta. That one is forwarded, answered and recorded.
4. In replay, seq 1 has no cassette (`anthropic-beta` is a matched header), so replay always misses.

Two strings in the CLI turn this off. `CLAUDE_CODE_DISABLE_REFUSAL_RETRY` is checked where the CLI decides to
retry a refusal. `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK` gates the refusal-fallback feature. `child_env()` in
`northwind_common/llm.py` builds the CLI's environment from scratch, so both flags go there. The CLI's
environment comes from no other place. *(Superseded: wrong, `extra_env` also reaches it; advisor F3, guarded in `child_env`.)*

**M2.** `Proxy._log` writes `entry["body"]`, the raw body, to `.llm-proxy-log/<suite>.jsonl`. Cassettes store
`match_key(…, scrub(body))`, so only the log carries the CLI's device ID. The runner has no `log_path`, so this
is sandbox-side only. `make scan-cassettes` already scans `.llm-proxy-log` (`cli.py:90`), and the scanner stays
as it is.

## Changes

### M1 (commit 1): a refusal ends the turn, and a fallback attempt fails closed

1. **Primary fix:** `child_env()` sets `CLAUDE_CODE_DISABLE_REFUSAL_RETRY=1` and
   `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`. A refusal is then the turn's last request: one 200, recorded, and
   replayed by hash.
2. **Fail-closed tripwire in the sandbox proxy** (`proxy.py`, before the gate and before cassette lookup, in
   every mode): a request whose `anthropic-beta` has a value starting with `fallback-credit-` or
   `server-side-fallback-` gets a fixed local 400 (`validation`, not retryable). It is logged as
   `refused-fallback`, recorded in a new `state.fallback_attempts`, and never forwarded. So it never reaches the
   runner and never uses a runner refusal. The pytest plugin fails the test on any `fallback_attempts`, with a
   message that names this row and says "do not re-record". It is not reported as an ordinary cassette miss,
   which would invite a re-record. The flags rely on CLI internals; the tripwire is what fails closed if a CLI
   upgrade drops them.
3. **Unchanged:** `runner.py` (`ALLOWED_BETAS`, `MAX_REFUSALS`, the counter), `MATCHED_HEADERS`, and the scanner.

**Step 0, measured before any code:** the existing replay suites must still hit with the two flags set (the
flags must not change `anthropic-beta` or the body). The measure: `make test` plus the pytest half of `make e2e`
in replay. Any miss is a model-input change, so stop 7, and possibly a spending window. I do not expect one: the
flags only touch the refusal path.

Tests, all `@pytest.mark.req("HARDEN-1")`:

- `test_child_env_disables_refusal_retry_and_fallback` *(superseded by revision 1: `test_child_env_sets_only_the_refusal_fallback_flag` and `test_extra_env_cannot_undo_the_refusal_setting`)* (`services/common/tests/test_llm_factory.py`).
  Fails first.
- `test_proxy_never_forwards_a_fallback_credit_beta`, in record, replay and live modes. The scripted upstream must
  see 0 requests, the response is the fixed 400, the outcome is `refused-fallback`, and `fallback_attempts` is
  set. Fails first.
- `test_server_side_fallback_beta_is_refused_the_same_way`. Fails first.
- `test_plugin_fails_a_test_with_a_fallback_attempt` (pytester). Fails first.
- **End-to-end with the real bundled CLI, the scripted refusal** (`test_refusal_is_one_recorded_outcome`): the
  scripted upstream serves the recorded RT-016 seq-0 refusal frame (`d7b9216^`; synthetic, already scanned).
  Through the proxy in record mode, it must see exactly 1 request and write 1 cassette, with no
  `fallback_attempts`. Replay of the same turn must give 0 misses and the same refusal outcome. Failing first
  needs the CLI to retry against a scripted server. If it needs something only the real API sends, this test is
  not failing-first: I will say so in the failing-first log, and the three tests above carry it.
- `test_runner_allow_list_and_refusal_cap_unchanged`: pins `fallback-credit-2026-06-01 ∉ ALLOWED_BETAS` and
  `MAX_REFUSALS == 5`, so the fix cannot drift into loosening either.

### M2 (commit 2): scrub `metadata.user_id` in the proxy log

- `Proxy._log` writes `scrub(body)` instead of the raw body, using the same `SCRUB_RULES` as the cassettes, so
  the two can never drift apart. Nothing else in the entry changes.
- Tests (`HARDEN-1`): a Luhn-valid hex `user_id` in a recorded request does not appear in the log, and
  `<scrubbed:metadata.user_id>` does. With that log, `make scan-cassettes`' scan function passes, while the same
  line unscrubbed still fails it (proof the scanner did not change). The rest of the logged body is
  byte-identical. Both the log test and the unscrubbed-fails test fail first.

### S1 (commit 3): per-worktree `REVIEW_DIFF`

- Makefile: `REVIEW_DIFF ?= $(or $(TMPDIR),/tmp)/northwind-review-$(shell git rev-parse --show-toplevel | cksum | cut -d' ' -f1).diff`.
  `cksum` is POSIX, so it works the same on macOS and CI. An explicit `REVIEW_DIFF=` still wins. CLAUDE.md's
  `review-diff` line says so.
- Tests (`test_makefile_targets.py`): two different checkouts give different defaults, the same checkout gives
  the same one, and an explicit value wins. They use `make -n`/`--eval` against temporary git dirs.
- Your standing `REVIEW_DIFF=$TMPDIR/northwind-review-HARDEN-1.diff` is still used for every review in this row.

### S2 (commit 4): `evals/adversarial/**` on the control surface

- `CLAUDE.md`, in the control-surface paragraph.
- `tools/ship.sh` `classify()`: the surface list.
- `tools/advisor-reviewer.proposed.md`, item 7, for you to install.
- The tests that pin the list: `tests/hooks/test_stop_conditions.py` `CONTROL_SURFACE`,
  `tests/hooks/test_advisor_reviewer.py`, and a `tests/ship/test_ship.py` path-table row
  (`evals/adversarial/rag11_red_team.jsonl` is listed as control surface). The ship test fails first.
- **Not `tools/refused-paths.sh`** (question Q1): that file is not a control-surface list. It refuses a path from
  ever reaching the runner clone, so any PR that touches `evals/adversarial` could never ship.

### S3 (commit 5): `test_enrich.py` off the shared ledger

- The test now also sets `HOME` to a temporary home seeded with `.northwind/ledger.jsonl`, and asserts that file
  is unchanged. That is the default `LLM_LEDGER_PATH` would fall back to. `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`
  and `FASTEMBED_CACHE_PATH` point at the real caches, so `uv` does not re-download. It proves the same thing,
  "no window: nothing written to any ledger", without reading `~/.northwind/ledger.jsonl`.
- If `make enrich` needs the real `HOME` for something else, I fall back to a unit test of
  `localenv.ledger_path()` under a temporary `HOME`, and say which one landed. `evals/tests` is not a #13b path
  and is not on the control surface.

### S4 (commit 6): slice wording

- CLAUDE.md, "How we work", LLM calls: "Live calls only through …; until the credit expires (~2026-11-24, decision
  0013) full runs are fine with a generous `RUN_BUDGET`; after that, 10–20-item slices during development, with
  full runs only at milestones." The `$90` cap sentence is unchanged.
- Decision 0013, "What does not change yet": a dated line saying HARDEN-1 made the change. Decision **0016**
  (new, short) records the CLAUDE.md changes (S1, S2, S4) and M1's choice of a CLI flag plus a fail-closed
  tripwire, rather than allowing the beta or recording the runner's 400s.
- `tests/hooks/test_stop_conditions.py`: a test that the LLM-calls paragraph cites decision 0013 and keeps the
  slice default for after expiry. It fails first.

### Records

- PROGRESS.md: the HARDEN-1 row in Tier A before #18, status and evidence at the end. The open item at
  `PROGRESS.md:200` (proxy-log Luhn) is closed. RAG-11's F2 ("a classifier refusal uses 2 of the window's 5") is
  closed. The three "out" items are logged.
- COVERAGE.md: only if a CC-xx feature is exercised. CC-04 (plan mode) gets this plan.

## Control-surface paths this row changes (all flagged for you)

`CLAUDE.md`; `Makefile`; `tools/ship.sh`; `tools/advisor-reviewer.proposed.md`; files in `RUNNER_SYNC_PATHS`:
`services/llm-replay/src/northwind_llm_replay/proxy.py`, `…/pytest_plugin.py`, `services/llm-replay/tests/*`
(new and changed tests), `services/common/src/northwind_common/llm.py`, `services/common/tests/test_llm_factory.py`.
No `.claude/**` file changes: the advisor list change goes in `tools/advisor-reviewer.proposed.md` for you to
install. No cassette, baseline, `evals/src`, `evals/fixtures`, `known_failures.json`, scanner, cap or gate is
loosened.

`llm.py` is a hard-rule 6/7/8 path (the routine-row list). This row is not routine, so it may change it, but
`security-reviewer` gets it by name.

**Runner-sync paths to expect after merge:** `services/llm-replay` (proxy, plugin, tests), `services/common`
(`llm.py`, its test), `Makefile`, `tools/ship.sh`. Not `runner.py`, `scrub.py` or `scan.py`.

## Spend

None expected. No window. Every test runs on scripted fakes or in replay. The ledger total is recorded before and
after (`make llm-spend`). If step 0 shows the flags change model input, I stop (stop 7) before asking for any
window.

## Chain

Implement M1 and M2 (failing-first log saved to `docs/reviews/2026-11-12-HARDEN-1-failing-first.md`) → S1 to S4 →
`test-writer` → `security-reviewer` on the `make review-diff REVIEW_DIFF=$TMPDIR/northwind-review-HARDEN-1.diff`
output, with its raw report saved to `docs/reviews/2026-11-12-HARDEN-1-security-review.md` → fixes → at most two
scoped re-reviews. After that, a high in code the fixes touched stops the row; anything else is a logged residual
→ PR body `docs/reviews/2026-11-12-HARDEN-1-pr-body.md` → `make ci` once at the head → `advisor-reviewer` (the
same diff path, both SHAs, the failing-first log, this plan) → gate report.

## Approval and the user's additions (2026-11-12)

Approved by the user. Q1 = no (`refused-paths.sh` unchanged). Q2 = yes (both prefixes refused). Additions:

1. **After the tripwire's 400.** The CLI may strip betas and retry (mechanism step 3).
   - **Record:** the stripped request has only allowed betas, so it goes to the runner as an ordinary request.
     It is answered and recorded as the next seq. No request with a fallback beta reaches the runner, and no
     runner refusal is used.
   - **Replay:** the stripped request is served from that cassette. If none was recorded, it is an ordinary
     miss.
   - **Both modes:** the test still fails on `fallback_attempts`, because the tripwire fired, so a broken flag
     can never pass quietly. The tripwire's response is a fixed body, so the CLI follows the same path on every
     run.
   - **Tested with the scripted server**
     (`test_tripwire_then_stripped_retry_is_deterministic`): the CLI runs with the flags *removed* *(revision 1: the one flag's key removed from the launcher env, `test_with_the_flag_dropped_the_tripwire_fails_closed_the_same_way_every_run`)* (simulating an
     upgrade that drops them), twice in record mode and once in replay. Each run must fail on
     `fallback_attempts`. The scripted upstream (standing in for the runner) must see no request with a fallback
     beta. The sequence of logged outcomes and hashes must be identical across runs.
2. **Step 0 covers every CLI replay**, because the flags change the env for every agent, not only the assistant:
   `make test`, the pytest half of `make e2e`, `make test-integration` (with BE-05's 1,000-order backend replay)
   and `make eval-gate` (100/100 replayed, 0 stale). Any miss or stale is stop 7. This is the only `eval-gate` run
   outside `make ci`, and it is the measurement step 0 needs, not evidence.
3. **M2 fails closed.**
   - If a body cannot be parsed as JSON or scrubbing raises, the log gets a fixed placeholder
     (`"<unlogged:unparsed-body>"`), never the raw body. Tested with a non-JSON body that holds a Luhn-valid
     digit run, and with a scrub that raises.
   - The evidence confirms, from the code, that `RunnerGate.report()` saves nothing from the body. It prints
     outcome, model, status and numbers only, and writes no file. `Proxy.state.calls` already drops `body`.
4. **S1** derives the default from `$(CURDIR)` with one `$(shell printf '%s' '$(CURDIR)' | cksum | cut -d' ' -f1)`.
   There is no git call, so nothing can fail outside a repo. The three tests stay.
5. **Decision 0016 is provisional.** #13b may take the number. Whichever row merges second renumbers.
6. **The gate report** lists the runner-sync paths exactly as in this plan, plus any added since.

## Revision 1 (2026-11-12, after measuring M1): for `advisor-reviewer`, then the user

The measurement is in `docs/reviews/2026-11-12-HARDEN-1-failing-first.md`, "M1 measurement". It shows the plan's
M1 premise was wrong.

- **Not as planned:** setting both flags does not make a refusal the turn's only request. Both together behave
  like no flag (the beta is sent). `CLAUDE_CODE_DISABLE_REFUSAL_RETRY` alone is worse: it sends `fallback-credit` on
  the first request. No setting tried ends the turn on the refusal itself.
- **What works:** `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1` alone. The beta is never sent, and no runner refusal is
  used. A refusal ends as **two recorded requests**: the refusal, then the CLI's plain retry (the same model,
  allowed betas, its "stopped by a safety classifier" note). Both replay by hash, and the result is identical on
  every run.
- **Revised M1 change:**
  - `child_env()` sets only `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`.
  - A test pins that `CLAUDE_CODE_DISABLE_REFUSAL_RETRY` is absent.
  - The tripwire, the plugin failure and the runner pins stay as planned.
  - The end-to-end test asserts exactly 2 upstream requests, with no fallback beta on either. It also asserts 2
    cassettes, then replay with 0 misses and the same messages, run twice.
- **Criterion M1 as the user wrote it:** "a refusal ends as one recorded, deterministic outcome".
  - With revision 1 the *outcome* is one and deterministic: the recorded retry's answer, every time.
  - It takes two recorded requests, not one, and the shopper-visible result is the CLI's post-refusal retry,
    not the refusal. By analogy, RAG-11's seq 3 also ended with a post-refusal answer (a different path: a beta
    strip after 400s).
  - *(Superseded by the second measurement: refusing the retry, option B below, ends the turn on the refusal
    without altering any response. No CLI setting tried did so; only 7 combinations were tried.)*
  - **This is a scope question (stop 7) for the user:** accept the two-request outcome, or say what "one" must
    mean.
- Additions 1 to 6 are unchanged. Step 0 now measures `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1` alone.

### `advisor-reviewer` on revision 1: `findings`, no high or critical (raw output in the session; triage)

| # | Sev | Finding | Done |
|---|---|---|---|
| F1 | medium | Replay and "identical every run" were claimed, but only record mode was measured | **Measured** (second measurement): record plus 2 replays, same hashes, 0 misses. Scope stated |
| F2 | medium | The stop-7 options were incomplete. "No such setting" overclaimed; the option of refusing the retry was missing | **Measured, as option B.** The old lines in revision 1 are marked superseded ("no setting tried"). The question below gives both |
| F3 | medium | `extra_env` can override or undo the flag (`llm.py`, `env.update(extra)`) | **Fix:** `child_env` refuses `extra` that sets `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK`, `CLAUDE_CODE_DISABLE_REFUSAL_RETRY` or `CLAUDE_CODE_NO_MODEL_FALLBACK` (ValueError), with a test. The flag-dropped test builds its env with `write_launcher` directly. "Comes from no other place" is marked superseded at its line |
| F4 | medium | The fix is already in the tree before approval, and the failing-first runs are missing | The flag and the F3 guard do not depend on the A/B answer and are **committed in `eaefd39`**, after the failing-first runs. The failing-first runs are taken with `llm.py`, `proxy.py` and `pytest_plugin.py` at `1e1cfd9` (the fix reverted in the tree for the run), and logged below |
| F5 | low | "Same model" was unmeasured; seq 3 is an analogy | Measured (A: `claude-sonnet-5-5` both times). "What seq 3 did" is now worded as an analogy at its line |
| F6 | low | Scripted server ≠ the runner, one scenario, rig in the scratchpad | Scope stated in the measurement. The committed tests are the rig |
| F7 | low | The e2e test and fixture locations were not named | `services/llm-replay/tests/test_harden1.py`. The refusal frame is built in that file (no fixture file, nothing under `cassettes/**` or `evals/fixtures/**`, no `conftest.py`) |
| F8 | low | Stale text: a test name, "flags removed", 0016's text | The old lines are marked superseded with the new test names. The flag-dropped test removes the key (re-read R5). 0016 records the single flag and the measured interaction; the stop-7 answer is **pending** there |

## Stop 7, the question for the user: what ends a refused turn?

Both options keep every "never": the beta never reaches the runner, `MAX_REFUSALS` and `ALLOWED_BETAS` are
unchanged, no cassette is edited, the scanner is unchanged, and the fallback-beta tripwire stays in both.

- **A, the flag alone (simplest).**
  - A refusal ends as two recorded requests: the refusal, then the CLI's plain retry with its note.
  - The turn's outcome is deterministic, but it is the retry's answer, not the refusal. On an adversarial item
    the shopper sees what the model says after the CLI tells it it was stopped.
  - No new proxy logic beyond the tripwire.
- **B, the flag plus a local 400 for the post-refusal retry.**
  - Exactly one recorded request, the refusal. The turn ends as the CLI's error result, the same in record and
    replay, and only one request ever reaches the runner. This matches "one recorded outcome" literally.
  - Costs (advisor re-read R2), none of them measured:
    - The proxy matches the CLI's note text ("stopped by a safety classifier"). A CLI change to the text fails
      the e2e test, so CI catches it, not a quiet pass.
    - Shopper text holding that phrase (for example an adversarial prompt) would get a local 400 and end a turn
      that was never refused. That fails closed: nothing is answered.
    - In a multi-turn session the note may stay in the history. Every later turn would then be refused, so the
      session ends at the first refusal. A narrower match, only the newest user message, might avoid this;
      unmeasured.
    - Both measurements ran with `max_turns=1` and no tools. Behaviour in a tool loop and across turns is
      unknown, and this row would add tests for both.
    - Every refused turn reaches the caller as the CLI's error result ("API Error: 400 …"). How the assistant and
      the gateway show that to a shopper is in `services/assistant` (#13b's files), so this row does not change
      it; it becomes a #13b item.

My recommendation: **B, with the narrower match** (only the newest user message), if a session that hit a refusal
may end there. It is what "one recorded, deterministic outcome" says, and it never shows the shopper a post-refusal
answer, which is in line with absolute containment on adversarial items. If refused sessions must carry on, **A**.

**Done before stopping:** the tripwire, the plugin failure, the flag and the F3 guard; M2; S1 to S4; the
failing-first runs. **Step 0 is only partly done:** `make test` passed with the flag (one expected key-set pin
updated). The pytest half of `make e2e`, `make test-integration` (with the 1,000-order replay) and `make eval-gate`
were **not measured**, because Postgres was down. They need `make -C ~/.northwind/runner up` from the user
(stop 3), then a rerun.

**Waiting on the answer:**
- The e2e test's expected outcome. The committed `test_refusal_ends_as_two_recorded_requests_that_replay_with_no_miss`
  encodes A today and changes under B.
- The post-refusal check (B only).
- Decision 0016's M1 text.
- Then the rest of the chain.

**Residual (re-read R5), for 0016:** the F3 guard covers three keys. Other `CLAUDE_CODE_*` refusal switches, for
example `CLAUDE_CODE_REFUSAL_FALLBACK_CATCH_ALL`, were measured only alone, never with the flag. The tripwire is the
backstop.

## The user's answer to stop 7 (2026-11-12)

**A, the flag only** (the user's decision). Option B is not built. The stack is up (the user ran
`make -C ~/.northwind/runner up`).

**M1's criterion, amended by the user** (it replaces "one recorded, deterministic outcome"):

> A refusal ends as a fixed, deterministic recorded sequence (the refusal, then the CLI's plain retry). No fallback
> beta is ever sent, and no runner refusal is used.

**Added to M1, with tests:**

1. **Double refusal.** A scripted double refusal (the retry is refused too) ends after a bounded number of
   requests, with no fallback beta and no loop. The exact request count is pinned
   (`test_a_double_refusal_ends_after_a_pinned_number_of_requests`).
2. **The proxy counts the refusals it sees.** A response with `stop_reason: "refusal"` (JSON or SSE) adds to
   `ProxyState.refusals`, and its log entry gets `"refusal": true`, so tests and evals can report them. Nothing
   else changes in behaviour: the same response, outcome, recording and cassette
   (`test_the_proxy_counts_refusals_it_sees_and_changes_nothing_else`; replay hits count too).
3. **The real-CLI e2e test asserts:**
   - in record: exactly 2 upstream requests, 0 fallback attempts and 0 runner refusals;
   - in two replays: 0 misses.

   "0 runner refusals" is measured as: no request reached the scripted upstream that the runner's own gate
   (`RunnerGate.refuse`, unchanged) would refuse. The test runs every upstream request through the real gate.

**Order:** first rerun the three step-0 suites (the pytest half of `make e2e`, `make test-integration` with the
1,000-order replay, and `make eval-gate`, which needs 100/100 replayed and 0 stale). Any miss or stale is a stop.
Then the chain, to the full gate report. Stop before `/req-done`, because the user checks the gate report on disk
first.

## Questions (answered above)

- **Q1 (recommended: no).** Leave `evals/adversarial/**` out of `tools/refused-paths.sh`, since that file refuses
  paths outright rather than listing control surface, and put it in CLAUDE.md, `ship.sh` and the advisor proposal
  instead?
- **Q2 (recommended: yes).** The tripwire refuses both `fallback-credit-*` and `server-side-fallback-*` betas.
  The CLI strips and retries both the same way, and neither is on `ALLOWED_BETAS`. Or only `fallback-credit-*`,
  as written in the prompt?

## Scope addition to S2 (the user, 2026-11-12, from #13b's coverage check)

`evals/contexts/**` joins the control surface in the same places as `evals/adversarial/**`: CLAUDE.md's list,
`tools/ship.sh` `classify()`, `tools/advisor-reviewer.proposed.md`, and the pinning tests (`CONTROL_SURFACE` in
both hook tests, and the ship path-table row `evals/contexts/refinements.ndjson`). It is not added to
`refused-paths.sh`. Only the lists change: no file under `evals/contexts` (a #13b path) is touched. It arrived
while `security-reviewer` was reading `686ed4c`, so this commit goes to the scoped re-review.

## `advisor-reviewer`, second plan read (R1–R5): triage

| # | Sev | Finding | Done |
|---|---|---|---|
| R1 | medium | The stop-7 section listed step 0 as done | Fixed in `f0e5ffc`: step 0 marked partial, and the three suites listed as waiting on `make up`. They were measured later at `24b9448` |
| R2 | medium | Option B's costs were incomplete | Fixed in `f0e5ffc`: the four unmeasured costs listed. The user chose A |
| R3 | medium | The triage claimed plan edits that were not made | Fixed in `f0e5ffc`: the old lines are marked superseded, and the triage rows reworded |
| R4 | unconfirmed | Was the flag committed? | Yes, in `eaefd39`, after the failing-first runs. The F4 row was corrected |
| R5 | low | 0016 wording, the flag-dropped test, which option the e2e test encodes, the guard's reach | Fixed in `f0e5ffc` (the key removed, the A note, the residual in 0016) |

## `advisor-reviewer`, row review at `64982c4`: `findings`, no critical or high; triage

| # | Sev | Finding | Done |
|---|---|---|---|
| 1 | medium | An unscrubbable body gave a 500 and no log line, not the placeholder addition 3 asked for. The test was only a guard | **Fixed** in `1e98b29`: `refused-scrub`, `<unlogged:unscrubbed-body>`, a structured 400. The test was tightened and seen failing first |
| 2 | low | test-writer's 30 tests had no failing-first run | **Done**: three mutations, each killed (failing-first log) |
| 3 | low | The S3 test was not tagged | **Fixed**: `req("HARDEN-1")` |
| 4 | low | Records: the Luhn item, RAG-11 F2 and CC-04 not closed | **Fixed**: PROGRESS row #9's two hardening items and the Luhn open item marked closed by H1; COVERAGE CC-04 evidence added |
| U1 | unconfirmed | R1, R3 and R4 triage missing | The table above |
| U2 | unconfirmed | `make ci` at `64982c4` not on disk | Recorded in the failing-first log |
| U3 | unconfirmed | Spend after not on disk | $2.950179, recorded in the failing-first log |

Every fix is in the files this row already changed. Because the reviewed head moved, `make ci` runs again, and the
advisor re-reads the new head.
