# BE-07 advisor-reviewer scoped re-read at 6a12661 (verbatim)

pass

All four of my findings are closed at head `6a12661`. Three things are left for the user, and this pass does not decide them on the user's behalf.

**Diff check.** Lines 1–2 of /tmp/claude-502/northwind-review-BE-07.diff are `base 7296c04dfe7fb41b446bdc896982c8faa6c057ae` and `head 6a1266171c28af0c1e5a8f1ec40a1e539b98a769`, matching what you gave. A match catches accidents only. Compared with the `1be987a` review diff, the full diff has the same files plus `docs/reviews/2026-11-24-BE-07-advisor-review.raw.md`. Every source and test block is the same size, except `fakes/cases.py`, which grew by the 2 lines of the docstring change. That agrees with the 196-line delta. I re-read CLAUDE.md; "Running a row" and the control surface list are unchanged. I ran nothing.

## My findings, re-read
- **F-A (medium): closed.**
  - The log now records the session's own run: with the `holds_card_data` line removed, `test_n6_…` fails (1 failed, 98 passed), and `git diff` was empty after the line went back. So the N6 test you committed has now been seen failing.
  - The PR body names both tests that did not fail first.
  - The far-apart cross-field split is listed as a residual.
  - The `fakes/cases.py` docstring now matches what is checked. Its lines 5–6 are at most 100 characters, within ruff's `line-length = 100` by my count.
  - **Step 2 is still open:** accepting the narrowed N6 shape as a residual (stop 5) is the user's decision. It goes in the gate report, under open items, not under "Accepted by the user".
- **F-B (low): closed as a records item.** The PR body has a "Stricter than option (b) as worded" list with both fixes and F5's cost. The user's acceptance is still pending; put it in the gate report.
- **F-C (low): closed.** With the NFKC step in `check_summary` reverted, the two fullwidth money cases fail (2 failed, 71 passed). That confirms, from the session's own run, the part of `test-writer`'s report that covers this fix. The other 6 of its 8 reported failures are still second-hand, and the PR body describes the confirmation accurately.
- **F-D (low): closed for `1be987a`.** `ruff check`, `ruff format --check` and `mypy` were clean at that head. Lint was not re-run at `6a12661`, whose only code change is that docstring. `make ci` will cover it.
- **Index check note:** this goes to the user in your report, as you said.

The advisor raw file in the commit is my report, verbatim. The delta changes no logic, test, cassette or control-surface file.

## Checked
1. Scope: the delta is docs, the evidence log and one docstring, all in the row's own files.
2. Failing first: N6 is now shown failing without its fix, and the session confirmed `test-writer`'s NFKC money failures itself. The 4- and 2-digit split with no withheld turn between did not fail first because that case was already caught; this is disclosed.
3. Weakened tests: no test changed in the delta. The N6 narrowing is with the user as a stop-5 decision.
4. Hard rules and protected paths: no code behaviour changed. The docstring now understates nothing and overstates nothing.
5. Spend and window: unchanged since my last review. No new spend; the ledger figures in the PR body are the same.
6. Requested changes: all recorded. Waiting on the user: the F-B stricter fixes, the F-A residual, and the index check before `make ci` on `northwind_16`.
7. Control surface: nothing new in the delta. The list is unchanged: the 18 new files in `cassettes/backend-care/` added by `make record SUITE=backend-care` in the user's window, and, flagged though not on the defined list, `services/backend/tests/backend_audit/test_be08_views.py`, the view-constant pin the user approved under stop 5. The delta did not change it.

`make ci` should run at `6a12661`, the head the reviewers read.
