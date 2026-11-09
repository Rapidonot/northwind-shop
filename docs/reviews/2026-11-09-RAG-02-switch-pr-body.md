RAG-02: E1 measured on the region-filtered path; the switch to an enriched shared index is rejected (row #8c)

## What this does

- **The pass rule was measured, and it failed.** Before any switch, row #8c fixed a rule (E1) for planted trap reviews
  that reach a top 5. It is measured on the shopper's region-filtered hybrid path, over all 100 eval items, comparing
  the plain index with the isolated enriched one (`northwind_enriched`). E1 failed all three clauses:
  - (a) injection-trap appearances in top 5s: enriched 19, plain 16;
  - (b) three new (item, trap) entries: TRAP-INJ-003 on EV-070, TRAP-INJ-008 on EV-087, TRAP-INJ-002 on EV-088;
  - (c) R00219 on EV-087 and R00970 on EV-088 enter the top 5 of adversarial items.

  All three moved from rank 6 to rank 5. Reviews carry no context, so their own vectors did not change; their
  neighbours moved. No contradiction trap (TRAP-CON) entered any top 5.
- **Decision: the switch is rejected for v1** (the user's, 2026-11-09). The shared index stays plain. No workflow,
  `Makefile`, `services/`, baseline, cassette, `data/enrichment` or `data/embeddings` change. No LLM call, no spend.
  RAG-02 stays `partial`, with that as its written reason (PRD §10: Should items pass or have a written reason).
- **Reported, not the rule:**
  - review share of top-5 slots, 13.4% plain against 16.2% enriched;
  - recall@5, 0.954 against 0.960.

  These are on the items the configuration was chosen with, so they do not confirm RAG-02.

## What stays in the code

- `evals/src/northwind_evals/enrichment_report.py`:
  - `E1_RULE`, row #8c's text word for word; a test pins it to the row in `PROGRESS.md`;
  - `e1_verdict`. (a) and (b) count TRAP-INJ chunks; (c) counts TRAP-INJ and TRAP-CON chunks, per chunk, on
    adversarial and unanswerable items (the user's readings). An item with no segment fails (c);
  - `review_share`, `trap_ranks`, and a TRAP-CON table in the report;
  - `manifest_problems` and `same_index_problems`.
- `evals/src/northwind_evals/cli.py`:
  - `_contradiction_chunks()`, from the answer key;
  - `_index_problems(conn, embedder_id, side=...)`, with these checks:
    - each index's manifest must match this checkout;
    - the plain side must hash as the plain build (`corpus_sha256`, no cache applied), and the enriched side must not;
    - the two sides must not be one database or one index version;
  - `make eval-enrichment-report` exits 1 when E1 fails or an index is refused.
- `docs/reviews/RAG-02-enrichment-report.md` is regenerated from the measurement. The report states the region-filtered
  path and which traps each clause counts. The old text, "one query with no region filter", is gone.
- One existing test was edited: `test_the_report_states_its_own_limits`. Its two assertions on text that is no longer
  true now pin the corrected text. The user accepted the edit.

## Evidence

- **Tests:** `evals/tests/test_enrichment_e1.py` (32) and `evals/tests/test_enrichment_e1_more.py` (21, by `test-writer`),
  all tagged `RAG-02`. They were seen failing first. The decision 0010 guard test passes: the new code reads no generated
  context.
- **Mutants:** 27 of 27 killed, 7 of them on the security-review fixes.
- **Reviews:**
  - `security-reviewer` found one medium (E1 could compare an index with itself) and one low (an item with no segment
    skipped (c)). Both are fixed, and the scoped re-review closed them;
  - one informational low is logged in `PROGRESS.md`;
  - E1 was re-measured after the fixes, with byte-identical output.
- **Evidence file:** `docs/reviews/2026-11-09-RAG-02-switch-evidence.md`, which holds the user's decisions, the
  measurement, the code hashes measured, and row #8's `gh pr checks` record.

## Labels and checks

- **`gate-change`**, because `evals/src` changes. `Guard / eval-gate-main` runs main's `evals/src`, so it may fail on
  purpose; this is the case `make ship` accepts under that label (decision 0009). A green Guard would mean only that
  main's copy also passes.
- **No `ci-change`, `baseline-change` or other label is needed:** no workflow, baseline, known-failure or cassette change.
- `eval-enrichment-report` is not part of `make ci`, so CI does not run E1. Its exit code is for whoever reruns the
  comparison.

## Open items (in `PROGRESS.md`)

- **Condition 4** (the R00970 check for "unavailable" and the steer): owner #13.
- **The frozen-vector gap for an index with context, and the shared-database downgrade risk:** owner hardening, only if a
  later row revisits enrichment.
- **#32's RAG-02 part:** the user's Nov 12 checkpoint.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
