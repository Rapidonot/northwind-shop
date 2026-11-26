# BE-04 (row #25) advisor-reviewer, scoped re-read at c11cb59, verbatim

Verdict: **unconfirmed.** c11cb59 closes the high and both mediums; two lows remain (finding 4, and one new one). Still unseen: the Postgres race and contract runs with `make ci`, and two inputs this request didn't pass (the plan and the user's list of requested changes). So this is not a pass. The SHA lines match (base b5e2d508e9855721a234b2d2cd2d308727e3d784, head c11cb59f623a7a9278fcb5dfbf8c43606de1a200).

**Status of the 9a604d5 findings:**
1. High, item 2 (failing first for M2, L1, L3, L4, L6): **closed.** The mutants script has one mutant per fix, each run against a named test; a pattern that doesn't match exactly once prints `NOT APPLIED`, and the file is restored in a `finally`. m01 and m02 cover the two M2 tests, m03 L1's hard-rule-9 test, m04 and m05 the L6 limits, m06 `idempotency_conflict` (`-k conflict`, 4 failed), and m07 the "Nothing was changed" wording, against the new `test_be04_contract.py:649`, which pins L4. All 8 are logged `CAUGHT`; see N-1 for the log.
2. Medium, item 7 (pyproject.toml and uv.lock): **closed as to flagging.** The PR body names both, and its 96-packages claim matches the `uv.lock` hunk (dependency and `requires-dist` lines only). Still open as process: stop 8 needs the user's explicit answer, and PROGRESS.md row #25 doesn't mention either file.
3. Medium, item 6 (every other order byte-identical): **closed.** `test_be04_data.py` builds with and without `_fit_to_stock`, requires exactly `["O000476", "O000936"]` to differ, `tracking.jsonl`, `replay_labels.jsonl` and `shoppers.jsonl` to be equal, and the committed orders to equal the fitted build. It is a fair stand-in for version 2, since generate.py's only changes are `_fit_to_stock`, its call in `build` and the version constant. The docstring points to it, and m08 shows it catches a fit that touches other orders. Not confirmed against the user's own wording (not passed).
4. Low, item 6 (commit before capture): **open, waiting on the user.** Disclosed in the PR body.
5. Low, item 4 (stale comments): **closed.** The "awaiting" wording in the log and the plan is the record from that time, not a finding.

The PR body now says the $0 pin compares two stock levels on the new code, and that `invalid_line` is a loose fit for a key conflict.

**New in c11cb59:**
- **N-1, low, item 2:** the logged mutant output wasn't produced by one run of the committed script. m08's line comes before m07's, while the script runs them in order and prints one line each, so the output was reordered or assembled from separate runs, or the script differed when it ran. The heading says "at 9a604d5 plus this commit's tests", not the committed head. The mutated source lines look the same at the head, so the results are probably real but not reproducible as committed. Fix: rerun the committed script once at c11cb59 and paste its output unedited.
- Nothing else new; the new L4 test only adds checks.

**Unconfirmed:** the 7 race tests (the read-then-write sensitivity check included), the 58 Postgres contract tests and `make ci` haven't run, so the acceptance criterion's failing-first evidence and every Postgres claim (M1, L2, L5, U1, and N1 on Postgres) rest on reading only. Items 1 and 6 weren't re-checked against the plan and the user's list, which weren't passed. The mutant results, the 0-miss replays and the 5,989-hash identity are in logs, not seen. Window B's paths still weren't found; this row edits files in `services/backend/**`.

**Checked:** c11cb59 adds only tests, docs, comments and a docstring; no tests weakened; no new hard-rule or protected path touched (`services/backend/**` still not routine); $0, nothing recorded, no `cassettes/` file in the diff.

**Control surface:** `services/backend/pyproject.toml` (in `RUNNER_SYNC_PATHS` via `**/pyproject.toml`) adds `psycopg[binary]>=3.3.7`; `uv.lock` (in `RUNNER_SYNC_PATHS`) has the backend's dependency and specifier lines only. Both are flagged in the PR body and still need the user's answer under stop 8.
