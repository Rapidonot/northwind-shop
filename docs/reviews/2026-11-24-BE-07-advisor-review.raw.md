# BE-07 advisor-reviewer row review (verbatim)

`advisor-reviewer` on `make review-diff` (`base 7296c04…`, `head 1be987a…`, 7,645 lines), 2026-11-24.

---

findings

The SHA lines match. Line 1 of /tmp/claude-502/northwind-review-BE-07.diff is `base 7296c04dfe7fb41b446bdc896982c8faa6c057ae` and line 2 is `head 1be987a0d66061cc67f0f55a9828d865eeb4c8a4`, as you gave them. That catches accidents only. I read CLAUDE.md fresh, all of the diff's source and tests (7,645 lines), the plan, the failing-first log, the PR body, the recording log and the three raw security reviews. I ran nothing; I have no Bash, so I could not check git history between commits.

## Findings (most severe first)

**F-A. Medium (items 2 and 3).** The N6 test was rewritten after its first failing run, and the version you committed was never seen failing.
- Where: `services/backend/tests/care/test_be07_units.py::test_n6_the_queue_refuses_card_data_split_across_fields`. The fix it covers is in `services/backend/src/northwind_backend/fakes/cases.py`, `_holds_card_data` (the `or holds_card_data(asdict(case))` line).
- What the log says (line 57): the first version, with fragments far apart, failed, and was then "rewritten to adjacent fields before the fix, and the rewritten version was not run before the fix". So the test was changed to the shape the planned fix can catch (the `MAX_GROUP_GAP` join), and no recorded run shows the committed version failing.
- Reading `cards.py`, I expect it would fail without the fix: each 8-digit field alone matches nothing, and the joined text catches the card. That is my reading, not a run.
- What the rewrite left out (a card split across case fields that are far apart) is not listed as a residual anywhere. The PR body's open lows don't mention it. The `fakes/cases.py` docstring still says "Card data anywhere in a case is refused", which overstates what is checked.
- The PR body says "One test did not fail first". The log shows two: `test_b_a_card_split_…` and this one.
- To close it, before `make ci`:
  1. Remove the N6 line in the working tree, run only `test_be07_units.py` serially, put the line back, and add the result to the failing-first log. The head does not move.
  2. Give the user the far-apart shape to accept as a residual. Narrowing a test after it failed is theirs to decide (stop 5).
  3. Correct the PR body count and the docstring.

  Steps 2 and 3 mean a docs commit, so the advisor re-reads it scoped before `make ci`.

**F-B. Low (item 6).** Two of the fixes go further than the user's option (b) wording, and the user has not accepted them yet.
- F2's fix keeps only the task's own order ID. The user said "keeping order IDs (O\d{6})".
- F1's fix counts digits joined by one non-alphanumeric character as one number, so "1/2", "3-4" and "10.5" are masked too.
- Both are stricter, safer, recorded in the plan, and described in the PR body's behaviour list. But neither is marked there as a change from what the user decided. Name both in the gate report for the user's acceptance, with F5's cost: summaries can no longer name another order or a line.

**F-C. Low (item 2, evidence).** The test-writer's "8 failed, 67 passed" comes second-hand.
- The log quotes "as the test-writer agent reported its run", and its report was not saved. The session itself saw only the 75 passing after the fixes.
- Say so in the gate report, or confirm by reverting a fix (the NFKC money fix, say) and running `test_be07_writer.py`.

**F-D. Low, unconfirmed (anything that could block `make ci`).** Nothing shows ruff, `ruff format --check` or strict mypy ran at this head.
- `make check` was ruled out by the coordination, and the log shows only serial pytest runs.
- If `make ci` fails on lint, the fix is a new commit and another advisor pass.
- Suggest asking the user whether `make lint` may run now. It uses no xdist.

**Note (not a finding).** The plan's original read-only index check before `make ci` dropped out when change 4 moved the run to `northwind_16`. PROGRESS "Open items" (#15 M2) says `make ci` re-publishes the index into shared Postgres. The gate report should say whether that check runs before this row's `make ci`.

## What matches the user's requests and the plan
- **Change 1:** the fingerprint holds only code-filled fields (`cases.py` `code_fields`). The retry test `test_a_retry_with_another_summary_returns_the_first_case` is present.
- **Change 2 (deviation, accepted in decision 3):** `not_escalated` was added, the 12 existing entries are pinned by sha256, `orders_place.RECOMMENDED is RECOMMENDED`, and the non-escalated path is tested.
- **Change 3:** URLs and email addresses are refused, the summary is kept as one line of plain text, and the dates/quantities/order-ID test is present.
- **Change 4:** the plan and PROGRESS both say `northwind_16`.
- **Decisions on the stop:**
  - Option (b) masking is built, with the split-card tests (4- and 2-digit groups, with and without a withheld turn) as units and end to end.
  - `CARE_PRICES` is gone and its absence is pinned.
  - L4: `summary_missing` is out of the fingerprint and `total_unavailable` stays in.
  - N4: currency codes count in capitals only.
  - The pin is exactly as approved, plus a test of the set values, which tightens it.
- **No weakened tests** apart from F-A. The rewrites in log lines 70–71 follow behaviour the user removed (catalog total, price failures, L4) or the approved masking. Security pass 3 (e) found the same.
- **The recording matches the plan:**
  - 18 cassettes, all new files, all with test IDs from `test_be07_recorded.py`, recorded 09:29:18–09:29:45 on 2026-11-24.
  - $0.001872, against the plan's estimate of under $0.05.
  - No test card number in any cassette line.
- **The records are accurate against the code:**
  - tool-register: 32 currency codes, 3 attempts and 5 s (`RetryPolicy` defaults), 5 turns, the fields the model sees.
  - data-flow: the 20,000-character ceiling and the residuals.
  - threat-model: the residual and the cited tests, all of which exist.
  - PROGRESS row #26 is `in review`, waiting on `make ci`.
  - COVERAGE CC-16 is accurate.

## Checked
1. Scope: matches BE-07 and stays out of the plan's off-limits paths (assistant, gateway, `services/common`, `evals/**`, orchestrator). PROGRESS, COVERAGE and the governance docs are shared files, so expect merge conflicts. I was not given window A's file list, so its paths are unconfirmed beyond the plan's list.
2. Failing first: mostly shown. Exceptions are F-A (N6 never seen failing), F-C (second-hand) and `test_b_a_card_split…` (the behaviour already held; disclosed).
3. Weakened tests: none found except the N6 narrowing in F-A.
4. Hard rules and protected paths: respected.
   - Not a routine row; stop 1 was taken.
   - Amounts come from the store only. The model has two tools and no write or payment view. The key has no model text, and `dry_run` exists.
   - The transcript is withheld or masked and labelled as data.
   - The audit gets the transcript and summary as type and length only.
   - No `.env`, settings or `.claude` file was touched.
5. Spend and window: the plan named suite `backend-care`, 6 scenarios, under $0.05 and `RUN_BUDGET=1` before recording. The user opened the window, and the ledger before and after is recorded.
6. Revised plan: every requested change and decision is recorded. F-B's two fixes still need the user's acceptance.
7. Control surface: flagged below. Only `cassettes/backend-care/**` is on the control surface as CLAUDE.md defines it. `test_be08_views.py` is not on that list, but it is a pinned check changed under stop 5 with the user's approval, and the PR body and PROGRESS list it. No workflow, Makefile, `tools/*`, conftest, pyproject, package.json, `evals/**`, `data/**` or `RUNNER_SYNC_PATHS` file changed.

## Control surface
All are new files in `cassettes/backend-care/`, added by `make record SUITE=backend-care` in the user's window:
- 052d72f99bc5a3aef4a11525cfc80f0ad5d9a431cb42f478cdf0afc35d407479.json
- 195694aa31494231a935ce04d38081930a89e991ba67c9b4948125898cf369a1.json
- 2c226492cb863780bf9fce1c1f4e6e0e42cc30b54149730a03f9b619ceaeaa23.json
- 4a916754fd17e2ea873d7d42111086d5f863ee7ab7da6d7050de09b250a86021.json
- 529fb6a008171e0cd0482bb007d22bd8eba1b7408a4f1963f568d7886789f2dd.json
- 52c76394f1df10d3c32f3b7ba3986b960f4ef060eaa4838510b8b8cb9e575dcc.json
- 58b94ae5485e9dca6db694f6938f36d2a67be20b825968dc96996983e4c123b8.json
- 6b55e3408e2da58ed09ecff53d00e57c1d5a1e5ece6e7239fa60707fc616835d.json
- 731ab66492b6cbe7c22cb132ce5be6c5ac77e0c0635559473eddce4d5492240e.json
- 76af0668aeaedf9adc13768bec10cb753941e0049a76dcec3b0156d73e8aa91c.json
- 942285ed02b9f318ea6a8cc0c7af4e27ba2e3ca378f32d3648a5d62e0ecae808.json
- 9bb58674e1dd6ea4ff885c2459e09e90db86c895986e1cc0362e698dd7d9ad37.json
- 9c738474acf7d6531a3799b68e4147560ca661beb97f4a7709ca87daa7041810.json
- aa76fd3655a614dde1ea4a699e4f1bef23801820b9debb1bcfaccdd732672006.json
- c8d033e02f893b9608db7701ae0bfdd56e419cba78be29c826692b45d6839a54.json
- e651a589743167dfdea81d34681075d3b1d46ea27775e5047f6614a11edf3275.json
- ea5be2d8f55b9d8d21c7f7d531c8dbbd4ef5fe5678a4b177e8197fb49f5cf187.json
- f07cfe177405c48e5aec5ee5e1bc833ae3d5f6cb33b652e098513023958929a2.json

Also flagged, though not on the defined list: `/Users/rapido/Projects/northwind-shop-b/services/backend/tests/backend_audit/test_be08_views.py` (the view-constant pin, stop 5, approved by the user).

Key files:
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/fakes/cases.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/tests/care/test_be07_units.py
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-BE-07-failing-first.log
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-BE-07-pr-body.md
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-BE-07-plan.md
