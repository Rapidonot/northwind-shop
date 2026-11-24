# Row #11, window 1: `query-understanding` (2026-11-24)

- **Window and head:** opened by the user after B shipped (main `f84f97d`). B changed no model-input path:
  `git diff 6e46b8a origin/main` over services/assistant, common, mcp-rag, mcp-catalog, rag, gateway, data/fixtures and
  evals/src is empty. Recorded at head `57faaa8`.
- **Command:** `make record SUITE=query-understanding`. Exit 0, 2 passed.
- **Spend:** `make llm-spend` total $26.753027 → $26.754913, which is **$0.001886** for 15 Haiku calls.
  - 10 calls are the labelled routed items. 7 are the routed eval items, 2 of them shared with labelled items.
  - Cache hit rate 90.5%.
- **Cassettes:** 15 added under `cassettes/query-understanding/`. `make scan-cassettes` is clean. In replay the test
  file gives 2 passed.

## RAG-04 criterion
Scored on labels frozen at `924a487`, with the rule in the labelling guide.

| | Result |
|---|---|
| Filter extraction, the pipeline (rules plus Haiku) | **99 / 100** (bar 95). The one miss is QU-080 |
| Rules only (no model) | 93 / 100 |
| Per field | availability 100, colour 99, price 100, size 99 |
| Split correct (part count) | 97 / 100 |
| Paths | 90 rules, 10 model |

## Haiku plan time (NFR-13 input)

| Source | n | min | p50 | p95 | max |
|---|---|---|---|---|---|
| Client wall time per plan (`Understood.ms`, labelled routed items) | 10 | 1,439 | 2,339 | 3,510 | 3,510 |
| Runner `first_byte_ms` (non-streamed, so close to the whole response) | 15 | 1,161 | 1,763 | 3,228 | 3,228 |

- Through the runner in record mode, recorded one call after another; p95 is nearest-rank.
- **Every call is over the 700 ms live deadline.** In live mode the deadline would turn each Haiku-routed message
  into its rules plan. Haiku adds no first-token time in live mode, but it adds no accuracy there either. The 99/100
  holds only when Haiku is waited for, as in record and replay. That is the user's decision, in the row's report.
