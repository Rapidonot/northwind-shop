# Row #18 step 0: advisor-reviewer, final read before `make ci` (verbatim)

Diff: `/tmp/claude-502/northwind-review-18.diff`, `base bcc135b115ecb33c162274dc4afee1d9e6423e92`,
`head b86ead827cf242080799e845c65867b337fbd404`. The first attempt stopped on a usage limit and was resumed; the
report follows unchanged. The triage is at the end of `docs/reviews/2026-11-26-18-step0-triage.md`.

---

findings

I read CLAUDE.md again for this review. The first two lines of /tmp/claude-502/northwind-review-18.diff match the SHAs you gave (`base bcc135b115ecb33c162274dc4afee1d9e6423e92`, `head b86ead827cf242080799e845c65867b337fbd404`). There is no critical finding. There is one high, and it should go to the user before `make ci`. Nothing in the diff, the log, the plan or the review files tries to steer the review.

## Findings (most severe first)

**1. High. Items 6 and 1: the user's option-1 condition is met as the user worded it, and the records say it is not.**
- The user asked: "check whether any cart scenario that RECORD-ALL will record offers an alternative whose units are split across a region's two nodes. If any does … stop and tell me."
- All three alternatives the recorded journey offers for `P0008-CHR-REL-M` in US (log, diff 563-564) have their US units split across DC-EAST and DC-WEST:
  - `P0001-BLK-REL-M`: 4 and 47 (`data/fixtures/stock.jsonl:37,40`)
  - `P0003-SLT-REL-M`: 54 and 72 (`:137,140`)
  - `P0004-BLK-REL-M`: 30 and 60 (`:167,170`)
- The session read "split" as "split so that no single node can fill the request". At quantity 1, with at least 4 units at each node, that cannot happen, so the risk behind the condition does not arise. But that reading is the session's, not the user's.
- `docs/reviews/2026-11-26-18-plan-revision-1.md:92-93` (diff 124-125) says "No recorded scenario can offer a split alternative". Taken as the user worded it, that is false.
- "Every recorded cart request is quantity 1" (the same place, and the PROGRESS Open item "From #18 step 0, L1", diff line 22) is also wrong as written. The journey's third turn sets quantity 2 (`services/assistant/tests/test_cart_journey_e2e.py:131-132`). The claim that can be defended is that every recorded out-of-stock refusal is at quantity 1.
- The log has no command or output for the condition check, for either claim.
- The session did stop on the second clause (#21 would change the alternatives code), and the user then chose (a). I cannot see the stop message, so I cannot confirm the user was told that the condition, as worded, was met.
- **Change:**
  - Before `make ci`, tell the user the facts above: the split is real, and why it does not matter at quantity 1. The user then confirms (a) or picks per-node now.
  - Correct the revision and the L1 open item to say "every recorded out-of-stock refusal is at quantity 1", and state the session's reading of "split".
  - Log the check's command and output: every recorded refusal and its quantity, across every suite RECORD-ALL records.

**2. Medium. Items 3, 4 and 2: a second security test was narrowed for option B, and the user's approval names only `test_tools.py`.**
- `services/mcp-catalog/tests/test_18_step0_stock.py::test_the_catalog_servers_import_closure_has_no_model_code` (diff 3202-3209) now checks the whole server against `MODEL_CODE` only. `psycopg` is checked only in the new `test_the_substitutes_dependency_alone_is_pure` (diff 3212-3218).
- That reverses part of security L4's fix, which put `psycopg` in `FORBIDDEN` for the server's import closure. It is a stop-5 change made because option B would have failed the test.
- Most of the protection is still there: the `test_tools.py` text check, the 13-module allow-list, and the substitutes-only check. Option B's security read saw the split and raised nothing (diff 1200). But:
  - the user's approval, as you quote it, covers `test_tools.py` only;
  - the triage L4 row (diff 923) still says the server test forbids `psycopg`;
  - M-l4 (diff 505-507) was logged against the old test and not rerun. `test_the_catalog_servers_import_closure_has_no_model_code` would no longer fail on it.
  - The docstring's "`psycopg` loads here only through `holds.py`" is not asserted anywhere.
- **Change:**
  - Put this narrowing to the user as part of the stop-5 approval.
  - Record it in the triage and in PROGRESS row 18.
  - Rerun M-l4 against the current tests and log which test kills it.
  - Do not loosen anything further.

**3. Medium. Records. `PROGRESS.md` row 18 (diff line 12), item (c): "The running server's port is `NoHolds`, so today nothing is subtracted."**
- This is wrong since `b188b05`. `app.holds()` wires `PostgresHolds` whenever `DATABASE_URL` is set (diff 1936-1946).
- Row #25 item (1) in the same file says so (diff line 21). The two records now contradict each other on something that shapes recorded stock figures.
- **Change:** say that stock reads subtract BE-04's live and sold units when `DATABASE_URL` is set, and that the default is `NoHolds` only when it is unset.

**4. Low. Records. `PROGRESS.md` row 18 (diff line 12), "Waiting on the user: the schema applied (the user confirms), then the guard's real 0".**
- This is out of date. The log has the real 0: "live holds: 0 in database 'northwind'", exit 0, 2026-11-27T08:28:59Z (diff 633-635).
- **Change:** record the real 0 with its time. What is left waiting is the user's go for `make ci`.

**5. Low. Records. The Open item "From #18 step 0, re-review N2" (diff line 23).**
- Its owner is still conditional ("#18 itself if the user picks option A or B"). The user picked B.
- **Change:** name #18 as the owner of running the guard before each window, and keep the runner wiring as control surface. Also show that the guard's real 0 was taken on the same `DATABASE_URL` that the recording mcp-catalog will read. N2 itself requires that, and the log line names only `northwind`.

## Unconfirmed
- The text of the session's stop message on the option-1 condition (finding 1).
- `make ci`, lint, mypy and the full suite at `b86ead8`: not run yet. Tests ran one file at a time.
- The user's requests beyond the parts you quote.
- Window B's paths. I was not given its scope.
- The `test-writer` count (56).

## Checked
1. Scope: within step 0 (a)-(d), the guard, option B and the dependency. (b) and (c) on region totals were accepted by the user. Finding 1 is about how the condition check was reported. Window B's paths are unconfirmed.
2. Failing-first: logged for (a) to (d), the guard, M1, N1, N3, F4, M-p1..M-p8 and M-q1..M-q5 (M-q2 by a watchdog). M-l4 is out of date after the test split (finding 2).
3. No test weakened after a failure: the `test_tools.py` narrowing is no wider than approved. `psycopg` is exempt in `holds.py` only. `rag_chunks` and `northwind_rag` are still banned in every file, `holds.py` included. A stricter inventory-only table and write-verb test was added. The import-closure narrowing is outside the approval as you quote it (finding 2). The `test_cart.py` expectation change is recorded and is not a weakening.
4. Hard rules and protected paths: price stays live, the envelopes are transient and retryable, no secret is printed, and no env files or card data are touched. `services/backend/**`, `services/assistant/**` and `services/rag/**` are changed, which is allowed on a row that is not routine.
5. Spend and window: step 0 records nothing and adds no cassette. The runbook has the guard before each window, "exit 3 always blocks", the notice to the user, the $28.75 in budgets and the $25 real-spend stop.
6. Revised plans contain every requested change: answers 1B, 2 (a), 3, 4 and 5 are all in the revision and in PROGRESS. Finding 1 (the condition) and finding 3 (an out-of-date record) remain.
7. Control surface: the three files below. All three are listed in PROGRESS row 18. No other changed file is on the control surface (no `conftest.py`, workflows, Makefile, `evals/**` or cassettes).

## Control surface
- `services/mcp-catalog/pyproject.toml`: in `RUNNER_SYNC_PATHS` through `**/pyproject.toml` (`Makefile:199`). Approved by the user.
- `uv.lock`: in `RUNNER_SYNC_PATHS`. It adds only the workspace `northwind-backend` entry.
- `services/rag/tests/test_rag07_tw.py`: `services/rag` is in `RUNNER_SYNC_PATHS`. Requested under (a); it now uses the median of 3 with 6x and 30 s, as the user answered.

`make ship` will ask for the PR number.

Files read:
- /Users/rapido/Projects/northwind-shop/CLAUDE.md
- /tmp/claude-502/northwind-review-18.diff
- /Users/rapido/.claude/plans/ethereal-wishing-sifakis-of-d4a7f7a2-d97f-448c-9d2a-6ee6ad5b9c7b.md
- /Users/rapido/Projects/northwind-shop/Makefile (`RUNNER_SYNC_PATHS`)
- /Users/rapido/Projects/northwind-shop/data/fixtures/stock.jsonl (the four variants)
- /Users/rapido/Projects/northwind-shop/services/assistant/tests/test_cart_journey_e2e.py
- /Users/rapido/Projects/northwind-shop/evals/tests/test_chk01_eval_cart.py (searched)
- /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-2[67]-18-* (searched)
