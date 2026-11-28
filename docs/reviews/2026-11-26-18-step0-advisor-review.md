# Row #18 step 0: advisor-reviewer (verbatim)

Diff: `/tmp/claude-502/northwind-review-18.diff` from `make review-diff`, `base bcc135b115ecb33c162274dc4afee1d9e6423e92`,
`head 3666dd80dcc39a1beea40bd4747b761fdf4b8ffb`. The report follows unchanged; the triage is at the end of
`docs/reviews/2026-11-26-18-step0-triage.md`.

---

findings

I read CLAUDE.md again for this review. The SHA lines at the top of `/tmp/claude-502/northwind-review-18.diff` match the ones you gave: `base bcc135b115ecb33c162274dc4afee1d9e6423e92`, `head 3666dd80dcc39a1beea40bd4747b761fdf4b8ffb`. There are no critical or high findings. Nothing in the diff, log, plan or review files tries to steer the review.

## Findings (most severe first)

**F1. medium. Items 1 and 2. `services/rag/tests/test_rag07_tw.py:185-198` (diff 2492-2511) and `services/assistant/tests/test_output_check.py`, `_assert_linear` (diff 467-478).** (a) does not follow the approved plan or the user's words, and the evidence that it is load-proof does not test what was built.
- **Plan vs code:** the plan (line 15) says "median of 3 at n and 4n, ratio < 8", with a 60 s backstop. The router test takes one CPU-time sample per size and allows at most 6x, with a 30 s backstop. The output-check tests also take one sample per size. With linear growth at 4x, 6x leaves 1.5x of headroom on single samples. The user asked for "operation counts or a far larger margin, not tighter timing". The router test has no operation count at all.
- **Evidence:** the "slow clock" runs in the log patch `perf_counter`, and `process_time` ignores that patch. So they show only that the old tests depended on wall-clock time. Nothing shows the new CPU ratios hold up under real contention, such as window B's `make ci` on the same machine. The plan's "or a CPU hog" was not run.
- **No quadratic mutant for `route_indexes`:** the plan's "a quadratic mutant fails the new tests" was done for the output check (M-a1, M-a2) but not for the router.
- **Change:** put the deviation (6x, single sample) to the user, or build what was approved. Add a quadratic mutant of the router to the log. Do not change the thresholds silently in either direction.

**F2. medium. Items 1 and 6. `services/mcp-catalog/src/northwind_mcp_catalog/cart.py`, `alternatives` (diff 1263-1270), and `holds.py`, `Holds.live` (region totals only).** An approved plan item was narrowed by triage, not by the user.
- For (b), the plan says a `reservable` "that reads the catalog's per-node stock minus live holds". For (c) it says "Σ over the region's nodes of max(0, on_hand − live holds)".
- What was built is the region total less the region's holds. The session logged the gap as security L1 and gave it to row #21.
- Moving part of an approved step-0 item to another row is a scope question (stop 7) or a revised plan (advisor, then the user). It is not a low to log.
- PROGRESS row 18's "(b) the cart's alternatives are BE-04's substitutes" does not say this part is missing.
- **Change:** put it to the user, and record (b) and (c) as built on region totals in row 18.

**F3. medium. Item 7.** Three changed files are on the control surface, and no record says so:
- `services/rag/tests/test_rag07_tw.py`, because `services/rag` is in `RUNNER_SYNC_PATHS` (`Makefile:199`);
- `services/mcp-catalog/pyproject.toml`, through `':(glob)**/pyproject.toml'`;
- `uv.lock`.

The user approved the pyproject and `uv.lock`, and the diff confirms `uv.lock` gains only the workspace `northwind-backend` entry. The user also asked for the router test change. But neither PROGRESS nor the triage says that the router test is control surface and synced to the runner. `make ship` will ask for the PR number because of these files.
- **Change:** list all three in row 18 and later in the gate report, and tell the user.

**F4. medium. Item 2. `docs/reviews/2026-11-26-18-step0-failing-first.log`.** These fix and security tests have no recorded failing run:
- the import-closure allow-list and `FORBIDDEN` (L4; `test_18_step0_stock.py`, diff 1802-1847);
- L5's exit 3 for absent tables, and exit 2 on any `psycopg.Error` (`test_main_exits_3_not_0_when_the_tables_do_not_exist`, `test_main_exits_2_on_any_database_error`). Neither was run against the code before the fix;
- the tool-list sha256 pin (`test_the_tool_list_is_unchanged`, which backs "`add_to_cart`'s description unchanged");
- L6's `-P`, which no test checks.

Mutants logged: M-a1, M-a2, M-b1, M-b2, M-c1, M-c2, M-d1, M-g1, M-g2, M-m1, M-n1, M-n3.
- **Change:** run and log mutants for each one: a `psycopg` import in the backend import chain, exit 3 put back to 0, only `OperationalError` caught, a one-character change to the tool description.

**F5. low. Item 1, the user's condition (2).** The user allowed the dependency "only for the pure substitutes module". Importing it runs `northwind_backend.inventory.__init__`, which loads 13 backend modules: `InventoryFake`, `fakes._base`, `fakes.catalog`, `recovery`, `idempotency` and others. The first security review says `fakes._base` imports `northwind_common.cards` checks. The session decided on its own that a 13-module allow-list meets the condition. The allow-list excludes everything the user named, but the user has not been told about it and PROGRESS does not mention it.
- **Change:** tell the user. Alternatively, import the module so that the package `__init__` side effects don't run, if the user wants the condition read literally.

**F6. low. Records: `PROGRESS.md` row 18 (diff line 12) and `docs/reviews/2026-11-26-18-step0-triage.md`.**
- "(a) … counted reads and scans plus CPU-time ratios": the router test counts nothing.
- "(c) every catalog stock read subtracts live holds": the running server uses `NoHolds`, so today nothing is subtracted. The "waiting" sentence covers this only in part.
- The change to row #16's pre-existing expectation, `test_cart.py::test_an_out_of_stock_add_is_refused_with_alternatives_the_server_chose` (at most 5 becomes at most 3, and the ordering is replaced), is not named in PROGRESS or the triage. The triage names only the two changes inside this row's own new tests. It is not a weakening: the user asked for (b), and the new asserts add the price band and one variant per product. It still needs recording where the user reads.
- The triage's "On `northwind` today: exit 3" has no output line in any evidence file.

**F7. low. Item 6.** The user's settled points (1)-(3) are in neither the plan file nor the runbook:
- §2 and §4 of `docs/reviews/2026-11-26-18-runbook-draft.md` have no hold-guard step and no "guard line in the window record";
- the plan's Freeze list (plan lines 32-33) names `cart.py`, `tools.py` and `substitutes.py`, but not `holds.py`, `server.py` or `live.py`. The last three shape model input (stock figures, the `in_stock` filter) once a reader is wired.
- **Change:** update the plan and runbook before the freeze, and send the revised plan to `advisor-reviewer` as CLAUDE.md requires.

**F8. low. Point 3 (a forward risk).** `services/assistant/tests/test_cart_journey_e2e.py:125` asserts `offered` (alternatives exist) for `P0008-CHR-REL-M`. The samples in this row's tests (P0001, P0002, P0010, P0003) do not show that BE-04's rules (±20 % band, same subcategory, one variant per product) give that variant any alternative in the journey's region. If they give none, W7 hits a stop 7 at the recorded expectation.
- **Change:** check it at no cost before W7.
- The "1 stale call" figure comes from a grep, not from a replay run.

**F9. low. Hygiene.**
- Leftover bytecode from the temporary mutant file: `services/assistant/tests/__pycache__/test_zz_old_tmp.cpython-312-pytest-9.2.0.pyc` and `services/rag/tests/__pycache__/test_zz_old_tmp.cpython-312-pytest-9.2.0.pyc`. It is not tracked and not collected.
- `test_18_step0_tw.py` (diff 2437-2438) adds dead code, `_unused()`, only so an unused `Iterator` import passes lint. Removing the import is the fix.

## The answer to point 3 is right
- `make ci` does not run the stale `assistant-cart` call. `pyproject.toml:62` sets `-m "not integration and not e2e"`, `test-integration` runs `-m integration`, and `ci` (`Makefile:339`) has no `e2e`.
- GitHub's browser job runs `make e2e` (`.github/workflows/ci.yml:182`). That runs `pytest -m e2e`, which includes `test_cart_journey_e2e.py`, so "nothing is pushed before W7" holds.
- Exactly one cassette holds an `"alternatives"` block: `cassettes/assistant-cart/ed523317….json`. Each journey turn is a fresh conversation, so one stale call is consistent.

## The open stops are stated correctly
- Stop 5: `services/mcp-catalog/tests/test_tools.py:520-534` does forbid both the text `psycopg` and its import in mcp-catalog's source. `holds.py` avoids the word.
- Decision (1)'s Postgres wiring is not in this commit.
- Reading the guard's exit 3 as 0 is recorded as waiting on the user, with N2's warning.

## Unconfirmed
- There is no full-suite, `make lint` or mypy run at `3666dd8`. Tests ran one file at a time, and `make ci` has not run.
- The pin's comment says "sha256 at `bcc135b`". The diff supports that no tool spec changed, but nothing shows the hash computed at the base.
- The count "`test-writer` 56 tests" is not checked.
- Which paths window B owns: I was not given its scope.

## Checked
1. Scope: findings F1, F2, F5. Nothing beyond (a) to (d), the guard and decision (2)'s files. Window B's paths are unconfirmed.
2. Failing-first: shown for (a) M-a1 and M-a2, (b), (c), (d), the guard, M1 and N1/N3. Missing for the router's quadratic case, L4, L5, the tool pin and L6 (F1, F4).
3. No test weakened after a failure: none found. The timing changes and the `test_cart.py` change are what the user asked for, and the L5 change from exit 0 to exit 3 makes the test stricter. Recording gap in F6.
4. Hard rules and protected paths: no environment-file access, no card data. Stock and price stay live (hard rule 2). Envelopes are transient and retryable (hard rule 10). `services/backend/**` is changed, which is fine on a row that is not routine.
5. Spend and window: does not apply to step 0, which has no recording and no cassette in the diff. The plan states budgets and the $25 stop on real spend.
6. Revised plans contain every requested change: F2, F7.
7. Control surface: F3, and the list below.

## Control surface
- `services/mcp-catalog/pyproject.toml` (in `RUNNER_SYNC_PATHS` through `**/pyproject.toml`; approved by the user under decision 2)
- `uv.lock` (in `RUNNER_SYNC_PATHS`; approved under decision 2; only the workspace `northwind-backend` entry added)
- `services/rag/tests/test_rag07_tw.py` (`services/rag` is in `RUNNER_SYNC_PATHS`; the change was requested under (a) but is not flagged as control surface anywhere)

Files read:
- `/tmp/claude-502/northwind-review-18.diff`
- `/Users/rapido/.claude/plans/ethereal-wishing-sifakis-of-d4a7f7a2-d97f-448c-9d2a-6ee6ad5b9c7b.md`
- `/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-26-18-runbook-draft.md`
- `/Users/rapido/Projects/northwind-shop/Makefile`
- `/Users/rapido/Projects/northwind-shop/pyproject.toml`
- `/Users/rapido/Projects/northwind-shop/.github/workflows/ci.yml`
- `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/tests/test_tools.py`
- `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/tests/test_cart.py`
- `/Users/rapido/Projects/northwind-shop/services/assistant/tests/test_cart_journey_e2e.py`
- `/Users/rapido/Projects/northwind-shop/services/assistant/tests/test_rag10_rounds.py`
