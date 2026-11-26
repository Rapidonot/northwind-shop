findings

Row #31 (RAG-10 + RAG-07), scoped re-read since f2dad0f. The diff `/tmp/claude-502/northwind-review-RAG-10.diff` starts with `base 2c40d88dcac6f556c9d517644efc25585be19dd5` and `head 695c025da628af1141ed353cd63920e0299f5947`. Both match the SHAs you gave me. The session can write those refs, so a match only rules out accidents.

What I read:
- `CLAUDE.md`, my raw report, and the seven files you named, in the full diff.
- In the plan: the addendum and the human-marks quote.
- `RUNNER_SYNC_PATHS` (`Makefile:195`), the branch reflog, the router's pick rules in `query.py`, and the catalog in `data/fixtures/products.jsonl`.

I have no execution tool, so I ran no test and computed no hash. I took the user's decisions from your quote and cannot check that they are the user's own messages.

**No high is open, and F1 is closed.** The one medium is N1: about 180 new security-tagged tests have never been seen failing.

## Status of F1–F7
- **F1 (high): closed.** The PR body (`docs/reviews/2026-11-25-RAG-10-pr-body.md:55-62`) and PROGRESS row 31 (`PROGRESS.md:98`) now list six control-surface files and quote the user's acceptance. The PR body is honest that `test_rag07_tw.py` came after that acceptance. The six match the diff (list below). The gate report is not written yet and must carry the same list.
- **F2 (medium): closed.** `test-writer` ran, its raw report is committed (`docs/reviews/2026-11-26-RAG-10-test-writer.raw.md`), and its outcome is recorded in the PR body and PROGRESS.
- **F3 (medium): closed for the injection and unsure tests and the pins; the RAG-11 part is still open (see N1).**
  - The blind router `378fdce` fails only L1's 12 cases.
  - The system mutant gives 40 failures and the unsure mutant gives 7 (`failing-first.log:59-88`).
  - Each pin fails on a one-byte change.
  - The sed mutants that did not apply are disclosed and were redone in Python.
- **F4 (low): still open** (L1).
- **F5 (low): closed.** The breakdown at `pr-body.md:19-22` now matches the held-out score file exactly: 4 + 1 + 2 + RH7-001 + RH7-059 = 9 misses.
- **F6 (low): closed.** The coupling and its owner are written down at `pr-body.md:65-67`.
- **F7 (low): partly open** (L2).

## Findings (most severe first)

**N1. Item 2, medium. The passing security tests in `test_rag07_tw.py` were never seen failing.**
- File: `/Users/rapido/Projects/northwind-shop/services/rag/tests/test_rag07_tw.py`. The tests are at :61 (NFR-03, injection alone), :69 (NFR-03, injection around a question), :113 (RAG-11, caller words) and :137 (RAG-11, internal policy).
- The only failing evidence for this file is the test-writer's run against the frozen router. That run covers the 5 cases that are now xfail and nothing else.
- No mutant run against this file is in `docs/reviews/2026-11-25-RAG-10-failing-first.log`. The F3 mutants ran against `test_rag07_router.py` only.
- The RAG-11 property has never been seen failing in either file. The one mutant aimed at it, the caller-word stripping, was a no-op (`failing-first.log:88`), so `test_rag07_router.py:65` is also open.
- This can be fixed inside the row without touching the frozen router:
  - run the logged system and unsure mutants against `test_rag07_tw.py`;
  - add one mutant, on a copy, that makes a caller word such as "internal" or "in the UK" a cue;
  - log the results.

**L1. Items 4 and 6, low. F4 is recorded as closed, but it is not.**
- `pr-body.md:50-51` says "The user confirmed the `human` marks (F4) in their decision of 2026-11-26". `PROGRESS.md:98` says the "4 low [were] fixed in the records".
- The only confirmation anywhere is `docs/reviews/2026-11-25-RAG-10-plan.md:59-60`. The session wrote that quote and committed it at `60fde44`, before my review. F4 already called that same evidence unconfirmed.
- The decisions you quoted from after the review say nothing about the marks.
- F4 stays open until the user confirms the marks directly at `/req-done`. The records should say it is open, not confirmed.

**L2. Item 4, low. F7 is partly open: there has been no security read after `9c42876`, and the records do not say so.**
- `security-reviewer` has not read:
  - `evals/fixtures/rag07_heldout.jsonl` (RH7-059 carries injected text);
  - the three pin tests;
  - `test_rag07_tw.py`, which is hard-rule 6/7 tagged and synced to the runner.
- The PR body's Reviews section (`pr-body.md:37-39`) names only the pass at `54b6b52` and does not list what it did not read.
- The claim that the held-out products all validate with "0 errors" (`pr-body.md:63-64`) has no output file behind it.
- Whether to run another security read is the user's decision. At minimum, the PR body and gate report should state the unread set.

**L3. Item 3, low. The replacement case is a catalog product, but it is not like-for-like.**
- The case is `test_rag07_tw.py:213`, "Which throw is best for a cold living room?".
- Throws are active, public catalog products (`data/fixtures/products.jsonl:163-164`, subcategory `throw`).
- But "cold" is a specifications cue on its own (`services/rag/src/northwind_rag/query.py:1048`), and the tent message has no such word. So this case can pass on an attribute cue without exercising the rule-3 pick (`_PICK`, `query.py:1059-1066`, reached through `throw` in `_PRODUCT_NOUNS`, `query.py:246`).
- A pick with no attribute word, matching the tent case's shape, would test the same rule.
- This is not a weakening: the expectation is unchanged and the original case stays as a strict xfail. It is a weaker replacement than the case it stands in for.

**L4. Low, for the user's attention. The "not sold" reason does not separate the tent case from cases that pass.**
- The reason is at `test_rag07_tw.py:29`.
- These cases name products the shop does not sell, and they pass: daypack, poncho, gloves, sleeping bag and boots (`:219-222`, `:227-228`), and boots again at `:21`. None of these is in `data/fixtures/products.jsonl`, and there are no tents anywhere in `data/fixtures`.
- What actually fails is narrower. A "which … best" pick whose noun is missing from `_PRODUCT_NOUNS` (`query.py:243-249`) gets no pick cue and falls back to "unsure".
- The reason is the user's own wording, so the session did nothing wrong here. The user should know that this xfail marks a router limit on nouns outside that list, which a wiring row may want to cover too.

**L5. Item 4, low. An unexplained line in the pin restore.**
- `failing-first.log:77` reads "-- restored: 3 files differ from HEAD". It follows `git checkout` of the four pinned files, and the three files are not named.
- Most likely these were the records being edited at the time, but the log does not say so.
- The `make ci` pin tests will catch a stray byte in the committed files. The log should still name the three files.

**L6. Item 6, low. Wording and records.**
- `pr-body.md:24-26` reads "the strict xfails in `test_rag07_tw.py` (colon directives) and the frozen router miss `<admin>…` … and narrows". As written, it says the xfails miss RH7-059.
- COVERAGE CC-14 for row #31 does not record the `test-writer` run or the advisor reads ("Update COVERAGE.md when a Claude Code feature was exercised").

## Unconfirmed
- **Router unchanged after `f2dad0f`.**
  - `docs/reviews/2026-11-26-RAG-10-commit-evidence.txt` was committed in `54ce765` and lists commits only up to `f2dad0f`. Its "HEAD" hash line (:68) is the head at the time it was written, not `695c025`.
  - The reflog messages for `54ce765..695c025` claim no router edit, and your 7-file list excludes `query.py`. But those are messages and claims, not per-commit file lists I can see.
  - `test_rag07_router_frozen.py` will check this at `make ci`.
- **That `test_rag07_tw.py` matches what the test-writer wrote, apart from the 5 marks and the added case.**
  - The original file was never in git, so I cannot diff it.
  - The structure does match the raw report: 22 injected forms; 14 phrases × 4 positions × 3 questions; 20 edge inputs; 7 greetings; and 16 guide cases, now 17. The total is 193 + 1 = 194, which matches "189 passed, 5 xfailed".
  - Assertion-level equality is not visible to me.
- **The 189/5 result and the lint claims.** These are self-reported. `make ci` has not run, on the user's instruction, and I do not count that as a finding.

## Checked
1. **Scope: checked, no finding.**
   - Since `f2dad0f`, the changes are one test file that calls only `route_indexes` (report-only), plus records.
   - There is no hunk in window B's CHK-07 paths.
   - Per-commit file lists are unconfirmed (above).
2. **Failing first: findings (N1).**
   - F3 is closed for the injection and unsure tests and the pins.
   - The RAG-11 tests in both files, and the passing NFR-03/RAG-11 tests in `test_rag07_tw.py`, have never been seen failing.
3. **No test weakened: checked, low (L3).**
   - There are exactly 5 xfails, all `strict=True`:
     - `route: specifications` (`:44`) gives 1 case in the injection-alone test and 3 in the injection-around-a-question test;
     - the tent case is the fifth (`:214-218`).
   - Reasons:
     - `COLON` (`:25-28`) names RH7-059's class and says "Any row that wires the router fixes these first".
     - `NOT_SOLD` (`:29`) matches the user's wording.
   - There is no other xfail or skip in the file.
   - No other test file is in the scope since `f2dad0f`.
   - Equality to the test-writer's original is unconfirmed.
4. **Hard rules and protected paths: findings (L1, L2, L5).**
   - The new test file contains no card data, secrets, prices or stock counts.
   - It has no LLM client.
5. **Spend and window: checked, no finding.** $0, no window, no cassette and no promote. The row stops before `make ci`, as the user asked.
6. **Requested changes: findings (L1, L6).** Otherwise everything the user asked for is in the records:
   - Decision 1 (option A) is implemented as stated.
   - Decision 2 (F1) is recorded with the user's quote.
   - The three scores, blind 44/60, in-sample 58/60 and held-out 51/60, are in `PROGRESS.md:98`, `PROGRESS.md:515` and the PR body table.
   - RAG-07 is recorded as partial, and the six control-surface files are listed accurately.
   - The order "single test files, scoped advisor re-read, records, stop before `make ci`" is being followed.
7. **Control surface: listed below, for human attention.**

## Control surface (every changed file on it in the row's diff)
- `evals/fixtures/rag07_queries.jsonl` (new; approved in the plan)
- `evals/fixtures/rag07_heldout.jsonl` (new; the user's decision (a))
- `services/rag/src/northwind_rag/query.py` (on `RUNNER_SYNC_PATHS` through `services/rag`; accepted by the user, decision 2)
- `services/rag/tests/test_rag07_router.py` (on `RUNNER_SYNC_PATHS`; accepted, decision 2)
- `services/rag/tests/test_rag07_router_frozen.py` (on `RUNNER_SYNC_PATHS`; accepted, decision 2)
- `services/rag/tests/test_rag07_tw.py` (new since `f2dad0f`; on `RUNNER_SYNC_PATHS`; committed by the user's decision 1, but not named in the decision 2 acceptance, which the PR body discloses)

`evals/tests/**`, `services/assistant/tests/test_rag10_rounds.py`, `docs/reviews/**`, `PROGRESS.md` and `COVERAGE.md` are not on the control surface. There is no change in the diff to any `conftest.py`, cassette, baseline, `evals/src` file, workflow, `.claude` file, Makefile or `pyproject.toml`.

I made no edits.
