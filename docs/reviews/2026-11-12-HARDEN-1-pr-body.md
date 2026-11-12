# HARDEN-1: a refusal never sends a fallback beta; the proxy log is scrubbed; control-surface and doc fixes

Row H1 in `PROGRESS.md` (Tier A, before #18 RECORD-ALL). No PRD ID: the user's start prompt is the scope. Not
routine. Plan, with every revision, both advisor plan reviews and the user's answers:
`docs/reviews/2026-11-12-HARDEN-1-plan.md`. Decision `docs/decisions/0016-runner-hardening.md`; the number is
provisional, and #13b may take it, so whichever row merges second renumbers.

## Why

- A classifier refusal made the bundled CLI retry with the `fallback-credit-2026-06-01` beta. The runner refuses
  that beta, and each refusal counts toward its `MAX_REFUSALS = 5`. One RT-016 refusal used 2 of a window's 5,
  and replay could never match the retry (RAG-11, F2).
- The proxy log (`.llm-proxy-log/`) kept the CLI's hex `metadata.user_id`. It failed `make scan-cassettes` at
  #21's scale, and RECORD-ALL records at that scale.

## What changes

**M1. A refusal ends as a fixed, deterministic recorded sequence:** the refusal, then the CLI's plain retry. This
is the user's amended criterion, after measurement showed the planned "one request" cannot be had from the CLI's
settings (option A). No fallback beta is ever sent, and no runner refusal is used.

- `northwind_common.llm.child_env` sets `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`. On the bundled CLI 0.2.178 this
  was the only setting tried that keeps the beta off. `DISABLE_REFUSAL_RETRY` sends it on the first request, and
  combinations bring it back. So `child_env` refuses an `extra` that sets any of the three refusal switches.
- The sandbox proxy refuses any `fallback-credit-*` or `server-side-fallback-*` beta itself, in every mode, with
  a fixed 400. It never forwards one, and the pytest plugin fails the test ("do not re-record"), not as a miss.
  The runner is unchanged (`ALLOWED_BETAS`, `MAX_REFUSALS`, its own `refused-beta`).
- A double refusal ends after exactly 2 requests, pinned.
- The proxy counts the refusals it serves (`ProxyState.refusals`, `"refusal": true` in the log, and an
  `llm_refusals` user property). It changes nothing else.

**M2.** The proxy log writes the scrubbed body the cassette uses (the same `SCRUB_RULES`). A body it cannot parse
is logged as `<unlogged:unparsed-body>`. A body that fails to scrub is refused (`refused-scrub`, a structured
400) and logged as `<unlogged:unscrubbed-body>`. The scanner is unchanged.

**S1.** `REVIEW_DIFF` defaults to `$(TMPDIR)/northwind-review-<cksum of $(CURDIR)>.diff`, with no git call. A
checkout path holding a `'` is refused. An explicit value wins.

**S2.** `evals/adversarial/**` and `evals/contexts/**` (the user's addition) join the control surface in
`CLAUDE.md`, `tools/ship.sh` and `tools/advisor-reviewer.proposed.md` (**for you to install**). Not in
`tools/refused-paths.sh` (Q1).

**S3.** `evals/tests/test_enrich.py` no longer reads the shared ledger. A temporary `HOME` holds a seeded default
ledger instead.

**S4.** `CLAUDE.md`'s LLM-calls rule follows decision 0013: full runs until ~2026-11-24, then the slice default.

## Evidence

- Failing-first log, measurements and step 0: `docs/reviews/2026-11-12-HARDEN-1-failing-first.md`.
- Step 0 (the flag across every CLI replay):
  - `make test` passed.
  - The pytest half of `make e2e`: 43 passed.
  - `make test-integration`: 282 passed, including the 1,000-order replay.
  - `make eval-gate`: equal to the baseline on all 7 metrics, 100/100 replayed, 0 stale.
- `advisor-reviewer`: two plan reads and a row review at `64982c4` (1 medium, 3 low; all fixed, triage in the plan).
- Security review, verbatim with its triage: `docs/reviews/2026-11-12-HARDEN-1-security-review.md`.
- No spend: `make llm-spend` $2.950179 before and after. No cassette added or changed.

## Control surface changed

`CLAUDE.md`, `Makefile`, `tools/ship.sh`, `tools/advisor-reviewer.proposed.md`, and these files in
`RUNNER_SYNC_PATHS`:

- `services/common/src/northwind_common/llm.py`
- `services/common/tests/test_llm_factory.py`
- `services/llm-replay/src/northwind_llm_replay/proxy.py`
- `services/llm-replay/src/northwind_llm_replay/pytest_plugin.py`
- `services/llm-replay/src/northwind_llm_replay/scripted.py`
- `services/llm-replay/tests/test_harden1.py`
- `services/llm-replay/tests/test_harden1_extra.py`

Runner-sync after merge: `services/llm-replay` (proxy, plugin, scripted, tests), `services/common` (`llm.py`, its
test), `Makefile`, `tools/ship.sh`. `scripted.py` was added after the plan.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
