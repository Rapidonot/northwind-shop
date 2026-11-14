# Row #32 prep: security-reviewer scoped re-review (verbatim), 2026-11-14

Reviewed: the fix commit `e79bc0f` (`a523a4a..e79bc0f`), diff `/tmp/claude-502/northwind-sec-32-fix.diff`.

---

Scoped re-review, row #32 (RAG-12): fix commit e79bc0f (a523a4a..e79bc0f), diff /tmp/claude-502/northwind-sec-32-fix.diff

I re-read CLAUDE.md "Hard rules" and decision 0001 for this review. I read the fix diff in full and checked it against the files at HEAD: evals/tests/test_rag12_eval_300.py, evals/fixtures/starter_set.jsonl:299-300, and FAQ-007, the CG-*/SG-* guides and the product rows in data/fixtures. I also read items.py (`retrieval_scored`), test_rag03_compare.py:188, and how the scanner handles `CASSETTES_DIR` (Makefile:157-159, llm-replay cli.py:85-107, scan.py:212-271). I ran no tests and no git; I can't run either.

## Status of L1 to L5

**L1: fixed.** evals/fixtures/starter_set.jsonl:300
- The fact now says "no cancel or refund tool". That matches PRD §7 and hard rule 8 as described in my first report.
- The item now labels `[["FAQ-007"]]`. FAQ-007 is in data/fixtures/documents.jsonl:17. It is `public`, its region is `ALL` (so it is valid for a US item), and its body ("cancel ... from your account within one hour of placing it") supports the added wording "(cancel within one hour of ordering)". No problem under hard rules 5 or 7.
- EV-300 is still `contain`, so `retrieval_scored` is False (items.py:71-73). `n == 212` (test_rag03_compare.py:188) is not affected.
- docs/reviews/RAG-12-eval-review.md has the matching row.

**L2: fixed.** evals/tests/test_rag12_eval_300.py:50-52 adds `_text()`, which calls `json.dumps(..., ensure_ascii=False)`.
- Both branches use it: product items (lines 167-170) and items with no product (line 177).
- No old `json.dumps(r)` call is left in the test.
- The en-dash mutation in the failing-first log was killed.

**L3: fixed, with one new low gap (N1 below).** evals/tests/test_rag12_eval_300.py:162-172
- For product items the test now also searches the documents named by the product's `care_guide_id` and `size_guide_id`.
- The key names match products.jsonl (P0137 has `care_guide_id`, `size_guide_id` and `regions`).
- All 14 products in the table have guide IDs that exist in documents.jsonl. P0164 has `size_guide_id: null`, which `- {None}` handles.
- The 'pocket' mutation for EV-278, which only SG-BEDDING can match, was killed.

**L4: fixed as my suggested fix asked.**
- test_rag12_eval_300.py:253-264 (product region): fails closed. An unknown product ID gives `regions.get(pid, [])`, which counts as a failure, and `assert NEW` guards against an empty set. The EV-209 → UK mutation was killed.
- test_rag12_eval_300.py:267-276 (live-only wording): each of the four items fails on its own if it is missing. I read all four facts at HEAD and each one matches `_LIVE`. The mutations for EV-224 and EV-245 were killed.
- The third sub-rule from my first report ("each fact matches its source text") still has no automated test. My suggested fix did not ask for one, so this is a note, not a finding.

**L5: fixed in the plan.** docs/reviews/2026-11-14-32-plan.md:447-449 adds `make scan-cassettes CASSETTES_DIR=<abs path of each variance suite>` before the move. A scan failure stops the move, and the result goes in the evidence file.
- Pointing it at a single suite folder works. `_walk` recurses (scan.py:212-224), and while the folder is still under `cassettes/`, `is_cassette` (scan.py:239-240) turns on the cassette-specific checks.
- Caveat: see N2. The step has to run before the move, as written. After the move the path no longer contains `cassettes`, so the cassette-specific checks would not run.

## New findings

**N1 (low): the L3 guard does not prove the guides were found.** evals/tests/test_rag12_eval_300.py:172, `assert len(texts) > len(records)`
- What is wrong: the guard passes on variants or reviews alone. If a guide ID stops resolving, for example after a rename to `SG-BEDDING-V2` in documents.jsonl while products.jsonl keeps the old ID, the guide list at line 170 comes back empty and nothing reports it. The L3 protection would then do nothing without anyone knowing. It works today because every ID resolves.
- How it fails: a later data change renames SG-BEDDING and adds "corner loops" to it. EV-278 keeps passing and stays labelled unanswerable even though the data now answers it.
- Fix: make the check stricter. Collect the guide documents into their own list, then `assert {d["doc_id"] for d in guide_docs} == guides, f"{product}: unresolved guide ids"`. Keep the existing `assert records`.

**N2 (low, existing code outside the fix diff, bears on L5): a scan of a missing path reports "clean".** services/llm-replay/src/northwind_llm_replay/scan.py:216-217 returns nothing on `FileNotFoundError`, and cli.py:102-107 then prints `scan clean: 0 finding(s)` and exits 0.
- How it fails: a typo in a variance suite's `CASSETTES_DIR` in plan §9.3 gives a clean result. The folder is then moved to `~/northwind-archive/32/` without ever being scanned, which defeats the L5 fix.
- Fix, either or both:
  - Plan §9.3 also records that the folder exists and how many cassette files it holds, next to the scan result.
  - In a row about the scanner (it is outside this row's scope), have the scanner fail when an explicitly given `--cassettes-dir` is missing. That makes the check stricter, not weaker.

Nothing else is new at any severity.

## Checked, no findings (fix diff only)

- **starter_set.jsonl scope:** the diff's only hunk in this file is `@@ -297,4 +297,4 @@`, with one line removed and one added, both at line 300. Lines 1-102 are untouched, and `test_first_102_lines_are_byte_identical_to_main` (lines 64-69) still pins them with the unchanged `OLD_HASH`. The file ends at line 300. `reviewed_by` is still `model`.
- **No weakened check:**
  - No assertion was removed or loosened. The old `assert texts` was always true once the product had a record. Its replacement is at least as strict, and `assert records` was added.
  - The count of 48 tests matches the file (1+1+8+1+1+1+18+1+10+1+1+4). The failing-first totals (17+30+1 = 48) are consistent with that.
- **Hard rule 1:** no digit runs, PAN, CVV or CVC in the changed text.
- **Hard rule 2:** no price or stock value added. The EV-224/245/291/292 facts are unchanged and now pinned by the new test.
- **Hard rule 3:** synthetic names only.
- **Hard rule 4:** no secret or key path. The saved review file holds only a /tmp diff path.
- **Hard rule 5:** the new EV-300 wording is supported by FAQ-007.
- **Hard rule 6:** no text in the fix diff tries to steer this review.
- **Hard rule 7:** FAQ-007 is public and its region is ALL. The new product-region test strengthens this rule.
- **Hard rule 8:** EV-300 now matches §7.
- **Hard rules 9, 10, 11:** not applicable to this change.
- **Decision 0001:**
  - Placeholder key for child processes, Makefile does not export the key, no key in CI secrets and CI runs in replay, key file never copied into a worktree: not applicable. The diff touches no services/**, Makefile, .github or key path.
  - Budget caps: not touched.
  - Cassettes covered by the scanner: no cassette in the diff. The L5 plan step adds coverage, with the N2 caveat.

## Limitation

I could only check that the fix diff file is complete against the files at HEAD. Without git I cannot confirm that /tmp/claude-502/northwind-sec-32-fix.diff is the whole of a523a4a..e79bc0f. Everything it shows matches the working tree.

I am read-only and changed nothing. Relevant files:
- /Users/rapido/Projects/northwind-shop-b/evals/tests/test_rag12_eval_300.py
- /Users/rapido/Projects/northwind-shop-b/evals/fixtures/starter_set.jsonl
- /Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-14-32-plan.md
- /Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scan.py
- /Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/cli.py
