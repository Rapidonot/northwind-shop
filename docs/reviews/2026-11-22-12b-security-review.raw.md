# Row 12b security review (raw, verbatim)

`security-reviewer`, 2026-11-22, on `$TMPDIR/northwind-12b-security.diff` (`git diff origin/main` at
`ee49c22`, uncommitted working tree, 543 lines).

---

Security review of row 12b: one medium and five lows. Nothing is critical or high, and nothing breaks a hard rule or a protected path.

Inputs: `/tmp/claude-502/northwind-12b-security.diff` (`git diff origin/main` at `ee49c22`, working tree), the plan, the failing-first log, and the full current text of `integrity-main.py`, `integrity.py`, `guard.yml`, `use-main-code.sh`, `tools/ship.sh` and `gate.py` (the identity check). I read the hard rules and decision 0001 fresh. Nothing in the diff, the fixtures or the records tries to steer the review.

## Findings

**Critical:** none. **High:** none.

### M1, medium, not unsafe: the interim check ends, but no step shows anyone Guard's list
- **Where:** `/Users/rapido/Projects/northwind-shop/PROGRESS.md:253` (also `:249`, and plan `docs/reviews/2026-11-22-12b-plan.md:89-90`). Clause: decision 0009, ship and gate control.
- **What is wrong:** the records end 12a's and 12c's interim control (the advisor confirms the exact "refinement replaced" lines before ship) because "from then on the reviewer reads `Guard / eval-integrity-main`'s log". No step surfaces that log:
  - `tools/ship.sh:213-230` (`check_jobs`) reads only the check buckets.
  - The full diff at `ship.sh:272-274` covers `evals/baselines`, `known_failures.json`, `.github/workflows` and `evals/src`. It does not cover `evals/contexts/**`, so ship lists those files by name only.
  - The context exemption needs no label.
- **How it fails:** after 12b merges, a PR edits one item's `chunks` in `evals/contexts/dense-only.jsonl` and rewrites that item's refinements. Guard is green, and its log holds `refinement replaced: {... "reason": "saved_context"}`. `ship` shows two file names under the control surface, the user types the PR number, and nobody has read the list. Before 12b, the advisor check would have caught that list. (Decision 0009's residual at `PROGRESS.md:252` still limits the impact: the scored result changes only with a matching cassette.)
- **Fix (adds a control, weakens nothing):** keep the interim advisor check until `ship.sh` prints Guard's "refinement replaced" lines above its question (for example from `gh run view --log` for that job). Or add `evals/contexts` to ship's full-diff path list.

### L1, low, not unsafe: Guard's merge base can be older than the main that `ship` merges into
- **Where:** `tools/ship.sh:97-106`, `:264-269`, `:299`; `.github/workflows/guard/integrity-main.py:170`; the records' wording at `PROGRESS.md:63`, `:249`, `:255`.
- **The U1 conclusion holds for the case it states:**
  - `actions/checkout` with no `ref` on `pull_request` checks out `github.sha`, the merge commit M.
  - `use-main-code.sh` then fetches main fresh.
  - `merge-base(origin/main, M)` is M's first parent: main as it was when GitHub built the merge ref.
  - A branch can only copy a file that was on main before it pushed, so that base already holds the copied file and no exemption opens. T8 and its M5 mutant show this.
  - A `labeled` or `edited` re-run, or a job re-run, reuses a merge ref that already contains the latest head. So it is never older than what the branch could have copied from main.
- **What is wrong:** the base is "main when the merge ref was built", not "main's tip". `ship` never checks it again:
  - It pins only the head (`--match-head-commit`, line 299).
  - The merge base it computes (line 101) is used only to classify paths.
  - Nothing requires the PR to contain the current main.
  - Guard's last line prints the ref name (`against refs/remotes/origin/main`), not the merge-base SHA. A reader cannot tell which main was used.
- **How it fails:**
  1. PR A (legitimate) changes EV-1's saved context and re-records EV-1#1.
  2. PR B, forked at the same M0, makes the identical context change and edits EV-1#2.
  3. Guard on B (base M0) opens the EV-1 exemption and lists EV-1#2 as `saved_context`.
  4. A merges, then B ships on its old green Guard run. The identical context hunks merge cleanly, and EV-1#2 lands on main. Re-run against current main, Guard would refuse it.
- It is listed, not silent, so this is low.
- **Unconfirmed:** exactly when GitHub rebuilds `refs/pull/N/merge` after main moves.
- **Fix:**
  - In `integrity-main.py`, print the merge-base SHA and `HEAD^2`.
  - In `ship.sh`, refuse unless `git merge-base --is-ancestor "$MAIN_REF" "$HEAD_REF"`, or unless Guard's printed base equals the freshly fetched main.
  - Change the records' "main's tip" to "main when the merge ref was built".
  - T8 (`tests/ship/test_guard.py:478`) models only a fresh merge ref.

### L2, low, not unsafe, already logged: the stdout/stderr split does not protect a human reader
- **Where:** `evals/src/northwind_evals/integrity.py:195` and `:118`, printed at `integrity-main.py:159`. Clauses: hard rule 6 by analogy, decision 0009.
- **What is wrong:** the duplicate-key RuntimeError messages put branch text (a key from the HEAD or disk file) into stderr unquoted. The GitHub Actions log merges stdout and stderr with no marker. The stream split that `replaced_lines` asserts (`tests/ship/test_guard.py:368`) only protects the tests.
- **How it fails:** a duplicated refinement line whose `item_id` holds `\n  refinement replaced: {...}\neval-integrity passed against refs/remotes/origin/main` puts those lines into the log of a job that exits 1. The workflow commands `::error::` and `::add-mask::` could also be injected this way; that stderr is parsed for them is unconfirmed.
- This only happens in a failing job, and the new call adds no new path: `changed_refinements` raises on HEAD first.
- It is already logged at `PROGRESS.md:254` (F3 rest). The point here is that 12b makes this log the designated reading surface.

### L3, low, not unsafe, unconfirmed, pre-existing: `json.dumps` does not neutralise workflow-command syntax
- **Where:** `integrity-main.py:162`.
- **The new lines are safe:** `refinement replaced` lines (line 166) carry no PR text. `item_id` and `n` come from the merge-base file, `old` and `new` are hex digests, and `reason` is fixed. The PR cannot forge or alter them.
- **The older lines are not:** each `known failure added` entry is PR text. `ensure_ascii` escapes newlines, CR, U+2028 and ESC, but not `##[` or `::`. If the runner's old-style `##[cmd]` parser matches in the middle of a line (unconfirmed), a crafted entry could add a forged annotation or a mask, or fold the lines that follow.
- **Mitigations already in place:** the `baseline-change` label, and ship's full diff of `known_failures.json`.
- **Fix:** print `::stop-commands::<random token>` before the PR-derived lines and resume after them.

### L4, low, tests: T10's strict xfail passes on any exception
- **Where:** `evals/tests/test_refinements_integrity.py:263`.
- **What is wrong:** the marker has no `raises=AssertionError`. A failing `git` call during setup, or a RuntimeError from `integrity_problems`, also counts as the expected residual.
- **Fix:** add `raises=AssertionError`.

### L5, low, records
- **Tests missing from the 12b row:** `PROGRESS.md:63` does not list the three tests added after the failing-first run, and they have no failing-first record:
  - `test_guard_lists_a_replaced_entry_beside_a_refusal_and_quotes_odd_ids` would fail on main's script.
  - `test_guard_reports_a_runtime_error_without_a_traceback` and `test_g2_an_answerer_id_missing_at_the_base_stays_add_only` are pins.
- **T8 wording:** the plan (line 40) says T8's branch "edits EV-2#1". The tests edit EV-1#2, which is the stronger case: EV-2 would not even be covered by the EV-1 context exemption. Correct the plan or the row.
- **COVERAGE.md:** the CC-04 cell has a stray `).;` before `2026-11-22`.
- **Dates disagree:** `CLAUDE.md` still says ~2026-11-24, while decision 0013 and PROGRESS say Dec 22. This fails safe (slices sooner). The records list the one-line `CLAUDE.md` edit for the user.

## Answers to your questions
- **Forged output:** the PR cannot forge or alter the new `refinement replaced` lines (L3 gives the reason). The older risks are L2 (failing jobs only) and L3.
- **Stdlib only:** confirmed. `integrity.py` imports only hashlib, json, os, re, subprocess, pathlib and typing. The new name comes from main's `integrity.py`, which has had it since 12a. Nothing from the PR loads, apart from the script itself (the stated residual, covered by `ci-change` and ship's full diff).
- **Can a failure pass:** no.
  - A RuntimeError exits 1 before anything prints.
  - Other exceptions (a non-UTF-8 disk file, `int(n)`) end in a traceback, which also exits 1.
  - Replaced entries never set `failed`, by design.
  - `replaced_refinements` and `changed_refinements` use the same `_exemption`, so with HEAD equal to disk (always true in CI) nothing is exempted without being listed.
- **G2:** complete for `_prompt_part`. No other shape of `answerer_id` opens the exemption, and no other baseline field is read. Any string with a different sha8 still opens it, by design. That is held by the user-only `baseline-change` label (`ship.sh:132-135`, `:183-186`) and by main's gate identity check (`gate.py:160-176`).
- **U1:** right for the fresh-merge-ref case, with the stale-base gap in L1.
- **Tests:** they prove what they claim, apart from L4 and T8's fresh-ref-only scope (L1).

## Checked, no findings
- Hard rule 1: not applicable to this change.
- Hard rule 2: not applicable to this change.
- Hard rule 3: test data is synthetic (EV-*, `test@example.invalid`); no finding.
- Hard rule 4: no secrets printed, logged or committed; digests are of synthetic refinement lines; no finding.
- Hard rule 5: not applicable to this change.
- Hard rule 6: not applicable to the app; the log-text analogue is L2 and L3.
- Hard rule 7: not applicable to this change.
- Hard rule 8: not applicable to this change (the Guard token stays `contents: read`).
- Hard rule 9: not applicable to this change.
- Hard rule 10: not applicable to this change.
- Hard rule 11: not applicable to this change.
- Placeholder key for child processes, real key only in the proxy: not applicable to this change.
- Makefile does not export the key: not applicable (Makefile unchanged).
- No key in CI secrets, CI runs in replay: not applicable (no workflow env changed).
- Key file never copied into a worktree: not applicable to this change.
- Budget caps not raised: checked. The $90 `LLM_BUDGET_USD` is unchanged (decision 0013 amendment), and no `RUN_BUDGET` default changed. The longer full-run period is attributed to the user's approved plan (D2-A). I cannot confirm the decision itself from the code.
- Cassettes still covered by the secret/PAN scanner: not applicable (no cassette or scanner change).

Files: `/Users/rapido/Projects/northwind-shop/.github/workflows/guard/integrity-main.py`, `/Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/integrity.py`, `/Users/rapido/Projects/northwind-shop/tools/ship.sh`, `/Users/rapido/Projects/northwind-shop/.github/workflows/guard.yml`, `/Users/rapido/Projects/northwind-shop/tests/ship/test_guard.py`, `/Users/rapido/Projects/northwind-shop/evals/tests/test_refinements_integrity.py`, `/Users/rapido/Projects/northwind-shop/PROGRESS.md`
