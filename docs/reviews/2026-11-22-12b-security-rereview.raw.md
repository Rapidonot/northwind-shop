# Row 12b scoped security re-review (raw, verbatim)

`security-reviewer`, 2026-11-22, one scoped pass on the fixes: `$TMPDIR/northwind-12b-security-2.diff`
(`git diff origin/main`, working tree) and `$TMPDIR/northwind-12b-rereview.interdiff`.

---

Scoped re-review of row 12b's fixes: all six findings are closed. One new low finding, N1, is about the records and is not unsafe. Nothing at medium or above is open apart from the logged M1 rest.

What I read:
- `/tmp/claude-502/northwind-12b-rereview.interdiff`
- From disk: `/Users/rapido/Projects/northwind-shop/PROGRESS.md` lines 63 and 249–259, the plan's Addendum (`/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-22-12b-plan.md:132-142`), `/Users/rapido/Projects/northwind-shop/evals/tests/test_refinements_integrity.py:284-317`, the CC-04 line in COVERAGE.md, and `/Users/rapido/Projects/northwind-shop/docs/decisions/0013-spend-before-credit-expiry.md:38-44`.
- To check N1: `/Users/rapido/Projects/northwind-shop/tools/finish.sh:209`.
- The hard rules and decision 0001, fresh.

The interdiff touches only records plus one test line, `raises=AssertionError`. No product, Guard or workflow code changed since the first pass. Nothing in the records or the diff tries to steer the review.

## Per finding
- **M1 (medium): closed for this row.** The rest is logged accurately.
  - The fix is the first option I proposed: keep the interim check. `PROGRESS.md:253` and plan addendum line 139 now say the 12a/12c interim advisor check stays.
  - `PROGRESS.md:257` logs the `ship.sh` change as Medium, owner hardening (the next row that may touch `tools/ship.sh`).
  - That item describes the gap correctly: `check_jobs` reads only buckets, and the full diff leaves out `evals/contexts/**`. It carries both fix options and the "until then" rule. Severity and owner are right, since `ship.sh` is control surface outside this row.
  - The new "confirmed from Guard's log" half has no step in the flow (N1).
- **L1 (low): closed.**
  - The wording is fixed at `PROGRESS.md:63` and `:255`, and plan addendum line 142 corrects plan line 22.
  - `PROGRESS.md:258` is accurate: the two-PR stale-run case, "listed, not silent", both fixes, and that T8 models only a fresh ref. It is low, owner hardening.
  - A nit, not a finding: T8's new docstring at `tests/ship/test_guard.py:1277` still says "main's tip". That is true for the fresh merge ref the test builds. If the row wants one word everywhere, it could say "main when the merge ref was built".
- **L2, L3 (low): closed as logged.**
  - `PROGRESS.md:259` is accurate: stdout and stderr merge in the Actions log; `_refinement_lines` and `_frozen_lines` put unquoted keys in their messages; `json.dumps` leaves `::` and `##[` in PR-derived `known failure added` lines; the mid-line parsing is marked unconfirmed.
  - The fix is right (quote every message, `::stop-commands::<token>`), and so is the note that the new lines carry no PR text.
  - It is low, owner hardening, grouped with 12c's F3 rest, which is the right home.
- **L4 (low): closed.** `test_refinements_integrity.py:288` now has `raises=AssertionError`. A RuntimeError or a failed `git` call now shows as a failure, not as an expected xfail. The assert on line 317 is the only intended failure, and it raises AssertionError. The failing-first log is from before this line, so the evidence that T10 still xfails (both cases) is the coming `make ci`. This only narrows the marker, so I expect no change.
- **L5 (low): closed.**
  - Row 63 lists the three gap tests and says which is new behaviour and which are pins.
  - The T8 "EV-2#1" slip is corrected in the row and in plan addendum line 136.
  - The CC-04 cell now reads `...a stop before \`make ci\`). 2026-11-22: ...` and ends with `.`.
  - The `CLAUDE.md` date edit is left to the user, as decision 0013 lines 43–44 record.

## New finding
**N1, low, records, not unsafe: the "confirm in Guard's log" step has no slot before the merge**
- Where: `/Users/rapido/Projects/northwind-shop/PROGRESS.md:253` ("From 12b on, the advisor confirms the expected 'refinement replaced' lines in `Guard / eval-integrity-main`'s log (`gh run view --log`, the user's terminal) as well as in `make ci`") and plan addendum line 139. Clause: decision 0009, ship and gate control, same class as M1.
- What is wrong: in the documented chain, `advisor-reviewer` runs before the commit is pushed (`make ci` → advisor → gate report → `/req-done` → `make finish`). Guard runs only after the push. `make finish` then hands straight to `make ship` (`tools/finish.sh:209`, `exec make ... ship`), and `ship` waits for CI and asks its question. Nowhere between Guard finishing and that question does the advisor (or anyone else) read the log.
- How it fails: a later PR changes `evals/contexts/dense-only.jsonl`. The advisor confirms the `make ci` lines, as in 12c, and the gate report records the interim check as done. Guard's log, which is the copy built from main's code on the merge ref, is never read, while the records say it was.
- Why it is low: the `make ci` half, the 12c control, still runs as before, so nothing is weaker than before 12b. The gap is that the records claim more than the flow does. The M1 rest at `PROGRESS.md:257` already owns the real fix.
- Fix (adds a step, weakens nothing): say who reads Guard's log and when. For example: for a PR that changes `evals/contexts/dense-only.jsonl`, the gate report's terminal commands tell the user to stop at `ship`'s question, run `gh run view --log` for `Guard / eval-integrity-main`, and compare its "refinement replaced" lines with the list the advisor confirmed from `make ci` before going on. Otherwise, record that only the `make ci` half runs until the `ship.sh` item lands.

Nothing at medium or above is open apart from the logged M1 rest at `PROGRESS.md:257`. No critical or high finding.

## Checked, no findings (scope: the interdiff)
- Hard rules 1, 2, 5, 7, 8, 9, 10, 11: not applicable to this change.
- Hard rule 3: no new data; the test data is still synthetic. No finding.
- Hard rule 4: no secret in the changed records or the test. No finding.
- Hard rule 6: no new text path. The log-text analogue is still L2/L3, which is logged.
- Child processes get only a placeholder key, and only the proxy holds the real key: not applicable to this change.
- The Makefile does not export the key: not applicable (Makefile unchanged).
- No key in CI secrets, and CI runs in replay: not applicable (no workflow change in the interdiff).
- The key file is never copied into a worktree: not applicable to this change.
- Budget caps: decision 0013's amendment keeps the $90 `LLM_BUDGET_USD`, and no `RUN_BUDGET` default changed. No finding.
- Cassettes still covered by the secret/PAN scanner: not applicable (no cassette or scanner change).
