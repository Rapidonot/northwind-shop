# 0016: A refusal ends deterministically; review diffs per checkout; adversarial evals on the control surface

Date: 2026-11-12 · Row: HARDEN-1 (no PRD ID) · Status: decided by the user (plan approval with Q1 = no and Q2 =
yes, additions 1 to 6; the stop-7 answer A and the amended M1 criterion; all 2026-11-12)

Plan, with the measurements, the advisor triage and the questions: `docs/reviews/2026-11-12-HARDEN-1-plan.md`.
Measurements: `docs/reviews/2026-11-12-HARDEN-1-failing-first.md`.

**Numbering is provisional.** Row #13b may also take 0016. Whichever row merges second renumbers (the user,
2026-11-12).

## Context

After a classifier refusal, the bundled CLI retried with the `fallback-credit-2026-06-01` beta. The runner refuses
any beta off `ALLOWED_BETAS` (decision 0005), and each refusal counts toward `MAX_REFUSALS = 5`. One RT-016
refusal used 2 of a window's 5, and replay always missed on the retry (RAG-11 evidence, F2). Separately, the
proxy log kept the CLI's hex `metadata.user_id`, which tripped the Luhn check at scale (row #21).

## Decision

1. **The CLI env sets `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1`, and only that flag** (`northwind_common.llm.child_env`).
   - Measured on the bundled CLI 0.2.178 against a scripted refusal: this flag alone keeps the fallback beta off.
   - `CLAUDE_CODE_DISABLE_REFUSAL_RETRY` sends the beta on the first request. Combinations such as the flag plus
     `CLAUDE_CODE_NO_MODEL_FALLBACK` send it again.
   - So `child_env` refuses an `extra` that sets any of the three. The behaviour is pinned by an end-to-end test
     on the bundled CLI, so an SDK upgrade that changes it fails CI.
2. **The sandbox proxy refuses any `fallback-credit-*` or `server-side-fallback-*` beta itself**, in every mode,
   with a fixed local 400.
   - It never forwards such a request, so a fallback attempt costs no runner refusal.
   - The pytest plugin fails the test with its own message, never as a cassette miss to re-record.
   - The runner is unchanged: `ALLOWED_BETAS`, `MAX_REFUSALS` and its own `refused-beta`.
3. **What ends a refused turn (the user's answer to stop 7, 2026-11-12: option A).** The criterion, amended by the
   user, is: "a refusal ends as a fixed, deterministic recorded sequence (the refusal, then the CLI's plain
   retry). No fallback beta is ever sent, and no runner refusal is used."
   - A double refusal (the retry refused too) ends after a pinned number of requests.
   - The proxy counts the refusals it sees, in its state and log, and changes nothing else.
   - Option B, a local 400 for the CLI's post-refusal retry, was measured but not built.
4. **The proxy log is scrubbed** with the cassettes' own `SCRUB_RULES`. A body it cannot parse is logged as a
   fixed placeholder, never raw. The scanner is unchanged.
5. **`REVIEW_DIFF` defaults per checkout:** `$(TMPDIR)/northwind-review-<cksum of $(CURDIR)>.diff`. It needs no
   git call. An explicit value wins.
6. **`evals/adversarial/**` is on the control surface**: in `CLAUDE.md`, `tools/ship.sh` and the advisor proposal.
   It is not in `tools/refused-paths.sh`, which refuses a path outright rather than listing control surface
   (the user, Q1).
7. **CLAUDE.md's slice wording follows decision 0013:** full runs until the credit expires (~2026-11-24), then
   10–20-item slices during development.

## Residual

The `child_env` guard covers three keys. Other `CLAUDE_CODE_*` refusal switches (for example
`CLAUDE_CODE_REFUSAL_FALLBACK_CATCH_ALL`) were measured only alone, not combined with the flag. The proxy tripwire
is the backstop: any fallback beta still fails the test and never leaves.

## Why not the alternatives

- Adding `fallback-credit` to `ALLOWED_BETAS`, raising `MAX_REFUSALS`, or recording the runner's 400s would
  loosen the controls the incident showed working.
- Dropping `anthropic-beta` from the match key would hide a real change in model input.
