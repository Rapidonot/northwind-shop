# Row #18, W12b: `eval-answers-r2` (2026-11-27)

- **Window:** opened by the user at `b7cbccd` with `RUN_BUDGET=6.00` and 120 minutes. The freeze is at `2ca787e`.
- **Hold guard before the window** (11:59:59Z): `live holds: 0 in database 'northwind'`, exit 0. The eval run builds
  its catalog in process.
- **Count at $0 before the window** (at `b7cbccd`): no `eval-answers-r2` cassette existed in `cassettes/`, in git
  history or in the runner clone, so all 308 items would miss. No eval input changed between `2ca787e` and `b7cbccd`
  (`evals/src`, `evals/contexts`, `evals/adversarial`, `evals/baselines`, `data/fixtures`, `data/embeddings`,
  `services/rag`, `services/mcp-rag`, `services/assistant/src`).
- **Run:** `make eval-live EVAL_SLICE=308 EVAL_SUITE=eval-answers-r2` with `FASTEMBED_CACHE_PATH` set,
  12:01:05–12:28:26Z, exit 0. Result: "308 items, 0 not ok".
- **Metrics, reported only (a variance run, not gated):** groundedness 0.990, relevance 0.929, false_refusal 0.009,
  behaviour_ok 0.971, citation_ok 0.990 (305/308, or 302/308 counting live-backed claims), fabricated_ok 0.994. No
  refusals. 2 payload mentions. Judge `judge:v6:claude-sonnet-5-5`.
- **Spend:** `make llm-spend` showed $32.773357 before and $36.042725 after, so **$3.269368**. The suite's ledger
  total is $6.842731, #32's $3.573363 included. The row's real spend so far is **$3.338693**, under the $25 stop.
- **Proxy hit log:** `.llm-proxy-log/eval-answers-r2.jsonl` did not exist before. After the run it holds 656 lines,
  all `recorded`, with no hit and no refusal. No old cassettes existed, so the hit-log rule removes nothing.
- **Cassettes:** 0 before, 656 added (13 MB), none removed.
- **Results:** `.llm-proxy-log/eval-answers-r2-results.json` (git-ignored), 308 results, all `ok`. A copy is in the
  session's `$TMPDIR` for the 3-run figure.
- **Replay right after:** not available. `northwind_evals.cli run` refuses unless `LLM_MODE=record`, and `gate`
  replays only `eval-answers`. No replay command reads this suite.
- **Tests that read the suite** (`git grep -l eval-answers-r2 -- '*.py'`, the user's rule after W11):
  `evals/tests/test_rag12_judged_extra.py` and `evals/tests/test_rag12_side_runs.py`, 61 passed. Both use scripted
  models or the CLI's arguments, so neither reads the new cassettes.
- **Scan:** `make scan-cassettes` clean (0 findings). The key-fragment check from the runner clone is the user's.
- **Freeze:** `git diff 2ca787e..HEAD` on the 24 frozen paths is empty, and `shasum -a 256 -c` gives 24 OK.
- **For the final proof:** RECORD-ALL's "full replay of every suite with 0 misses" has no command for the variance
  suites. That needs a decision before the final replay (put to the user with W12c).
