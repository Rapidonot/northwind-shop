# Data flow (NFR-07)

Where each class of data is processed and stored, and which services receive it. PRD §8 NFR-07 asks
for this table to be kept current in the repository; `tests/governance/test_data_flow.py` fails if a
directory under `services/` or a spending Makefile target has no row in the inventory below.

**Baseline:** main at `f50f7c8`, 2026-11-05. Anything not on main carries its row: in flight means it
is on a branch, planned means it is only in `PROGRESS.md`. All data is synthetic (`CLAUDE.md` hard rule
3); a statement here about shopper data describes the code path, not real people.

**How to read "Anthropic API":** nothing reaches the API at runtime unless the user has opened a
spending window. Replay is the default, CI forces it, and a request with no recording fails rather than
goes out (decision 0001). With no window, nothing spends and the key is never loaded
(`services/llm-replay/tests/test_runner.py::test_no_window_means_no_spend_and_the_key_is_never_loaded`).
So "window" below means: only during a recording run the user opened with `make llm-runner`.

This matrix covers the product's runtime path. Claude Code sessions are a separate route to Anthropic,
for anything in the repo; see the build-time row.

## Data classes

| Data class | Anthropic API | GitHub and CI | Local fastembed | Postgres |
|---|---|---|---|---|
| Shopper message | Window | Only test questions held in cassettes | Yes, as the search query | Query vector only, not stored |
| Retrieved chunk | Window | Yes | Yes, at ingest | Yes, stored |
| Review text | Window | Yes | Yes, at ingest | Yes, stored |
| Internal-only docs | Bodies: no path in main's code. **Names and topics: yes, through eval items, in a window.** **#7 in flight: document text has gone to Haiku, from the branch** | Yes, in fixtures | Yes, at ingest | Yes, stored as `internal` |
| Generated context | None on main. **#7 in flight: produced by Haiku** | None on main. #7: committed cache | None on main. #7: embedded ahead of the chunk | None on main. #7: separate database |
| Model answers and telemetry | Answers come from it, in a window | Answers held in cassettes | No | No |
| Prices and stock | No | Yes, in fixtures | No | No exact values |
| API key | Yes, as the credential, in a window | No | No | No |
| Cassettes | No: read locally in replay | Yes | No | No |
| Ledger | No | No | No | No |
| Eval items | Window, through `make eval-live` | Yes | Yes, the query | Query vector only, not stored |
| Answer key | No | Yes, in fixtures | No | No |
| Build-time session context | **Yes, through Claude Code, on the Max subscription** | Not applicable | No | Not applicable |

### Processing and storage

| Data class | Processed in | Stored in | Evidence |
|---|---|---|---|
| Shopper message | `services/gateway/src/northwind_gateway/chat.py` checks it (2,000 characters, card refusal, nothing echoed); `services/assistant/src/northwind_assistant/turn.py` sends it to search and the model | Nowhere on an ordinary turn: no log and no CLI transcript. In a recording it lands in the cassette and `.llm-proxy-log` | `services/gateway/tests/test_chat.py::test_an_accepted_message_is_not_logged`, `services/assistant/tests/test_tool_loop.py::test_a_turn_leaves_no_trace_of_the_message_on_disk`, `services/gateway/tests/test_chat.py::test_a_rejected_body_is_never_echoed_or_logged_and_reaches_no_model` |
| Retrieved chunk | `services/rag/src/northwind_rag/retrieval.py`, then `render_sources` in `services/assistant/src/northwind_assistant/tools.py`, which labels and escapes it | `rag_chunks` in `infra/db/migrations/0001_rag.sql`, built from `data/fixtures/`; saved contexts in `evals/contexts/dense-only.jsonl`; cassettes | `services/assistant/tests/test_tool_loop.py::test_the_retrieved_text_is_labelled_and_escaped_data`, `services/rag/tests/test_retrieve_context.py::test_a_shopper_gets_only_their_region_and_all` |
| Review text | The same path as a retrieved chunk. Written by shoppers, and some reviews hold planted injections on purpose | `data/fixtures/reviews.jsonl`, `rag_chunks`, cassettes | `PROGRESS.md` row 1; `services/assistant/tests/test_smoke_e2e.py::test_a_planted_injection_in_a_review_is_not_followed` is a strict xfail owned by #13 |
| Internal-only docs | Ingest only on main; 11 internal chunks. The filter is `_VISIBLE` in `services/rag/src/northwind_rag/retrieval.py`, and the only caller on main is a shopper (`current_caller` in `services/gateway/src/northwind_gateway/settings.py`) | `data/fixtures/documents.jsonl`, `rag_chunks` with `visibility` = `internal` | `services/rag/tests/test_retrieve_context.py::test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly`, `services/assistant/tests/test_context.py::test_no_canary_reaches_the_model` proves the retrieval filter; `PROGRESS.md` row 3. **What has crossed:** the adversarial items EV-094 to EV-097 in `evals/fixtures/starter_set.jsonl` name or describe internal documents and P0009 on purpose. They went to Sonnet, and P0009's name is in 2 cassettes in `cassettes/eval-answers/`. **Measured 2026-11-05, on main's recordings only:** no sentence over 40 characters from any of the 6 internal documents appears verbatim in any cassette, and P0009's name is not in `evals/contexts/dense-only.jsonl`. This says nothing about #7: that run keeps no cassette, and it has already sent document text (next row). `_context_leaks` in `evals/src/northwind_evals/cli.py` refuses canaries, card and key shapes and internal source IDs only, so plain internal text would pass it |
| Internal-only docs, **#7 in flight** | **RAG-02 enrichment sends internal-only document text to Haiku**, the first designed flow of document bodies to the provider. Parents are built per visibility, so a public chunk's request holds nothing internal. No proxy log and no cassette for this suite | `req/RAG-02-enrichment:data/enrichment/internal.jsonl` (11 entries) | `req/RAG-02-enrichment:docs/decisions/0009-enrichment-without-batches.md`, `req/RAG-02-enrichment:services/rag/src/northwind_rag/enrichment.py`. The run has happened: `make llm-spend` shows suite `rag-enrichment` at $0.113222, which matches the cost in decision 0009 |
| Generated context, **#7 in flight** | One sentence per chunk, for ranking only. It never enters a prompt or a shopper view without a new review. Reviews get none | `req/RAG-02-enrichment:data/enrichment/public.jsonl` and `req/RAG-02-enrichment:data/enrichment/internal.jsonl`; `rag_chunks.context` in `northwind_enriched`, a separate database | Decision 0009, rules 4 and 6 and "Reviews get no context" (on the branch). Row #7 on main still says Batches; decision 0009 replaces that with plain Messages calls through the runner |
| Model answers and telemetry | Streamed by `services/gateway/src/northwind_gateway/chat.py`. Telemetry is one line of counts per turn, to stderr | Answers: cassettes, and eval answers in `.llm-proxy-log` (`LAST_RUN` in `evals/src/northwind_evals/cli.py`, on every candidate build, replay included; gitignored). Telemetry: not stored | `services/assistant/src/northwind_assistant/usage.py`; `docs/reviews/2026-11-04-rag-13-security-review.md` |
| Prices and stock | Nothing reads them on main: the assistant points to the product page. Live tools are planned at #10 and #12. Promotion wording is different: reviews R00881 and R00002 hold it, it reaches the model as review text, and it rests on the prompt until #12 | `data/fixtures/prices.jsonl`, `data/fixtures/stock.jsonl`. Refused at ingest; chunks hold at most a price band | `data/src/northwind_data/ingest_guard.py`, `services/rag/tests/test_sources.py::test_guard_refuses_answer_key_and_live_tables`, `services/rag/tests/test_index.py::test_planted_price_aborts_the_run_and_writes_nothing`, `services/assistant/tests/test_smoke_e2e.py::test_a_price_question_gets_the_product_page_not_a_price` |
| API key | Read only by the runner, and by the key-fragment scan run from the clone. Sandboxed code holds the placeholder | `~/.northwind/secrets/anthropic.env`, outside every checkout | Decision 0005. OS enforcement is measured in `docs/reviews/2026-11-04-cc-27-sandbox-evidence.md`; `tests/hooks/test_sandbox_settings.py::test_the_sandbox_network_list_has_no_anthropic_host` only pins the proposal file. `services/common/tests/test_llm_factory.py::test_anthropic_client_sends_only_the_placeholder_even_with_parent_credentials`. Limits: a key typed into a shell lands in its history, and a key moved with `LLM_ENV_FILE` is off the denied path (decision 0005, CC-27 evidence) |
| Cassettes | Written in the sandbox during a recording; read in replay. Each holds the request (system prompt, messages, tools), the response, usage and cost | `cassettes/`, committed. `.llm-proxy-log` holds raw request bodies locally, gitignored, in record and live modes only | Decision 0001; `services/llm-replay/src/northwind_llm_replay/scan.py`; `.gitignore`; FOUND-2 review finding 7 |
| Ledger | Written by the runner; read by `make llm-spend` | `~/.northwind/ledger.jsonl`, outside the repo. Entries hold model, suite, run ID, a request hash prefix, amounts and token usage. No prompt text | `services/llm-replay/src/northwind_llm_replay/ledger.py`; decisions 0001 and 0005 |
| Eval items | `evals/src/northwind_evals/harness.py`, `evals/src/northwind_evals/judge.py`. Query and context go to the answerer, and the reference facts to the judge | `evals/fixtures/starter_set.jsonl` (100 items), `evals/baselines/dense-only.json`, `evals/fixtures/known_failures.json`. The promotion candidate (`CANDIDATE` in `evals/src/northwind_evals/cli.py`) sits in `.llm-proxy-log`, local and session-writable | `PROGRESS.md` row 4, decision 0003, `evals/tests/test_harness.py::test_containment_is_absolute_whatever_the_baseline_says` |
| Answer key | Tests and the canary scans; refused at ingest | `data/fixtures/_answer_key.json`, committed: traps and canaries | `data/src/northwind_data/ingest_guard.py`, `PROGRESS.md` row 1 |
| Build-time session context | Claude Code sessions and subagents send repo contents, fixtures and evidence to Anthropic, on the user's Max subscription, outside the ledger and `LLM_BUDGET_USD`. That can include anything a session reads: internal-only fixtures, cassettes, reviews, this file. The advisor reads the checkouts through the device bridge | Provider side: `unconfirmed`. Key files and `.env*` are denied to sessions | Billing: decision 0001, "Resolution 2026-11-04: billing route". Denies: `tools/settings.proposed.json`, decision 0005 "Open". Device bridge: stated by the user in the GOV-1 brief, 2026-11-05; nothing in the repo records it, so what it can reach is `unconfirmed` |

## Services and spending commands

| Entry | Status | What it handles | What it reaches | Evidence |
|---|---|---|---|---|
| `services/assistant/` | On main | Shopper message, retrieved chunks, model answers | Postgres through `services/rag/`; the proxy | `services/assistant/src/northwind_assistant/turn.py` |
| `services/common/` | On main | The only LLM client factory; card check; prices | Builds clients for the proxy; holds no key | `services/common/src/northwind_common/llm.py`, `services/common/src/northwind_common/cards.py` |
| `services/gateway/` | On main | Shopper message, SSE answer | `services/assistant/`, Postgres health check | `services/gateway/src/northwind_gateway/chat.py` |
| `services/llm-replay/` | On main | Prompts, responses, the key (runner only), the ledger | Anthropic API, from the runner only | Decision 0005 |
| `services/rag/` | On main | Chunks, embeddings, query vectors | Local fastembed, Postgres | `services/rag/src/northwind_rag/retrieval.py`, `services/rag/src/northwind_rag/embedding.py` |
| `apps/portal/` | On main | Shopper message, answers | The gateway only | `apps/portal/` |
| `evals/` | On main | Eval items, saved contexts, baselines | Proxy, Postgres, local fastembed | `evals/src/northwind_evals/cli.py` |
| `data/` | On main | Synthetic fixtures | Nothing external | `data/src/northwind_data/` |
| `make llm-runner` | On main, user only | Opens one window. The only target that sends the key anywhere | Anthropic API | `Makefile`, decision 0005 |
| `make record` | On main | Re-records one suite through a window | The runner, then Anthropic API | `Makefile`, decision 0005 |
| `make eval-live` | On main | Records answers and verdicts through a window | The runner, then Anthropic API | `Makefile`, `PROGRESS.md` row 4 |
| `make llm-proxy` | On main | Replay by default. In record or live mode it also needs a window, so it counts as able to spend | The runner, in a window | `services/llm-replay/tests/test_runner_criteria.py::test_serve_in_record_mode_with_no_window_exits_2_with_the_command` |
| `make enrich` | **In flight, #7; not on main** | Sends chunk text, internal-only included, to Haiku | The runner, then Anthropic API | `req/RAG-02-enrichment:Makefile`, decision 0009 on the branch |

Planned, each with its row: `mcp-catalog` and `mcp-rag` (#10); `mcp-orders`, `mcp-policy` and `mcp-cases`
(#19); `orchestrator` (#24); `payment-port` (#19 interface, Window B adapters). Each gets a row here when
its directory lands, or the test fails.

## Services that receive data

| Service | What it receives | Evidence |
|---|---|---|
| Anthropic API, runtime | Prompts and tool definitions, in a window only. Only `POST /v1/messages`, two models, no images or documents | Decision 0005, "What the runner forwards" |
| Anthropic, Claude Code | Whatever a session reads; see the build-time row | Decision 0001 |
| GitHub and GitHub CI | The repository: fixtures, cassettes, contexts, baselines, docs. CI holds no API key and always replays, on a throwaway Postgres | `.github/workflows/ci.yml`, decision 0001 |
| fastembed | Local only: text in, vectors out. The only network step is fetching the model with `make model`, and the sandbox cannot reach those hosts | `services/rag/src/northwind_rag/embedding.py`, decision 0004 |
| Postgres | Chunks, metadata, embeddings, and query vectors at search time. Local stack, shared by all worktrees | `infra/docker-compose.yml`, `infra/db/migrations/0001_rag.sql`, decision 0005 |
| Payment, fraud, carrier agents | Nothing today. Planned: Window B, P4 and P5 | PRD §7 |

## Open items

| Item | Owner |
|---|---|
| What Anthropic retains from API calls and from Claude Code sessions: `unconfirmed`, not recorded in the repo | No row owns this |
| What the device bridge can reach and keep: `unconfirmed` | No row owns this |
| `.github/workflows/ci.yml` lists its scan paths and leaves out `docs/governance/`, so a secret typed into these docs is scanned by nothing. Control surface, not changed here | No row owns this |
| `CLAUDE.md` says only `make record` and `make eval-live` spend. `make llm-runner` opens the window, and #7 adds `make enrich`. Control surface, not changed here | #7 edits `CLAUDE.md` on its branch |
| The docstring in `services/common/src/northwind_common/llm.py` still says the proxy reads the key from `.env`, which decision 0005 replaced. Control surface, not changed here | No row owns this |
| The key-fragment scan covers `cassettes/` only, not `.llm-proxy-log` or `evals/` | Decision 0005, "Limits"; no row owns this |
| No PAN scan at ingestion, so a card number planted in fixture text could reach the model on the assistant path. The eval path scans it; `.github/workflows/ci.yml` does not scan `data/fixtures/` | `PROGRESS.md` row 3 lists it open; no row owns this |
| Masking personal data before model calls (NFR-05), and consent, retention, export and deletion (NFR-06) | Window B, P6 |
| Conversation memory does not exist yet, so nothing about a shopper persists. When it does, this table changes | SA-05, Window B |
| This table describes `f50f7c8`, and GOV-1 merges after 5b and #7. At that merge, the in-flight rows here and in `docs/governance/tool-register.md` must change to landed. No test sees this: the table check runs one way, and `req/` citations are exempt | The user, at the GOV-1 merge |
