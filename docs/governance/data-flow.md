# Data flow (NFR-07)

Where each class of data is processed and stored, and which services receive it. PRD §8 NFR-07 asks
for this table to be kept current in the repository; `tests/governance/test_data_flow.py` fails if a
directory under `services/` or a spending Makefile target has no row in the inventory below.

**Baseline:** main at `6f920a1`, 2026-11-06, with 5b and #7 landed (decisions 0009 and 0010). Updated 2026-11-07, at `7d45a40`, for the lines AUTO-2 names and nothing else. Anything not
on main carries its row: planned means it is only in `PROGRESS.md`. All data is synthetic (`CLAUDE.md` hard rule
3); a statement here about shopper data describes the code path, not real people.

**How to read "Anthropic API":** nothing reaches the API at runtime unless the user has opened a
spending window. Replay is the default, CI forces it, and a request with no recording fails rather than
goes out (decision 0001). With no window, nothing spends and the key is never loaded
(`services/llm-replay/tests/test_runner.py::test_no_window_means_no_spend_and_the_key_is_never_loaded`).
So "window" below means: only during a run the user opened with `make llm-runner`, a recording, `make enrich`,
which keeps no recording, or `make llm-proxy` in live mode, which keeps no cassette.

This matrix covers the product's runtime path. Claude Code sessions are a separate route to Anthropic,
for anything in the repo; see the build-time row.

## Data classes

| Data class | Anthropic API | GitHub and CI | Local fastembed | Postgres |
|---|---|---|---|---|
| Shopper message | Window | Only test questions held in cassettes | Yes, as the search query | Query vector only, not stored |
| Retrieved chunk | Window | Yes | Yes, at ingest | Yes, stored |
| Review text | Window | Yes | Yes, at ingest | Yes, stored |
| Internal-only docs | **Bodies: yes, through `make enrich`, in a window; none at runtime.** **Names and topics: yes, through eval items, in a window** | Yes, in fixtures and in `data/enrichment/internal.jsonl` | Yes, at ingest | Yes, stored as `internal` |
| Generated context | **Produced by Haiku, in a window** | Yes, the committed cache | Yes, embedded ahead of the chunk, enriched index only | `northwind_enriched` only; the shared index stays plain until #8 |
| Model answers and telemetry | Answers come from it, in a window | Answers held in cassettes | No | No |
| Prices and stock | No | Yes, in fixtures | No | No exact values |
| API key | Yes, as the credential, in a window | No | No | No |
| Cassettes | No: read locally in replay | Yes | No | No |
| Ledger | No | No | No | No |
| Eval items | Window, through `make eval-live` | Yes | Yes, the query | Query vector only, not stored |
| Answer key | No | Yes, in fixtures | No | No |
| Synthetic shopper data | No | Yes, in `data/backend/` | No | No |
| Build-time session context | **Yes, through Claude Code, on the Max subscription** | Not applicable | No | Not applicable |

### Processing and storage

| Data class | Processed in | Stored in | Evidence |
|---|---|---|---|
| Shopper message | `services/gateway/src/northwind_gateway/chat.py` checks it (2,000 characters, card refusal, nothing echoed); `services/assistant/src/northwind_assistant/turn.py` sends it to search and the model | Nowhere on an ordinary turn: no log and no CLI transcript. In a recording it lands in the cassette and `.llm-proxy-log` | `services/gateway/tests/test_chat.py::test_an_accepted_message_is_not_logged`, `services/assistant/tests/test_tool_loop.py::test_a_turn_leaves_no_trace_of_the_message_on_disk`, `services/gateway/tests/test_chat.py::test_a_rejected_body_is_never_echoed_or_logged_and_reaches_no_model` |
| Retrieved chunk | `services/rag/src/northwind_rag/retrieval.py`, then `render_sources` in `services/assistant/src/northwind_assistant/tools.py`, which labels and escapes it | `rag_chunks` in `infra/db/migrations/0001_rag.sql`, built from `data/fixtures/`; saved contexts in `evals/contexts/dense-only.jsonl`; cassettes | `services/assistant/tests/test_tool_loop.py::test_the_retrieved_text_is_labelled_and_escaped_data`, `services/rag/tests/test_retrieve_context.py::test_a_shopper_gets_only_their_region_and_all` |
| Review text | The same path as a retrieved chunk. Written by shoppers, and some reviews hold planted injections on purpose. **All of it also went to Haiku in #7's window**, before the rule that reviews get no context | `data/fixtures/reviews.jsonl`, `rag_chunks`, cassettes. Sentences written from it are in `data/enrichment/public.jsonl`, unused: see "Generated context" | `PROGRESS.md` row 1; `services/assistant/tests/test_smoke_e2e.py::test_a_planted_injection_in_a_review_is_not_followed` is a strict xfail owned by #13 |
| Internal-only docs | Ingest, and `make enrich` (next row); 11 internal chunks. No runtime path. The filter is `_VISIBLE` in `services/rag/src/northwind_rag/retrieval.py`, and the only caller on main is a shopper (`current_caller` in `services/gateway/src/northwind_gateway/settings.py`) | `data/fixtures/documents.jsonl`, `rag_chunks` with `visibility` = `internal` | `services/rag/tests/test_retrieve_context.py::test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly`, `services/assistant/tests/test_context.py::test_no_canary_reaches_the_model` proves the retrieval filter; `PROGRESS.md` row 3. **What has crossed:** the adversarial items EV-094 to EV-097 in `evals/fixtures/starter_set.jsonl` name or describe internal documents and P0009 on purpose. They went to Sonnet, and P0009's name is in 2 cassettes: `cassettes/eval-answers/76c753a56f19c69c4a61aed9e015308f02f7756e92780fa45687fa5bc8bf238b.json` and `cassettes/eval-answers/a99ae51f02afe28593c2e929661d65542a3b209f2be95cca09a692748be7c2ed.json`. **Checked on the recordings only:** no sentence over 40 characters from any of the 6 internal documents appears verbatim in any cassette (`tests/governance/test_internal_text.py::test_no_internal_sentence_is_in_a_cassette`), and P0009's name is not in `evals/contexts/dense-only.jsonl` (`tests/governance/test_internal_text.py::test_the_saved_contexts_do_not_name_the_internal_product`). This says nothing about #7: that run keeps no cassette, and it has already sent document text (next row). `_context_leaks` in `evals/src/northwind_evals/cli.py` refuses canaries, card and key shapes and internal source IDs only, so plain internal text would pass it |
| Internal-only docs, **through #7** | **RAG-02 enrichment sends internal-only document text to Haiku**, the first designed flow of document bodies to the provider, through `evals/src/northwind_evals/enrich.py`. Parents are built per visibility, so a public chunk's request holds nothing internal. No proxy log and no cassette for this suite | `data/enrichment/internal.jsonl` (11 entries) | Decision 0010, rule 3; `services/rag/src/northwind_rag/enrichment.py`. The run has happened: `make llm-spend` shows suite `rag-enrichment` at $0.113222, which matches the cost in that decision |
| Generated context, **through #7** | One sentence per chunk, for ranking only. It never enters a prompt or a shopper view without a new review. No review gets one in any index | `data/enrichment/public.jsonl` and `data/enrichment/internal.jsonl`. **The public cache still holds 1,085 review sentences from the first run, unused and committed** (decision 0010, "Reviews get no context"); `rag_chunks.context` (`infra/db/migrations/0002_rag_context.sql`) in `northwind_enriched`, a separate database | Decision 0010, rules 4 and 6 and "Reviews get no context"; `services/rag/tests/test_context_checks.py::test_only_the_index_writers_touch_context_or_embed_text`, which misses raw SQL and `asdict(chunk)`, a prerequisite of #10; `services/rag/tests/test_context_allowlist.py::test_the_real_cache_gives_no_review_a_context`, which reads what the index gets, not the cache file. GitHub CI's scan step lists its paths, so `evals/tests/test_enrichment_cache_scan.py` scans the committed cache |
| Model answers and telemetry | Streamed by `services/gateway/src/northwind_gateway/chat.py`. Telemetry is one line of counts per turn, to stderr | Answers: cassettes, and eval answers in `.llm-proxy-log` (`LAST_RUN` in `evals/src/northwind_evals/cli.py`, on every candidate build, replay included; gitignored). Telemetry: not stored | `services/assistant/src/northwind_assistant/usage.py`; `docs/reviews/2026-11-04-rag-13-security-review.md` |
| Prices and stock | Nothing on the runtime path reads them on main: the assistant points to the product page. The one reader is the offline generator in `services/backend/`, which uses them to choose IDs and copies no figure (see its row below). Live tools are planned at #10 and #12. Promotion wording is different: reviews R00881 and R00002 hold it, it reaches the model as review text, and it rests on the prompt until #12 | `data/fixtures/prices.jsonl`, `data/fixtures/stock.jsonl`. Refused at ingest; chunks hold at most a price band | `data/src/northwind_data/ingest_guard.py`, `services/rag/tests/test_sources.py::test_guard_refuses_answer_key_and_live_tables`, `services/rag/tests/test_index.py::test_planted_price_aborts_the_run_and_writes_nothing`, `services/assistant/tests/test_smoke_e2e.py::test_a_price_question_gets_the_product_page_not_a_price` |
| API key | Read only by the runner, and by the key-fragment scan run from the clone. Sandboxed code holds the placeholder | `~/.northwind/secrets/anthropic.env`, outside every checkout | Decision 0005. OS enforcement is measured in `docs/reviews/2026-11-04-cc-27-sandbox-evidence.md`; `tests/hooks/test_sandbox_settings.py::test_the_sandbox_network_list_has_no_anthropic_host` only pins the proposal file. `services/common/tests/test_llm_factory.py::test_anthropic_client_sends_only_the_placeholder_even_with_parent_credentials`. Limits: a key typed into a shell lands in its history, and a key moved with `LLM_ENV_FILE` is off the denied path (decision 0005, CC-27 evidence) |
| Cassettes | Written in the sandbox during a recording; read in replay. Each holds the request (system prompt, messages, tools), the response, usage and cost | `cassettes/`, committed. `.llm-proxy-log` holds raw request bodies locally, gitignored, in record and live modes only | Decision 0001; `services/llm-replay/src/northwind_llm_replay/scan.py`; `.gitignore`; FOUND-2 review finding 7 |
| Ledger | Written by the runner; read by `make llm-spend` | `~/.northwind/ledger.jsonl`, outside the repo. Entries hold model, suite, run ID, a request hash prefix, amounts and token usage. No prompt text | `services/llm-replay/src/northwind_llm_replay/ledger.py`; decisions 0001 and 0005 |
| Eval items | `evals/src/northwind_evals/harness.py`, `evals/src/northwind_evals/judge.py`. Query and context go to the answerer, and the reference facts to the judge | `evals/fixtures/starter_set.jsonl` (100 items), `evals/baselines/dense-only.json`, `evals/fixtures/known_failures.json`. The promotion candidate (`CANDIDATE` in `evals/src/northwind_evals/cli.py`) sits in `.llm-proxy-log`, local and session-writable | `PROGRESS.md` row 4, decision 0003, `evals/tests/test_harness.py::test_containment_is_absolute_whatever_the_baseline_says` |
| Answer key | Tests and the canary scans; refused at ingest | `data/fixtures/_answer_key.json`, committed: traps and canaries | `data/src/northwind_data/ingest_guard.py`, `PROGRESS.md` row 1 |
| Synthetic shopper data | Written by the generator in `services/backend/`; read by the tests. Names, emails, addresses, orders and tracking, all invented. No service loads it yet, and it reaches no model | `data/backend/shoppers.jsonl`, `data/backend/orders.jsonl`, `data/backend/tracking.jsonl`, committed | `services/backend/src/northwind_backend/generate.py`, `services/backend/tests/test_found3_data_safety.py::test_no_string_field_contains_a_pan`, `PROGRESS.md` row 19 |
| Build-time session context | Claude Code sessions and subagents send repo contents, fixtures and evidence to Anthropic, on the user's Max subscription, outside the ledger and `LLM_BUDGET_USD`. That can include anything a session reads: internal-only fixtures, cassettes, reviews, this file. The advisor reads connected folders through the device bridge | Provider side: `unconfirmed`. Key files and `.env*` are denied to sessions | Billing: decision 0001, "Resolution 2026-11-04: billing route". Denies: `tools/settings.proposed.json`, decision 0005 "Open". Device bridge: decided by the user, 2026-11-07: the advisor reads only connected folders, never `~/.northwind`, and writes only when asked (decision 0011). A stated policy, not measured |

## Services and spending commands

| Entry | Status | What it handles | What it reaches | Evidence |
|---|---|---|---|---|
| `services/assistant/` | On main | Shopper message, retrieved chunks, model answers | Postgres through `services/rag/`; the proxy | `services/assistant/src/northwind_assistant/turn.py` |
| `services/backend/` | Row #19, `partial` | Payment, fraud and carrier port interfaces with deterministic fakes. They take idempotency keys, sandbox payment tokens, amounts and currency, payment intent IDs, order and shopper IDs, region, postal code, carrier service, tracking numbers and `dry_run`, never card data. Its generator writes synthetic shoppers (names, emails, addresses), orders and tracking to `data/backend/` | No network, Postgres or model, and no other service imports it. Only the generator touches files: it reads `data/fixtures/`, `data/fixtures/prices.jsonl` and `data/fixtures/stock.jsonl` included, to choose IDs, and copies no product name, price or stock figure. Its card check uses `services/common/` plus a local long-digit check | `services/backend/src/northwind_backend/ports.py`, `services/backend/src/northwind_backend/fakes/payment.py`, `services/backend/src/northwind_backend/fakes/_base.py`, `services/backend/src/northwind_backend/generate.py`, `services/backend/src/northwind_backend/cli.py`, `services/backend/tests/test_found3_card_data.py`, `services/backend/tests/test_found3_data_safety.py::test_no_string_field_contains_a_pan` |
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
| `make enrich` | On main, row #7 `partial` | Sends chunk text, internal-only included, to Haiku. No cassette, because it runs live, and no proxy log, because `log_path` is `None` | The runner, then Anthropic API | `Makefile`, decision 0010 |

Planned, each with its row: `mcp-catalog` and `mcp-rag` (#10); `mcp-orders`, `mcp-policy` and `mcp-cases`
(#19); `orchestrator` (#24); `payment-port` (Window B adapters; the #19 interface is in `services/backend/`). Each gets a row here when
its directory lands, or the test fails.

## Services that receive data

| Service | What it receives | Evidence |
|---|---|---|
| Anthropic API, runtime | Prompts and tool definitions, in a window only. Only `POST /v1/messages`, two models, no images or documents | Decision 0005, "What the runner forwards" |
| Anthropic, Claude Code | Whatever a session reads; see the build-time row | Decision 0001 |
| GitHub and GitHub CI | The repository: fixtures, cassettes, contexts, baselines, docs. CI holds no API key and always replays, on a throwaway Postgres. `Guard` runs main's `evals/src` beside the PR's services | `.github/workflows/ci.yml`, `.github/workflows/guard.yml`, decisions 0001 and 0009 |
| fastembed | Local only: text in, vectors out. The only network step is fetching the model with `make model`, and the sandbox cannot reach those hosts | `services/rag/src/northwind_rag/embedding.py`, decision 0004 |
| Postgres | Chunks, metadata, embeddings, and query vectors at search time. Local stack, shared by all worktrees. Generated context only in `northwind_enriched` | `infra/docker-compose.yml`, `infra/db/migrations/0001_rag.sql`, `infra/db/migrations/0002_rag_context.sql`, decisions 0005 and 0010 |
| Payment, fraud, carrier agents | Nothing today. Planned: Window B, P4 and P5 | PRD §7 |

## Open items

Owners placed at GOV-1 are explained in `docs/governance/threat-model.md`, "Owners".

| Item | Owner |
|---|---|
| What Anthropic retains from API calls and from Claude Code sessions: `unconfirmed`, not recorded in the repo | Window B P6, NFR-07 |
| What the device bridge keeps: `unconfirmed`. What it may reach is decided (build-time row), as a stated policy | The user |
| `.github/workflows/ci.yml` lists its scan paths and leaves out `docs/governance/`, so a secret typed into these docs is scanned by nothing. Control surface, not changed here | AUTO-2 |
| The docstring in `services/common/src/northwind_common/llm.py` still says the proxy reads the key from `.env`, which decision 0005 replaced. Control surface, not changed here | AUTO-2 |
| From the clone, the key-fragment scan covers `CASSETTES_DIR` only, not a worktree's `.llm-proxy-log`, `evals/` or `data/enrichment/`. Its other roots are the clone's own copies, so a worktree's cache needs `CASSETTES_DIR` pointed at it (`services/llm-replay/src/northwind_llm_replay/cli.py`, decision 0005, "Limits") | #18 |
| No PAN scan at ingestion, so a card number planted in fixture text could reach the model on the assistant path. The eval path scans it; `.github/workflows/ci.yml` does not scan `data/fixtures/` | Window B P6, NFR-04 (`PROGRESS.md` row 3 lists it open) |
| Masking personal data before model calls (NFR-05), and consent, retention, export and deletion (NFR-06) | Window B, P6 |
| Conversation memory does not exist yet, so nothing about a shopper persists. When it does, this table changes | SA-05, Window B |
