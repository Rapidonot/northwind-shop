# Row #18: the $0 replay of every suite at the frozen head (2026-11-27)

The user's option A: a suite with no miss in a $0 replay at the frozen head (`2ca787e`) gets no window, and its
replay is recorded here. The replays ran at `8c500dd`, which changes no frozen path.

Command, per suite:
`LLM_MODE=replay uv run --no-sync pytest -q -p no:cacheprovider -m "" --llm-suite <suite> -o addopts="--strict-markers --import-mode=importlib"`.
`-m ""` with the overridden `addopts` selects `e2e` and `integration` tests too.

**The first pass was environmental for three suites.** The loop did not set `FASTEMBED_CACHE_PATH`, which the
Makefile exports as `$(HOME)/.cache/fastembed`. Searches that needed a query embedding therefore tried to download
the model, the sandbox refused the download (403), and those turns lost their sources. That made
`assistant-intents` and `assistant-redteam` miss. They were rerun with the variable set, and only the rerun
counts. A proxy miss is printed as "3 request(s)" because the CLI retries twice with fewer betas
(`services/llm-replay/SPIKE.md`), so it is one real miss.

| Suite | Window | Result at the frozen head | Window? |
|---|---|---|---|
| `backend-audit` | W1 | 24 hits, 0 recorded (`2026-11-27-18-w1-backend-audit.md`) | done, $0 |
| `backend-orchestrator` | W2 | 5 passed, 0 misses | skipped |
| `backend-care` | W3 | 9 passed, 0 misses | skipped |
| `backend-replay` | W4 | 2 passed, 0 misses (the 1,000-order replay, 4 min 16 s) | skipped |
| `query-understanding` | W5 | 2 passed, 0 misses | skipped |
| `assistant-smoke` | W6 | 16 passed, 0 misses | skipped |
| `assistant-cart` | W7 | rerun: **1 miss** (`test_cart_journey_e2e.py::test_add_refused_add_change_quantity_remove`): the out-of-stock refusal's alternatives, changed by step 0 (b), as planned | **needed** |
| `assistant-orders` | W8 | 11 passed, 0 misses | skipped |
| `assistant-intents` | W9 | rerun: 2 passed, 0 misses (first pass: 1 false miss, model download) | skipped |
| `assistant-redteam` | W10 | rerun: 28 passed, 0 misses (first pass: 3 false misses, model download) | skipped |
| `a2a-05-partner` | W11 | **9 skipped, by design**: "stale: assistant changed since the provisional recording; RECORD-ALL (#18) re-records". Skipped tests prove nothing | **needed** |
| `eval-answers` | W12 | `make ci`'s eval-gate at `2ca787e`: 308/308 replayed, 0 stale, equal to the baseline | skipped |
| `eval-answers-r2` | W12b | see below | **needed** |
| `eval-answers-r3` | W12c | see below | **needed** |
| `eval-judge-repeat` | W13 | see below | put to the user |

## `eval-answers-r2` and `-r3`: the $0 count
- **No r2 or r3 cassette exists**: none in `cassettes/`, none anywhere in git history, none in the runner clone
  (`~/.northwind/runner/cassettes/` holds 14 suites, without either).
- #32's runs reached the ledger (`make llm-spend`: r2 666 calls for $3.573363, r3 666 calls for $3.541933), but
  their cassettes were never kept.
- So **all 308 items of each run miss** at the frozen head, and both runs are fully fresh against the frozen prompt
  (`prompt-26740a49:check-ba8577ea`).
- Run 1 is `eval-answers`, which replays 308/308 at the frozen head. So the three runs are on the same frozen
  inputs, and **the 3-run figure stands**.
- Estimated spend, from #32's per-call costs: about $3.57 + $3.54 = **about $7.11** (`RUN_BUDGET` 6.00 each).

## `eval-judge-repeat`: a measurement, not a replay
- It re-sends run 1's judge requests into its own suite. Only 33 of the 313 judge requests now in `eval-answers`
  have a matching `eval-judge-repeat` cassette ($0 comparison of request bodies, `$TMPDIR/judge_cmp.py`), so about
  280 would miss.
- Estimate: about 280 × $0.0065 ≈ **$1.80**.
- No replay test reads this suite. `evals/tests/test_rag12_side_runs.py` uses a scripted model. So it has no miss
  to fix, only a stale measurement. Whether to run W13 is put to the user.

## Estimated spend left
- W7 (`assistant-cart`, 1 miss): about $0.01.
- W11 (`a2a-05-partner`): $0.10–0.20 (memo).
- W12b and W12c: about $7.11.
- W13 (if the user wants it): about $1.80.

**Total about $7.3, or about $9.1 with W13.** The ledger is at $32.704032; the row's stop is $25 of real spend.
