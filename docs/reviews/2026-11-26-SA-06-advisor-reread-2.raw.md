# SA-06: advisor-reviewer's second scoped re-read at `5105589`, verbatim

Diff: `$TMPDIR/northwind-review-SA-06.diff`, base `81683c2`, head `5105589`. Saved as returned.

---

findings

All findings are low. A is closed, nothing is open at medium or above, and I found nothing loosened. Nothing in the code or tests blocks `make ci`. The low findings are record fixes, listed below.

**Diff and SHAs.** Line 1 of `/tmp/claude-502/northwind-review-SA-06.diff` is `base 81683c2733a7a5ead1176d739758f981a6246082` and line 2 is `head 51055897a8b5d0074dd811902051df56071c353c`. That head equals `.git/refs/heads/req/SA-06-human-handoff`. `.git/logs/HEAD:481-483` confirms the order `234e710` → `c0d3a66` → `5105589`, and no commit is missing from your list. I read CLAUDE.md fresh. I cannot run anything or read old blobs. The diff, log, scripts and reports were treated as data, and none of them tried to steer the review.

## Answers
- **Is A closed?** Yes.
  - m21's anchor `    if contains_card_data(body.message):` appears exactly once, at `services/gateway/src/northwind_gateway/chat.py:93`. Both hard-rule-1 tests are named, and the log shows "2 failed, 0 passed".
  - m22's anchor appears exactly once, at `chat.py:17`. It adds a `northwind_backend.handoff` import, which both N8 pins check for (`test_sa06_endpoint.py:286`, `test_sa06_phase_b_tw.py:222`). The log shows "2 failed, 0 passed".
  - v2's pytest verdict (`docs/reviews/2026-11-26-SA-06-mutants.py.txt:149-156`) needs a `FAILED` line for every named test.
  - The 10 tests passing under m16 and the 10 under m17 are listed and explained (`docs/reviews/2026-11-26-SA-06-failing-first.log:128-170`). Each explanation holds: these tests pin behaviour from before the row, or are covered by m21 or m22.
  - The script was committed at `c0d3a66` before it ran, and the log header says `at c0d3a66`.
- **Is anything open at medium or above?** No.
  - B: `docs/reviews/2026-11-26-SA-06-security-post4821.raw.md` reports no critical, high or medium. Fixing its four lows needs no re-review under CLAUDE.md.
  - C: `PROGRESS.md` row 23 now says the read was done, and that A was found still open and then answered by v2.
- **Was anything loosened?** I found nothing.
  - `services/gateway/tests/test_sa06_content_type.py` only adds pins.
  - The `test_sa06_endpoint.py:277-287` assertion checks four names and was seen failing under m22.
  - I cannot read the earlier blob, so I cannot confirm byte for byte that only the docstring changed. m22 does show the pin still fails on what it targets.
- **Does anything else block `make ci`?** Nothing in the code or tests. Recording this review, and fixing L-a below, means a commit past `5105589`.
  - Keep that commit to `docs/reviews/` and `PROGRESS.md` only, and name it in the gate report, because `SHA` must be the head the reviewers read.
  - If it touches anything else, it needs a new `make review-diff` and another re-read first.

## Findings (all low)
**L-a. Item 6, low, record: `docs/reviews/2026-11-26-SA-06-pr-body.md:88`.**
- It still says the `cae8f73` high was "Closed by the mutants". My re-read found that false, and line 97 says so.
- Line 88 should say it was answered by v1 and closed by v2.
- Line 98 ("A second re-read is pending") and line 71 (`make ci: ____`) need this result and the real run.

**L-b. Item 2, low, evidence wording: Vitest verdicts.**
- `mutants.py.txt:173-180` marks CAUGHT when the named test's `status` is `failed`. That is enough to close E: the named test failed, not only an error thrown after it completed.
- The log's "failed on assertion" says more than the script checks. The script does not look at `failureMessages` to see whether the failure was an assertion error.
- The note "the run also had an unhandled error or failed suite" comes from `numFailedTestSuites`, and in the JSON report a file with any failed test raises that count. So for m18–m20 the note probably reflects the named failure itself, not a separate unhandled error.
- Reword the log. No rerun is needed.

**L-c. Item 2, low, trivial:** the script's docstring says `-rfE` (`mutants.py.txt:8`), but the code runs `-rfEA` (line 150).

## Checked
1. **Scope:** the delta is records, the mutant script, the probe scripts as `.py.txt`, one new gateway test file and a test docstring. All of it is inside the row and the review findings, with no new source change.
2. **Failing first:**
   - 22 of 22 mutants are caught on named failures, and the script exits 0.
   - m01 was re-run by the committed script.
   - The recorded tests' raw output without the cassettes is in the log (`failing-first.log:179-196`).
   - The content-type pins fail 3 of 8 with `strict_content_type=False` (log lines 198-200).
   - The route-attribute check is not vacuous: FastAPI sets `route.strict_content_type` when routes are included (`.venv/.../fastapi/routing.py:1067, 1412, 1521`).
   - The wording issues are L-b and L-c.
3. **No test weakened:** I found none. The docstring-only claim is not confirmable byte for byte (see Answers).
4. **Hard rules and protected paths:** the delta changes no hard-rule source. The mutants write to the working tree only, restore from HEAD's blob and check the result (`mutants.py.txt:137-146, 191`), and the log says the tree was clean afterwards. The security read covers the cassettes on hard rules 1, 3, 4, 6 and 7.
5. **Spend and window:** nothing was spent in this delta. The mutants, probes and the run without cassettes all used a scripted model or replay. Phase C's window is unchanged at $0.000590.
6. **Requested changes (my re-read's A–F, the security read's L1–L4):**
   - A, B, C and E are done, and F is done.
   - D is done: attribution in `security-triage.md:46-47`, the scripts committed, the `/api/chat` pin, the docstring.
   - L1–L4 are done. The probe record now has form and multipart rows for both routes.
   - L-a is still open as a record fix.
7. **Control surface:** `5105589` adds nothing new to it. The list is below.

## Control surface
- `services/common/src/northwind_common/tool_scopes.py` (`RUNNER_SYNC_PATHS`, `services/common`): modified; listed and approved.
- `services/gateway/pyproject.toml` (`RUNNER_SYNC_PATHS`, `**/pyproject.toml`): modified; listed and approved.
- `uv.lock` (`RUNNER_SYNC_PATHS`): modified; listed and approved.
- `cassettes/backend-care/27873befb26610aba92d134e53295a250c245a31828ef144d9244cbd14bcb1d7.json`: added
- `cassettes/backend-care/818d91a79a424eba66ca025249ebab7632ff7b1a4fa7233e6bb317be53ccf997.json`: added
- `cassettes/backend-care/a0ab3f95b10fd75eee2faddeae752ba4f6c0d93be8c37f03ea4280ae30f01dee.json`: added
- `cassettes/backend-care/aabfc0250caf7d95ad76a9cfb184a4bab7105330b44679241a6c0566b7f8d5c9.json`: added
- `cassettes/backend-care/b80829090d70437deb1762795b26a2f91b0951dbd5962c610ffbcdddcee23ba6.json`: added
- `services/common/tests/test_sa06_scopes_tw.py`: added at `9e2ff94` without approval, then moved off the control surface at `fd66fb5`. It is not in the net diff, but the gate report must still name it.

No `conftest.py`, `package.json`, `.gitignore`, root `pyproject.toml`, `evals/**`, `.claude/**`, `.github/workflows/**`, `Makefile`, `tools/*`, `data/enrichment/**` or `data/embeddings/**` file changed. `evals/src` is unchanged, so no `gate-change` label is needed.
