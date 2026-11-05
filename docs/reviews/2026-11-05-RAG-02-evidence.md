# Row #7, RAG-02: evidence

Base `f50f7c8`. Figures that come after the spending window are added then.

## Mistakes made during this row

The user asked for these to be recorded.

1. **A commit after a stop string.** `git worktree remove` printed `Operation not permitted`, which
   `CLAUDE.md` says means stop and do not commit. `b8210d1` went through because it was chained in the
   same command. The user accepted the commit. Since then each git command runs on its own.
2. **The shared database.** `make test-integration` ran once against `northwind`, because a command was
   appended without `DATABASE_URL`. Checked straight after: same index version, 2,351 rows, `0001` only,
   no new columns, no leftover schemas, last row write unchanged. The tests use throwaway schemas, which
   is why nothing changed. Every later command named the scratch database.
3. **A test that wrote to `public`.** `test_dropping_a_context_goes_back_to_embedding_the_plain_text`
   called `sync_chunks` without migrating its schema, so `rag_chunks` resolved to `public` on the search
   path. It cut the scratch index from 2,351 rows to 5. `test-writer` reported the 5 rows as "older
   work", which was wrong: that database was created the same day. After merge, plain
   `make test-integration` would have done this to the shared index. Fixed, and proven by comparing
   `public` before and after a full integration run.

Two smaller errors, for completeness: the plan said a duplicate pair of chunks sat under different
parents, when they share one (decision 0009 gives the real reason for the parent in the key), and a
background `make ci` reported exit 0 from a wrapper `echo` when `make` itself had failed.

## Failing first

- `readable_forms`: before the first fix, 4 of 7 cases failed. Three of the 7 could not fail, because
  `json.dumps` escapes those characters, so they were dropped.
- The final split, with each remaining case shown failing with the fix reverted. Reverted to
  `text.splitlines()`: the three separator cases (U+2028, U+2029, NEL) and the rig test fail. Reverted to
  `text.split("\n")`: the CR-framed case fails. CRLF has no case, because a trailing `\r` is JSON
  whitespace, so no revert can make it fail.
- Review fixes, written before their tests, so proven by mutation: each fix reverted in turn, its test
  seen failing, the file restored. 23 mutants, all caught. The first pass left one alive, the `@` rule,
  because the bare-domain rule caught the same sentence, so it gained its own test.
- The scan root and Makefile wiring, the same way: 5 mutants, all caught (`advisor-reviewer` asked for
  this). Removing `data/enrichment` from the roots fails its tests, and so does dropping `LLM_MODE=live`
  from `enrich` or adding it to `ingest-enriched` or `eval-enrichment-report`.

## Where the row differs from the approved plan

All approved by the user, and listed because the plan file was not revised.

- The split is CRLF, CR and LF (answer 5), not `\n` only.
- `data/enrichment` joined the default scan roots, so `services/llm-replay/.../cli.py` changed as well as
  `runner.py`, and `pricing.py` had its docstring corrected. These appear in `runner-sync`.
- The spending-target pin widened in `evals/tests/test_wiring.py` (answer 2). New wiring tests went in
  `test_makefile_enrich.py`, not `test_makefile_targets.py`.
- The enriched index guard is an allowlist, and the tests use `northwind_enriched_test`.
- `ENRICH_PROMPT_VERSION` is 2, because the prompt now says to start with "This".

## Key-fragment scan of the cache

`EVAL_ROOTS` paths are relative, so from the clone they name the clone's own copy, and before merge the
worktree's cache would not be read. `CASSETTES_DIR` is what reaches it:

    make -C ~/.northwind/runner scan-cassettes CASSETTES_DIR=/Users/rapido/Projects/northwind-shop/data/enrichment

Checked in the sandbox with a temp directory: the same Makefile path reads the directory, fails on a
planted card number and passes when it is removed. The fragment comparison itself runs only from the
clone. The clone at `f50f7c8` already has `--cassettes-dir`, so this needs no sync.

## Security review triage (decided by the user)

The two reviews, of the runner-side diff and of the rest, gave no critical findings. The paraphrase
finding came in as high and the user rated it medium, because context is ranking-only and nothing reads
it. The CI scan gap is closed by a tagged pytest test, staying out of the workflow. Open items are in
`PROGRESS.md`.
