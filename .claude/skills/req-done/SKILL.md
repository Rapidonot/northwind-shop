---
name: req-done
description: Close out the current requirement. Runs CI, maps each criterion to a tagged test, and updates PROGRESS.md and COVERAGE.md with evidence.
argument-hint: "[REQ-ID]"
disable-model-invocation: true
---

Close out `$ARGUMENTS`. If no ID was given, take it from the branch name (`req/<ID>-<slug>`). Do
not commit, push or merge; the user reviews and merges.

1. **Run CI.** Run `make up`, then `make ci`. If it fails, show the output and stop. Change nothing.
2. **Map criteria to tests.** Read the ID's criteria from `docs/PRD.md` and its `PROGRESS.md` row.
   For each criterion, find a test tagged with the ID (`@pytest.mark.req("<ID>")` or
   `describe("[<ID>] …")`) that passed in step 1. A skipped or xfail test does not count. Show the
   table.
3. **Run the eval gate.** If `make eval-gate` exists, run it, and treat a regression as a failure
   (stop as in step 1). If it does not exist yet, say so; the requirement can still close, and the
   evidence must record that it was absent.
4. **Decide the status.** `done` only when every in-scope criterion has a passing tagged test, CI
   passes and the eval gate did not regress. Any gap means `partial`, with what is left written
   down. Never `done` with a gap.
5. **Update the docs.** In `PROGRESS.md`, set the row's status and evidence: date, test counts and
   what was checked. Add a line to the Log. In `COVERAGE.md`, update any CC-xx feature this work
   exercised, with evidence.
6. **Report** the status, any gaps, and what the user should review before merging.
