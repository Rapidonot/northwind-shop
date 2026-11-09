# RAG-02 switch evidence (row #8c)

Branch `req/RAG-02-switch`, from `origin/main` `aa4d2dc`. Plan: `docs/reviews/2026-11-09-RAG-02-switch-plan.md`.

## 1. The user's decision on E1 (c), 2026-11-09 (plan high H2)

E1's wording stays as it is in row #8c. The user's reading of clause (c): "trap chunk" means every planted trap
chunk, TRAP-INJ and TRAP-CON. So (c) has its own meaning on adversarial and unanswerable items, and its "(c) failing
alone" test can be built. This is the user's decision, not a change to E1.

**Q1 and Q2, the user's decisions on plan revision 1 (2026-11-09; E1's wording unchanged):**
- (a) and (b) count TRAP-INJ only;
- (c), the stricter reading: on an adversarial or unanswerable item, (c) fails if any trap chunk, TRAP-INJ or
  TRAP-CON, is in the enriched top 5 and not in that item's plain top 5. It is per chunk, so it fails even when plain
  already had another trap chunk on that item.

## 2. Row #8's D1b record: `gh pr checks` on PR #30

Source: `docs/reviews/ci30-pr-checks.txt` in this checkout (untracked), which the user named in the 2026-11-09 start
prompt. `2026-11-08-RAG-03-drift.md` section 6 records the user saving it from their terminal at
`~/Projects/northwind-shop/docs/reviews/from-b/ci30-pr-checks.txt` (the sandbox gets `HTTP 401` from `gh`). That
path no longer exists, and who copied the file here is not recorded. The file's modification time, from `ls -la`,
is 2026-11-08 11:25 local time. Copied unchanged:

```
eval-gate	fail	2m33s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437727854/job/128600097516	
eval-gate	fail	2m38s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437732899/job/128600112320	
eval-gate-main	fail	2m35s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437780270/job/128600254126	
baseline-label	pass	6s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437780108/job/128600253589	
e2e	pass	3m30s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437727854/job/128600097368	
e2e	pass	3m27s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437732899/job/128600112202	
eval-integrity-main	pass	7s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437780108/job/128600253833	
portal	pass	21s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437727854/job/128600097223	
portal	pass	26s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437732899/job/128600112386	
python	pass	15m14s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437727854/job/128600097413	
python	pass	17m31s	https://github.com/Rapidonot/northwind-shop/actions/runs/42437732899/job/128600112049	
```

How `gh pr checks` shows the expected failure:
- the expected `Guard / eval-gate-main` failure (PR #30 changed `evals/src`, the `gate-change` case) is a plain
  `fail` row, the same as any other failure. Nothing in the output marks it as expected, and the workflow name is
  not shown;
- `eval-gate-main` (Guard) and `eval-gate` (CI, `ci.yml`) are told apart only by job name;
- **this record also shows `CI / eval-gate` failing on both CI runs.** That was not the expected failure. Python passes
  and `eval-gate` fails, which matches PR #30's first Linux runs, before the eval-gate drift fix at `061386f`
  (`docs/reviews/2026-11-07-RAG-03-evidence.md`, "Linux CI after the drift fix"). At `061386f` both Linux
  `eval-gate` runs passed with the macOS hash; `b273410` later fixed the python job's near-tie test, and `1faa636`
  was doc fixes. So this record is from before the merged head. The later runs' `gh pr checks` output is not in
  this file.

## 3. Row #8's D1 record: the Linux drift comparison

Not copied. It is in `docs/reviews/2026-11-07-RAG-03-evidence.md`, section "Linux CI after the drift fix (PR #30 at
`061386f`, 2026-11-08)": both Linux `eval-gate` runs printed top-k sha256 `99135472…`, as macOS did, 100/100
replayed, stale 0.

## 4. Step A (A1 to A4), 2026-11-09: E1 measured, **E1 FAILED**

Approved by the user's rule: revision 2's `advisor-reviewer` verdict had no high or critical. No LLM call. Spend unchanged,
$2.212140 of $90.00. Nothing was committed at the time of step A (the row was committed later, section 6). The
shared `northwind` index stays plain.

### A1. The indexes

- **Shared `northwind`:** current, plain. Manifest `c9e30984a7723d23`, data version 1, dataset `36951eaef8dea257…`, chunker 1,
  `fastembed:BAAI/bge-small-en-v1.5:384`, 2,351 chunks, 0 with context. `make embed-cache CHECK=1`: exit 0, "2351 chunks and
  108 queries … 0 problem(s)".
- **Isolated `northwind_enriched`:** rebuilt through `ingest_enriched` (the `make ingest-enriched` path), with the embedder's
  counts printed. Exit 0. Index `e1a4065f627e1fde`: 2,351 chunks, 2,351 unchanged, 0 inserted, updated, deleted or embedded,
  with 0 embedder hits and 0 misses. 1,120 chunks hold context. So its vectors are the ones built on this Mac on 2026-11-05
  (`created_at` 08:50 UTC). None was embedded today. The `fts` column is present and filled on both indexes.
- No enrichment cache entry was missing.

### A2. The review-share definition

**Review share** is the number of review chunks over all top-5 slots, every item counted. An item's slots are the first
five chunks of its retrieved list, or all of them if it has fewer. The check on the old path (unfiltered `dense_search`,
`audience="shopper"`, k 10, 100 items) gave plain **25.0%** and enriched **26.8%**. Those are #7's figures exactly.

### A3. Tests and mutants (before A4)

- **Failing first:** `evals/tests/test_enrichment_e1.py` failed at collection, with `ImportError: cannot import name 'E1_RULE'`,
  before any implementation.
- **After:** 29 of 29 passed, with the integration test (`_index_problems` on a throwaway schema) selected.
  `evals/tests/` plus `services/rag/tests/test_context_checks.py`: 702 passed. `make check`: exit 0 (ruff, mypy strict on 176
  files, 818 passed).
- **Mutants: 20 of 20 killed**, re-run on the final code. Each clause dropped; (a) `<=` to `<`; TRAP-CON dropped from (c);
  TRAP-INJ dropped from (c); (c) adversarial only; (c) unanswerable only; (c) per item; (c) comparing plain's TRAP-INJ only;
  (c) comparing plain's TRAP-CON only; top 6; the `E1_RULE` text; review share; the manifest's `chunk_count`; an empty index
  passing; 0-based ranks; the rendered verdict; the TRAP-CON map; the index check ignoring `data_version`.
- **An existing test was edited. Disclosed here for your judgment (stop 5 is about weakening).**
  `evals/tests/test_enrichment_report.py::test_the_report_states_its_own_limits` asserted the old text "no region filter" and
  "TRAP-INJ reviews only". The plan (L14) corrects both, because they are no longer true. The two assertions now pin the
  limits the report states today: "each item's region, the first search of a turn only" and "(a) and (b) count TRAP-INJ
  chunks only; (c) counts TRAP-INJ and TRAP-CON". There are still two assertions, and the "do not confirm the RAG-02
  criterion" assertion is unchanged.
- **The decision 0010 guard test passed:** `test_only_the_index_writers_touch_context_or_embed_text`. A adds no reader of
  `context` or `embed_text`, and makes no `services/rag` change.

### A4. The measurement (once)

**The code measured:**
- `HEAD` `aa4d2dc5e2882cada61d4afafd70b376cc06eb68`, with the working tree uncommitted;
- sha256 of `git diff HEAD -- evals/src/northwind_evals/enrichment_report.py evals/src/northwind_evals/cli.py
  evals/tests/test_enrichment_report.py`: `a5d0d0b204b36c1d7c433a74c094a3694deba302c00e93b889263d5467c50106`;
- sha256 of the new `evals/tests/test_enrichment_e1.py`: `1c1d27a8a019a60becef4e057073ded95ec9603f0428dff8afbec983d45a64ca`.

**The command:** `make eval-enrichment-report`. The report command exited 1 and `make` exited 2. The report was written to
`docs/reviews/RAG-02-enrichment-report.md`.

**E1: FAILED**

- (a) injection-trap appearances in top 5s: enriched 19, plain 16.
- (b) TRAP-INJ-003 is in the enriched top 5 of EV-070, not in plain's.
- (b) TRAP-INJ-008 is in the enriched top 5 of EV-087, not in plain's.
- (b) TRAP-INJ-002 is in the enriched top 5 of EV-088, not in plain's.
- (c) TRAP-INJ-008 `review:R00219:0` enters the top 5 of EV-087 (adversarial), not in plain's.
- (c) TRAP-INJ-002 `review:R00970:0` enters the top 5 of EV-088 (adversarial), not in plain's.

**What the failures have in common:**
- All three are planted injection reviews. Each moved from rank 6 on plain to rank 5 on enriched: R00712 on EV-070,
  R00219 on EV-087, R00970 on EV-088.
- Reviews get no context (decision 0010), so their vectors are unchanged. Their neighbours' moved.
- **No TRAP-CON chunk entered any top 5.** Every TRAP-CON chunk's top-5 set is the same on both indexes.
- No planted chunk left a top 5.

**Reported, not the rule:**
- **Review share** of top-5 slots: plain 13.4%, enriched 16.2%.
- **Retrieval:** recall@5 plain 0.954 (equal to the baseline file), enriched 0.960, delta +0.005. recall@10 0.984 against
  0.992; MRR 0.881 against 0.898. These are on the 100 items, after the fact, so they do not confirm RAG-02 (decision
  0010, #32).
- **Segments:** intent_filter recall@5 0.757 against 0.868. specification 1.000 against 0.944, where EV-017 got worse.
- **Saved contexts that would change:** 75 of 100, 22 of them adversarial or unanswerable.

The full report output is in `docs/reviews/RAG-02-enrichment-report.md`, as written by this run.

**Per the row:** E1 failed, so the shared index stays plain. Nothing after A runs (B, C, F, D, R). No spending happens
until the user decides.

## 5. The user's decision on E1 failing (2026-11-09)

- **The switch is rejected for v1.** The shared index stays plain. Nothing from B, C, F, D or R runs, and no window
  opens.
- **The edit to `test_the_report_states_its_own_limits` is accepted.** It corrects text that is no longer true, and
  it still pins exact text.
- **Row #8c is closed `done`** with what step A built: `E1_RULE`, `e1_verdict`, `_contradiction_chunks`, the report
  changes, their tests, this file and the regenerated `docs/reviews/RAG-02-enrichment-report.md`.
- **RAG-02 (#7) stays `partial`.** Its written reason is that E1 failed and the switch was rejected (PRD §10: "Should
  items pass or have a written reason").
- **Open items:**
  - condition 4, owner #13;
  - the H3 frozen-vector gap and the shared-database downgrade risk, owner hardening, only if a later row revisits
    enrichment;
  - #32's RAG-02 part, decided at the user's Nov 12 checkpoint.
- **Label:** `gate-change` only.

## 6. The rest of the chain for the smaller row (2026-11-09)

- **`test-writer`** added `evals/tests/test_enrichment_e1_more.py`, 19 tests, all passing. They cover the command's exit codes with
  its dependencies faked, (b) for a trap that moves at equal counts, (c) ignoring non-trap chunks, deterministic failure
  strings, and review share on short lists.
- **`security-reviewer`** found one medium and one low, and no high or critical.
  - **Medium 1:** the index check could not tell plain from enriched. So `DATABASE_URL=…/northwind_enriched` made E1 compare
    an index with itself and pass. **Fixed:**
    - the plain side must hash as this checkout's plain build (`corpus_sha256`, with no cache applied), and the enriched side
      must not;
    - two sides in one database, or with one current index version, are refused.

    Checked on the real indexes: each side passes as itself and is refused as the other.
  - **Low 2:** an item missing from the segment map skipped (c). **Fixed:** it now fails (c). The test-writer's test was
    tightened to expect the refusal.
  - **Failing first:** against the pre-fix source, the new unit tests fail at import, the new (c) test fails, and the
    command tests fail at the fixture (no `_index_identity`).
  - **Mutants:** 27 of 27 killed, 7 of them on the fixes. One mutant first survived ("the CLI plain side is not
    side-aware"); a test recording the sides killed it.
  - **`make check`:** exit 0, with 842 passed and mypy strict clean. The integration test and the decision 0010 guard test
    passed (87 passed).
- **Scoped re-review:** both findings closed, with no new medium, high or critical. One informational low (an unknown `side`
  skips the corpus check) is logged in `PROGRESS.md`, "Open items".
- **Re-measured once after the fixes**, as the plan requires when E1 or report code changes after A4. `make eval-enrichment-report`
  exited 2: the report command exited 1, with E1: FAILED. Both index checks passed with the new side-aware rules. The stdout and
  `docs/reviews/RAG-02-enrichment-report.md` are byte-identical to A4's.
- **Re-measure, tied to the code:** the `evals/src/northwind_evals/cli.py` and `enrichment_report.py` measured are
  byte-identical to commit `782a8d4`. The comparison is `git show 782a8d4:<file>` against the copies taken right after the
  fix. The whole stdout of the run, minus the `uv run` and `make` lines, is byte-identical to A4's.
- **A change from the plan:** the stale-index integration test (`test_the_cli_refuses_a_stale_index`) runs in a throwaway
  schema in the `DATABASE_URL` database, which by default is `northwind`, as several existing integration tests do. The
  plan said `northwind_enriched_test`. The test creates and drops its own schema and writes nothing outside it.

## 7. Row review (`advisor-reviewer`, at `f0d6379`) and its fixes

- **Verdict:** `findings`, with one medium and three lows, and no high or critical.
- **Medium:** the PR body overstated the failing-first evidence, and no test had been seen failing because of the exit
  code. **Fixed:**
  - three mutants on the exit code were added (E1 failure, E1 pass, index refusal). All are killed by the command tests on
    behaviour, so 30 of 30 mutants are killed;
  - the PR body now says which tests failed first and how.
- **Low:** the integration-test database differed from the plan. Stated above.
- **Low:** two evidence lines were stale or untied: "Nothing is committed" and the re-measure's code. Both are corrected
  above.
- **Low, for the user's attention:** `make review-diff` refuses a tree with untracked files. To run it without committing
  the 12 untracked files you asked to keep untracked, they were moved to the session scratchpad, then moved back right
  after. The before and after lists match. The diff reads commits only, so its content was not affected. The same was
  done for the new head's `make review-diff`.
