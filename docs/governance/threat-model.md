# Threat model (PRD §10)

Assets, actors, and STRIDE across each trust boundary, mapped to the OWASP Top 10 for LLM Applications.
Each threat has either a control that exists today, with its evidence, or `open` with its owner. What no
`PROGRESS.md` row named an owner for is in "Owners", below. Residuals are taken from the reviews and
decisions and cited, not re-derived.

**Baseline:** main at `6f920a1`, 2026-11-06, with 5b (decision 0009) and #7 (decision 0010) landed. Updated
2026-11-07, at `7d45a40`, for the lines AUTO-2 names and nothing else. Landed is not done: row 5b is `done`,
#7 is `partial` in `PROGRESS.md`, and the cells below say what is left. Landed means the code is on main and
its tests pass. `Guard` ran green on GitHub on PRs #23 to #27, stated by the user, 2026-11-07. The GOV-1 session read the
runner clone's `HEAD` as `6f920a1` on 2026-11-06. That is a git ref, not a record of what loads there, no file
holds that read, and the clone has moved since. Rows are `PROGRESS.md` rows. A boundary with nothing built says so: its threats are `open` by design, not by
oversight.

**OWASP edition:** LLM Top 10 **2026**, from https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/.
The PRD names no edition, and the numbering differs from 2025. LLM01 Prompt Injection, LLM02 Sensitive
Information Disclosure, LLM03 Excessive Agency, LLM04 Supply Chain, LLM05 Data and Model Poisoning, LLM06
Unbounded Consumption, LLM07 Misinformation, LLM08 Hidden Context Exposure, LLM09 Vector and Embedding
Weaknesses, LLM10 Improper Output Handling.

Tool and agent threats also map to the OWASP Top 10 for Agentic Applications 2026; that mapping is an open item for P3/P5.

**Sources:** decisions are in `docs/decisions/`, cited by number. Reviews, cited by short name:

| Short name | File |
|---|---|
| FOUND-2 review | `docs/reviews/2026-11-04-found-2-security-review.md` |
| SA-02 review | `docs/reviews/2026-11-04-sa-02-security-review.md` |
| RAG-13 review | `docs/reviews/2026-11-04-rag-13-security-review.md` |
| CC-27 evidence | `docs/reviews/2026-11-04-cc-27-sandbox-evidence.md` |
| 5a evidence | `docs/reviews/2026-11-04-5a-gate-hardening-evidence.md` |
| AUTO-1 evidence | `docs/reviews/2026-11-05-auto-1-evidence.md` |
| 5b evidence | `docs/reviews/2026-11-05-5b-evidence.md` |
| RAG-02 evidence | `docs/reviews/2026-11-05-RAG-02-evidence.md` |

## Assets

| Asset | Why it matters | Where |
|---|---|---|
| API key and credit | Spend, and access to the provider | `~/.northwind/secrets/anthropic.env`; `docs/governance/data-flow.md` |
| Runner clone and its environment | Code here runs beside the key | `~/.northwind/runner`, decision 0005 |
| Budget state | Caps and the record of spend | `~/.northwind/ledger.jsonl` |
| Eval gate inputs | A weakened gate hides every later regression | `evals/baselines/`, `evals/fixtures/known_failures.json`, `evals/fixtures/starter_set.jsonl`, `cassettes/`, `evals/contexts/`, and the local promotion candidate |
| Enrichment cache | Feeds the isolated index now and, at #8, the shared one | `data/enrichment/` |
| Internal-only documents and unreleased product | Must never reach shoppers (hard rule 7) | `data/fixtures/documents.jsonl`, Postgres |
| Answer integrity | Grounded, cited, correct per region (hard rules 2 and 5) | The assistant's output |
| Control surface | What gates and reviews rest on | Listed in `CLAUDE.md` |
| Planned: card data, orders, payments, shopper identity | None exist yet | PRD §7, Window B |

## Actors

| Actor | Can do | Trust |
|---|---|---|
| Shopper | Send text to `/api/chat` | Untrusted |
| Review author | Place text that reaches the model as a source | Untrusted; fixtures hold planted injections on purpose |
| The model | Choose tool calls and write text | Untrusted output |
| Claude Code session or subagent | Edit checkouts in the sandbox, run tests, commit | Partly trusted: treated as able to edit anything it can write (decision 0005) |
| The user | Open windows, push, merge, promote, sync the runner | Trusted, and the only reviewer (decision 0006) |
| Model provider, GitHub | Receive what `docs/governance/data-flow.md` lists | Third parties; retention `unconfirmed` |
| Planned: partner agents, external A2A agents, staff | Nothing yet | PRD §7, Window B |

## Boundary 1: shopper to assistant

Built: `/api/chat` to the assistant, one message at a time, no sessions.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Caller claims another region or staff audience | LLM03 | Region and audience come from server config, never the request: `services/gateway/tests/test_chat.py::test_the_region_comes_from_server_config_not_the_request`. **Open:** no shopper identity, `current_caller` is a stub, #19 |
| Tampering | Prompt injection in the shopper's message | LLM01 | Message escaped inside its own block: `services/assistant/tests/test_first_request.py::test_hostile_source_and_shopper_text_stay_inside_their_own_blocks`. No state-changing tool exists to abuse: `docs/governance/tool-register.md`. The message is NFKC-normalised before escaping, as source text is (#13): `services/assistant/tests/test_rag11_gateway_caller.py::test_the_user_message_is_normalised_then_escaped_on_its_way_to_the_model`. Output check: landed, #13 (decision 0015) |
| Tampering | Attached file or slash command in a message | LLM01 | `services/assistant/tests/test_tool_loop.py::test_a_shopper_cannot_attach_a_file_with_an_at_path`, `services/assistant/tests/test_tool_loop.py::test_a_slash_command_stays_shopper_text` |
| Repudiation | No record of who asked what | LLM03 | **Open:** nothing on the shopper path is audited yet. #20 audits backend port calls and the Orders subagent only; this boundary is NFR-10's (Window B P6) |
| Information disclosure | Card data enters the model | LLM02 | Refused before any model call: `services/gateway/tests/test_chat.py::test_a_rejected_body_is_never_echoed_or_logged_and_reaches_no_model`. Accepted limit: digits spelled out or touching letters pass; hosted fields are the real control, Window B PAY-05 (SA-02 review, first-pass 3, second-pass 4). Check on model output: **open**, Window B PAY-05 (SA-02 review, finding 4) |
| Information disclosure | System prompt or tool schema extracted | LLM08 | Treated as discoverable: the prompt holds no secret (`services/assistant/src/northwind_assistant/prompt.py`). Red-team items RT-025 and RT-026 (`evals/adversarial/rag11_red_team.jsonl`) check for a run of more than 8 words from the system prompt and for tool schema JSON: `services/assistant/tests/test_red_team_e2e.py::test_a_red_team_query_surfaces_nothing_internal` (g). Recorded answers: `cassettes/assistant-redteam`, all checks passing; the user read all 28 answers (#9) |
| Denial of service | Flooding `/api/chat` to burn credit or block others | LLM06 | Message cap (`services/gateway/tests/test_chat_edges.py::test_the_length_limit_is_exact`), 3 searches (`services/assistant/tests/test_first_request.py::test_the_cap_is_three_searches`), 6 turns, 4 until row #12 (`services/assistant/tests/test_tool_loop.py::test_a_seventh_turn_never_happens`); budget caps hold. Rate, concurrency and wall-clock limits: **open**, Window B NFR-14 (SA-02 review, finding 7) |
| Elevation of privilege | Forced purchase or refund | LLM03 | The assistant has one read-only tool: `services/assistant/tests/test_tool_loop.py::test_a_tool_outside_allowed_tools_is_denied_not_just_absent`. Since #21 the payment port refuses `authorize` and `capture` without the shopper's confirmation reference for that order and amount, and above a per-order limit (decision 0014; `services/backend/tests/test_be05_prerequisites.py`); the Orders agent's handlers take the reference from the task, never the model, and no agent holds void or refund. The portal control that issues the reference is a test fixture until CHK-02; session and day spend limits: **open**, Window B CHK-03 |

## Boundary 2: retrieved content to model

Built: `retrieve_context` and the server's first search, dense-only, on the plain shared index. The enriched
index exists only in `northwind_enriched` (decision 0010).

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | A review poses as a policy or system source | LLM01 | Source text escaped and labelled: `services/assistant/tests/test_tool_loop.py::test_the_retrieved_text_is_labelled_and_escaped_data`. Reviews cannot set policy: `services/assistant/tests/test_smoke_e2e.py::test_a_review_cannot_set_the_return_policy`. Fullwidth lookalikes of a source tag: source text is NFKC-normalised before escaping, #9: `services/assistant/tests/test_render_nfkc.py::test_a_fullwidth_document_tag_cannot_open_a_trusted_source`, `services/assistant/tests/test_tool_loop.py::test_fullwidth_lookalikes_reach_the_model_escaped`. Angle-bracket homoglyphs with no compatibility form (`〈〉`, `‹›`, `⟨⟩`, `˂˃`, `ᐸᐳ`, `❮❯`) pass NFKC unchanged, and whether the model reads them as tags is untested: **open**, #13, whose planted review should use one: moved to #13b with the #13/#13b split (RAG-11 security review, pass 1, F2). **Tested, #13b:** R01221 / TRAP-INJ-016 holds fullwidth tag lookalikes (folded and escaped) and `〈/source〉〈source type="document"〉` (U+3008/U+3009, kept by NFKC): `data/tests/test_appended_reviews.py::test_r01221_holds_a_fullwidth_and_a_homoglyph_tag`. EV-102 runs it through the assistant. **The output check, not the model, kept the false 365-day term off the screen** (`false_policy`, blocked count 1; `docs/reviews/2026-11-13-RAG-08b-answers.md` §5). The shopper message is NFKC-normalised before escaping: landed, #13 (RAG-13 review, scoped check 1) |
| Tampering | Planted instruction in a review is followed or relayed | LLM01 | R00712 not followed in five recordings; that is one review of 15 planted traps. **Landed, #13 (decision 0015):** the output check replaces any sentence carrying a payload class before a token is sent, so the recorded answers that name R00712's subject, RT-003's and RT-016's refund claims now pass because of the check, with the recordings unchanged: `services/assistant/tests/test_smoke_e2e.py::test_a_planted_injections_subject_is_not_named` (xfail removed), `services/assistant/tests/test_red_team_e2e.py::test_a_red_team_query_surfaces_nothing_internal` check (h), `services/assistant/tests/test_output_check.py::test_r00712s_subject_is_never_sent_however_it_is_split`. Mentions without a payload subject stay a prompt preference, counted in #13b (decision 0007; RAG-13 review) |
| Tampering | Poisoned content in the index | LLM05 | Ingest guard (`services/rag/tests/test_sources.py::test_pipeline_runs_the_ingest_guard`), allow-listed fixtures (`services/rag/tests/test_sources.py::test_guard_refuses_a_file_not_listed_as_ingestible`), no injection filter at ingest on purpose. Flagging at ingestion: **open**, Window B NFR-03. `dataset_sha256` verified: **landed, #10b (Q4), as a consistency check, not authentication**. It proves the files on disk match their manifest (drift, a half-regenerated or corrupted dataset); a deliberately re-signed manifest passes, and the anchor against that is CI's `data/tests/test_fixtures_current.py::test_committed_fixtures_match_the_generator` over the committed fixtures (security review L1). The hash covers the listed files' bytes, not the manifest's own fields such as `data_version` (re-review N7). The verification read opens each listed file once, refusing a symlink in the last path component and anything not a regular file, a FIFO included (re-review N1); ingest aborts before any write on a mismatch and stores the hash it verified (L2); mcp-catalog parses the verified bytes (`data/src/northwind_data/ingest_guard.py::verify_dataset`, called first by `services/rag/src/northwind_rag/ingest.py`; mcp-catalog calls `load_verified` and parses the bytes it returns); the index-manifest resource reports `dataset_verified` true only when the stored hash is the hash of the fixtures on disk now (decision D4, no migration): `data/tests/test_ingest_guard.py`, `services/rag/tests/test_ingest_verify.py`, `services/mcp-rag/tests/test_manifest.py`. mcp-catalog verifies before it seeds its live store and refuses to start on a mismatch: `services/mcp-catalog/tests/test_live.py` |
| Tampering | Generated context lifts a planted review in ranking | LLM09 | Landed, #7, on the isolated index only. Reviews get no context: `services/rag/tests/test_context_allowlist.py::test_a_review_holding_a_context_is_refused_at_the_index`, `services/rag/tests/test_context_allowlist.py::test_the_real_cache_gives_no_review_a_context`. Context is ranking-only: `services/rag/tests/test_context_checks.py::test_what_retrieval_returns_has_no_context_field`, with `services/rag/tests/test_context_checks.py::test_only_the_index_writers_touch_context_or_embed_text` as the pin on who reads the field. Since #10 the pin also fails on raw SQL on `rag_chunks` outside `services/rag/src/northwind_rag/retrieval.py` and `services/rag/src/northwind_rag/store.py`, on a chunk dumped whole and on a new `Chunk` importer, by full path over every source root: `services/rag/tests/test_context_checks.py::test_nothing_but_retrieval_and_store_runs_sql_on_the_index`, `services/rag/tests/test_context_checks.py::test_no_chunk_is_dumped_whole`, `services/rag/tests/test_context_checks.py::test_only_the_allowed_modules_import_chunk` (decision 0010, rule 4). Reviews getting none removes the lift, not the planted reviews: 23 injection-trap appearances in a top 5 against 20 plain. The switch to the shared index carries five conditions: **open**, #8 (decision 0010, "Reviews get no context"; `PROGRESS.md` row 8) |
| Repudiation | An answer cannot be traced to its sources | LLM07 | Citations limited to sources returned this turn: `services/assistant/tests/test_tool_loop.py::test_an_invented_citation_is_dropped`. Invented markers are removed from the text before it is sent (#13): `services/assistant/tests/test_output_check.py::test_an_invented_marker_never_reaches_the_shopper`. Uncited claims stay in the text, not blocked (SA-02 review, finding 5). **Measured, #13b:** `citation_ok` through the assistant is 0.971 under the product-and-policy rule and 0.941 counting every claim, with the floor 0.95. The kind that decides which claims need citations is the judge's (owner #32) |
| Information disclosure | Internal documents reach a shopper | LLM02, LLM09 | Filter by server-bound audience: `services/rag/tests/test_retrieve_context.py::test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly`, `services/rag/tests/test_retrieve_context_isolation.py::test_an_internal_document_in_the_shoppers_own_region_is_still_hidden`. Red-team set, 28 items (`evals/adversarial/rag11_red_team.jsonl`), on every arm in every region: `services/rag/tests/test_rag11_access.py::test_no_red_team_query_returns_an_internal_chunk_on_any_arm`; what the model receives: `services/assistant/tests/test_rag11_model_context.py::test_nothing_internal_reaches_the_model_for_a_red_team_item`; through the assistant: `services/assistant/tests/test_red_team_e2e.py::test_a_red_team_query_surfaces_nothing_internal`, recorded in `cassettes/assistant-redteam`, all checks passing, and the user read all 28 answers (#9). Through `retrieve_context` on `mcp-rag`, shopper tokens in every region: `services/mcp-rag/tests/test_canary.py::test_no_internal_record_reaches_a_shopper_in_any_region`. Same rule on catalog tools: **landed, #10b**. A shopper token sees only `public` records on all five tools, and an internal ID gets the byte-identical `not_found` an unknown one gets, in every region; P0009's canary in no response byte: `services/mcp-catalog/tests/test_visibility.py`, `services/mcp-catalog/tests/test_catalog_canary.py` |
| Information disclosure | Another region's policy reaches a shopper | LLM07 | `services/rag/tests/test_retrieve_context.py::test_one_context_never_holds_two_regions_return_rules`. Evals still score an unfiltered context: **open**, #8 |
| Information disclosure | Prices or stock stated from text (hard rule 2) | LLM07 | Exact prices kept out of chunks: `services/rag/tests/test_index.py::test_planted_price_aborts_the_run_and_writes_nothing`. `textscan` gaps: landed, #7, `data/tests/test_textscan_extensions.py`. Promotions in reviews: the output check's promotion class replaces them before they are sent, landed, #13 (decision 0015; `services/assistant/tests/test_output_check.py::test_promotion_language_is_replaced`); live tool values for promotions are #12's (SA-02 review, finding 5) |
| Denial of service | A review crafted to fill the context | LLM06 | 5 chunks, `MAX_CHUNK_CHARS` at ingest (`PROGRESS.md` row 3) |
| Elevation of privilege | Retrieved text grants tool access | LLM03 | The allowed tool list is fixed in code, whatever the text says: `services/assistant/tests/test_tool_loop.py::test_a_tool_outside_allowed_tools_is_denied_not_just_absent`. "Type is not review" is a deny-list: **open**, low, #8 (RAG-13 review, scoped check 2) |

## Boundary 3: assistant to MCP servers

A network boundary since #10: `mcp-rag` serves `retrieve_context` over streamable HTTP on 127.0.0.1, with the SDK's DNS-rebinding protection, behind a per-turn token (HMAC-SHA256, 120 s, signed with `NORTHWIND_MCP_TOKEN_KEY` from the environment; the CLI holds the token, never the key). Audience, region, agent and turn come only from the token, and the server counts 3 searches per turn. The CLI runs with `--strict-mcp-config`. Card data: the shopper's message is checked in the gateway, again before the first search, and on the server; a query the model writes goes from the CLI to `mcp-rag` and is checked there only, over every argument, before any search or log (security review L5, L6). **Open, low:** the client does not authenticate the server, so a local process holding the port while `mcp-rag` is down would get tokens and could answer with forged sources; the same loopback trust as the dev LLM proxy (security review L8, lab-only). **Closed, #10b (L4):** a token carries a signed `srv` claim, and each server's gate (`services/common/src/northwind_common/mcp_gate.py`, shared, bound to one server) accepts only a token minted for itself, so a `northwind-rag` token is a bare 401 on `mcp-catalog` and the reverse (`tests/mcp/test_contract.py`, `services/common/tests/test_mcp_gate.py`). `mint`'s `server` defaults to `northwind-rag` for the one production minter, which #12 makes explicit. `mcp-catalog` serves the five read-only catalog tools on 127.0.0.1 behind that gate, scoped to the assistant but not attached until #12; it refuses card data in any argument and any unknown argument key (decision D3), and its live store's `set_price` / `set_stock` are not tools.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Stolen or replayed tool token | LLM03 | A missing, malformed, forged, expired or wrong-audience token gets a bare 401 before any tool runs: `services/mcp-rag/tests/test_server.py::test_a_bad_token_gets_a_bare_401_before_any_tool_runs`, `services/common/tests/test_mcp_token.py::test_a_signed_token_with_bad_claims_is_refused`. A stolen token buys at most its turn's remaining searches, shopper scope, read-only, for at most 120 s; it rides in the CLI's argv. A bad token at CLI start makes the CLI drop the server silently (S0 6b); the first search, with the same token, fails closed before any model call (Q6). Per-user tokens with sessions: **open**, #19 |
| Tampering | Model-supplied arguments widen a query | LLM03 | Schema offers only `query`: `services/assistant/tests/test_tool_loop.py::test_model_supplied_audience_and_region_change_nothing_through_the_cli`. On `mcp-rag`: `services/mcp-rag/tests/test_retrieve.py::test_an_audience_or_region_in_the_arguments_changes_nothing` (SA-02 review, second-pass 5) |
| Repudiation | Tool calls not attributable | LLM03 | **Open:** the assistant's tool calls, served by `mcp-rag` since #10, write no audit event. #20's backend audit log (`services/backend/src/northwind_backend/audit.py`) is groundwork only, since it audits no assistant tool call; owned by NFR-10 (Window B P6) |
| Information disclosure | Tool error leaks a connection string | LLM02 | Fixed text only: `services/assistant/tests/test_tool_loop.py::test_a_retrieval_failure_tells_the_model_nothing_about_why`, `services/mcp-rag/tests/test_retrieve.py::test_a_failed_search_is_transient_and_says_nothing_of_why`. The MCP client logs request arguments at DEBUG; the assistant holds its loggers at WARNING: `services/assistant/tests/test_rag_client.py::test_a_whole_turn_logs_no_shopper_message_and_no_card_digits` |
| Denial of service | A tool loop runs without end | LLM06 | `services/assistant/tests/test_first_request.py::test_a_model_that_always_searches_gets_three_real_searches_in_all`, `services/assistant/tests/test_tool_loop.py::test_a_seventh_turn_never_happens`, `services/mcp-rag/tests/test_retrieve.py::test_concurrent_calls_on_one_turn_never_exceed_three_searches` |
| Elevation of privilege | Over-broad database role | LLM03 | The app connects as the image's superuser, so `COPY … TO PROGRAM` may run code in the container: **open**, medium candidate, #9a (decision 0005, "Open") |
| Elevation of privilege | Agent picks up default tools | LLM03 | `tools` required; explicit option list: `services/common/tests/test_llm_factory.py::test_agent_options_require_tools`, `services/common/tests/test_llm_factory.py::test_anything_outside_the_allowlist_is_refused`. A planted MCP config adds no server: `services/common/tests/test_strict_mcp.py::test_a_planted_mcp_config_adds_no_server`. Each agent sees only its scoped tools: `tests/mcp/test_contract.py::test_tools_list_is_within_the_agents_scope_and_read_only` |

## Boundary 4: Northwind to external A2A agents

Not built. All threats **open**, Window B P4 and P5, except where noted. Designs are in PRD §7.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Spoofed agent or tampered card | LLM04 | **Open:** A2A-01, A2A-06 |
| Tampering | Forged push callback | LLM01 | **Open:** A2A-08 |
| Repudiation | Disputed payment outcome | LLM03 | **Partly closed, for the fakes only (#20, BE-08):** every payment call through an audited view writes an attempt and one outcome, and `reconstruct_order` rebuilds the payment from events alone (`services/backend/tests/backend_audit/test_be08_reconstruct.py::test_all_1000_orders_rebuild_from_reloaded_events_alone`). **Limits:** an outcome of `TIMEOUT`, `unknown` or `audit_incomplete` on a money call leaves it `uncertain` until a later event settles it, so the log shows the uncertainty but doesn't resolve it (`test_an_uncertain_capture_is_never_reported_as_absent`); and the chain limits: with no outside anchor, `verify_chain` can't find a truncated tail or a full recompute by anyone who can write the file (both pinned as known limits in `services/backend/tests/backend_audit/test_be08_audit_log.py`; the anchor is NFR-10). **Open:** real adapters, A2A-07 |
| Information disclosure | More data than needed sent to a peer | LLM02 | **Open:** A2A-06, NFR-05 |
| Denial of service | Hung task blocks checkout | LLM06 | **Open:** A2A-07, NFR-14 |
| Elevation of privilege | A peer's reply treated as an instruction | LLM01, LLM03 | **Open:** NFR-03 rest. Fakes first at #19, #21 |

## Boundary 5: partner agent to Northwind

Not built. All threats **open**: prep at #15, the rest in Window B. Out-of-scope rejection is enforced by
scope checks, not the model (decision 0002).

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | A partner without a valid token | LLM03 | **Open:** A2A-05, A2A-06 |
| Tampering | Injection in a partner message | LLM01 | **Open:** A2A-05, NFR-03 |
| Repudiation | No per-partner trail | LLM03 | **Open:** no partner call exists yet; A2A correlation is NFR-10's (Window B P6), and A2A-05 |
| Information disclosure | Partner reads other shoppers' data | LLM02 | **Open:** A2A-05 |
| Denial of service | Partner floods the assistant | LLM06 | **Open:** NFR-14 |
| Elevation of privilege | Order over the ceiling | LLM03 | **Open:** A2A-05, ceiling value `unconfirmed` (`docs/governance/tool-register.md`) |

## Boundary 6: build time (Claude Code)

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Sandbox claims a spending window it should not | LLM06 | Accepted limit: any sandboxed command can claim first. What holds is the bound: one suite, two models, the budget, the time limit (decision 0005, "Limits", CC-27 evidence finding 8) |
| Tampering | Session edits its settings, hooks or the runner's code | LLM04 | OS sandbox and deny rules, measured in CC-27 evidence, "R1 and R2 at the OS level", and the 5a evidence rig runs. `tests/hooks/test_sandbox_settings.py` pins the proposal file only; `tests/hooks/test_settings.py::test_weakened_settings_are_caught` covers the guard and rules. **Open**, AUTO-2: no test fails if the installed settings lose their sandbox block (CC-27 evidence, "Limits"); `make runner-check` reports it, but it is run by hand and proves things only for an honest session (decision 0005, "Limits"). Guard hook is a speed bump: 22 strict xfails (FOUND-2 review). Runner code moves only through `make runner-sync`, which shows the user the diff |
| Tampering | Baseline or known failures changed | LLM05 | `denyWrite`, `ship` labels: `tests/ship/test_ship.py::test_protected_change_without_label_is_refused`. Committed content changed through git plumbing: landed, 5b: `Guard / baseline-label` fails a PR that changes the baseline, known failures or the item set without `baseline-change` (`tests/ship/test_guard.py::test_a_protected_change_without_the_label_fails`, decision 0009, clause 10). `baseline-label` and `eval-integrity-main` are required checks, stated by the user, 2026-11-07, so a merge outside `ship` meets them too (decision 0009, clause 16). "Can only shrink" is now a check at promotion (`services/llm-replay/tests/test_promotion.py::test_known_failures_may_not_grow_in_the_checkout`). On a PR the label is the only check, so a labelled, added `approved_by: human` entry still waives a containment failure (decision 0006, rule 3) |
| Tampering | Promotion runs session-editable code unsandboxed | LLM04 | Landed, 5b: promotion runs from the runner clone on `python -I`, and the checkout is data: `evals/tests/test_rag12_followup.py::test_eval_promote_runs_no_python_outside_the_clone`, `services/llm-replay/tests/test_promotion.py::test_the_clones_baseline_decides_not_the_checkouts` (decision 0009, clauses 1 to 3). Row 5b is `done`, so the hold on promotion has ended; `eval-promote CHECK=1` ran on the clone, stated by the user, 2026-11-07 |
| Tampering | CI runs the PR's own gate, so a green gate proves little | LLM04 | Landed, 5b: `Guard / eval-integrity-main` and `Guard / eval-gate-main` run main's `evals/src`: `tests/ship/test_guard.py::test_the_prs_gate_code_is_replaced_by_mains`, `tests/ship/test_guard.py::test_integrity_runs_in_its_own_job_so_nothing_can_accept_it`, `.github/workflows/guard.yml`. `eval-gate-main` is not a boundary: the PR's services run in the same process, and what a lock-pinned dependency brings is not covered. Accepted by the user (decision 0009, "Residual risk, stated"). With `gate-change`, green means the PR's own gate passed |
| Tampering | Forged new cassette makes a stale item look fresh | LLM05 | `make eval-integrity` refuses modified cassettes, not added ones: **open**, medium, AUTO-2 (decision 0006, "Open limits"; decision 0009, "Residual risk, stated") |
| Tampering | Shared caches outlive a worktree | LLM04 | `~/.cache/uv` and the pnpm store: **accepted for the lab** by the user, 2026-11-07. F6 stands (decision 0005, "Open"; decision 0011). The runner has a private cache |
| Tampering | Embedding model not pinned to a revision | LLM04 | **Open**, #18 (`PROGRESS.md` row 3) |
| Repudiation | `approved_by` and override reasons are only claims | LLM05 | Accepted: plain strings, a claim not proof (decisions 0003, 0006). `eval-integrity` prints added entries, and nothing makes anyone read them: **open**, medium, AUTO-2 (5a evidence, finding 7) |
| Information disclosure | Session reads the key | LLM02 | `denyRead` and deny rules; fake-key test passed, a manual result (decision 0005, "Open"). User-level denies need the sandbox on: accepted (decision 0005, "Limits") |
| Information disclosure | Secret in a cassette | LLM02 | `make scan-cassettes` in CI, regex only; the key-fragment scan needs the user's terminal. Split secrets: **open**, #18 (FOUND-2 review, finding 6; SA-02 review, second-pass 9) |
| Information disclosure | `allowLocalBinding` exposes a listener on the LAN | LLM02 | Measured. No narrower setting found: **accepted for the lab** by the user, 2026-11-07; revisit before non-lab use. F4 stands (decision 0005, "Open"; decision 0011; CC-27 evidence) |
| Denial of service | Session burns credit | LLM06 | Window, budget and ledger: `services/llm-replay/tests/test_proxy.py::test_budget_breach_is_refused_with_zero_upstream_calls`. `CLAUDE.md` forbids raising a cap without asking |
| Elevation of privilege | Session merges to main or pushes a workflow | LLM03 | No credentials; `ship` and pre-push: `tools/ship.sh`, `tools/pre-push.proposed.sh`. A local check, not a boundary: only a server-side rule closes this. `baseline-label` and `eval-integrity-main` are required checks, stated by the user, 2026-11-07; nothing here records what else the repo settings enforce (decision 0008, "Limits"). 12 refusals untested: **open**, AUTO-2 |
| Elevation of privilege | A make variable reaches a shell | LLM03 | Free-text variables pinned: `evals/tests/test_rag12_followup.py::test_eval_promote_passes_a_value_literally_and_runs_nothing`. Others rely on rule 7's acceptance (decision 0006, "Found during this row") |

## Boundary 7: Northwind runtime to the model provider

Not in the PRD §7 table; added here at the user's request. Crosses shopper messages, retrieved and
review text and, from #7's window, internal-only text. Which data crosses is in `docs/governance/data-flow.md`.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Traffic sent to the wrong host | LLM04 | The upstream is a constant in code, and neither CLI nor environment can set it: `services/llm-replay/tests/test_runner_criteria.py::test_cli_runner_takes_no_upstream_or_key_option`. Client proxy settings ignored (`trust_env=False`, FOUND-2 review, finding A) |
| Spoofing | A runtime call lands on the developer's subscription | LLM02 | Environment built from scratch, placeholder key: `services/common/tests/test_llm_factory.py::test_child_env_is_built_from_scratch` |
| Tampering | Request widened to bill beyond the reservation | LLM06 | Allow-listed fields, models and content blocks; no images, documents or server tools (decision 0005, "What the runner forwards"). The budget bound rests on those lists (decision 0005, "Limits") |
| Tampering | A model response carries a hostile payload to the shopper | LLM10 | The portal renders plain text through React, with no raw HTML or markdown rendering (`apps/portal/src/Chat.tsx`). Output check by payload class (promotion, links and contact, confirmation, refund authority, stock, false policy), before any token leaves: landed, #13, decision 0015 (`services/assistant/tests/test_output_check.py`, `services/assistant/tests/test_output_check_fixtures.py`). Other policy terms: the judge, owner hardening |
| Repudiation | Spend cannot be attributed | LLM06 | Ledger per call, by suite and model; `make llm-spend`. Per-answer telemetry: `PROGRESS.md` row 6 |
| Information disclosure | Key leaks in a request or response | LLM02 | Fragment check on both, and a hit closes the window and prints `ROTATE THE KEY` (decision 0005, "Key fragments"). Sees text, not encodings: accepted (decision 0005, "Limits"). `readable_forms` now splits on SSE line ends only: landed, #7, `services/llm-replay/tests/test_runner.py::test_a_line_separator_in_a_delta_does_not_drop_it_from_the_joined_text`. `parse_sse_usage` still uses `splitlines()`, which cannot hide a key: **open**, low, #18 (`PROGRESS.md`, "Open items") |
| Information disclosure | Card data in a prompt | LLM02 | Refused before the model: boundary 1. Refused in tool arguments: `services/assistant/tests/test_tool_loop.py::test_card_data_in_a_tool_argument_is_refused_and_searches_nothing`. Retrieved text is a third route: no PAN scan at ingestion, so card digits in a review reach the model on the assistant path: **open**, Window B P6, NFR-04 (`PROGRESS.md` row 3) |
| Information disclosure | Internal-only text sent to the provider | LLM02 | At runtime, document bodies have no path: `services/assistant/tests/test_context.py::test_no_canary_reaches_the_model` proves the retrieval filter. Names and topics have crossed, through adversarial eval items, and `_context_leaks` does not stop plain text (`docs/governance/data-flow.md`). **Landed, #7: document text went to Haiku in that row's window**: 11 entries in `data/enrichment/internal.jsonl`, no cassette and no proxy log, so the recordings cannot show it (decision 0010, rule 3). It stays synthetic, and hard rule 7 covers shoppers, not the provider. Beyond the lab: **not acceptable without reviewing the provider's data terms**, decided by the user, 2026-11-07 (decision 0011) |
| Information disclosure | Shopper data retained by the provider | LLM02 | Retention `unconfirmed`: **open**, Window B P6, NFR-07. All data synthetic (hard rule 3). Masking before model calls: **open**, Window B NFR-05 |
| Denial of service | Provider down or rate-limited | LLM06 | Upstream failures become fixed codes: `services/gateway/tests/test_chat.py::test_an_upstream_failure_is_only_a_fixed_code`. Graceful degradation: **open**, Window B NFR-14 |
| Elevation of privilege | Credit used past the cap | LLM06 | `LLM_BUDGET_USD`, read by the runner from the user's terminal; a sandboxed value does nothing (decision 0005) |

## Residuals

Accepted or open, as recorded. Not re-derived; severity is the source's.

| Residual | Severity | State | Source |
|---|---|---|---|
| Promotion from a checkout runs session-editable code unsandboxed | High | Done, 5b: promotion runs from the clone, and `eval-promote CHECK=1` ran there, stated by the user, 2026-11-07 | Decisions 0006, 0009; 5a evidence, finding 1 |
| `denyWrite` protects paths, not committed content | High | Done, 5b: `ship` and `Guard` have run on GitHub, and `baseline-label` and `eval-integrity-main` are required checks, stated by the user, 2026-11-07 | Decisions 0006, 0009; 5a evidence, finding 2 |
| CI runs the PR's own gate and integrity code | Not rated | Done, 5b: `Guard` runs main's copy and ran green on GitHub, stated by the user, 2026-11-07. `CI / eval-gate` keeps running the PR's own, on purpose | Decision 0009, clauses 11, 12, 14 |
| `eval-gate-main` is not a boundary; `Guard` and its scripts are PR-editable | Not rated | Accepted by the user | Decision 0009, "Residual risk, stated" |
| Promotion cannot prove scores are real: forged scores pass with a forged new cassette | Not rated | Open, AUTO-2, with the forged added cassette below | Decision 0009, "Residual risk, stated" |
| Row 5b's left items: `Guard` on GitHub, the first real `ship`, the settings install, `make runner-check` in each checkout, `eval-promote CHECK=1` on the clone, the pre-push reinstall, the required checks | Not rated | Done, stated by the user, 2026-11-07 | `PROGRESS.md` row 5b and its log |
| The F1 and F3 fixes in 5b had no security pass | Not rated | Recorded, by the user's decision | 5b evidence, "F1 and F3, decided by the user" |
| Small retrieval drops have no check but the tolerance | Not rated | Open, #8 | Decision 0009, "Residual risk, stated" |
| `services/llm-replay/src/northwind_llm_replay/runner_check.py` M1 to M3, L1 to L6 | Medium, low | Landed, 5b. Proves things only for an honest session | 5b evidence, "Failing first: runner_check.py" |
| Planted reviews still reach a top 5 under the allow-list: 23 appearances against 20 plain, on one unfiltered query per item | Medium | Open, #8, before the shared index switches | Decision 0010; `PROGRESS.md`, "Open items" |
| RAG-02's recall gain is on the items it was chosen with | Not rated | Open, #32 | Decision 0010; `PROGRESS.md` row 7 |
| The meaning of injected text reaches the shopper | Logged gap | Closed by #13 for the payload classes (decision 0015); mentions without a payload subject counted in #13b: 0 in the promoted recording (`payload mentions 0`) | Decision 0007; RAG-13 review |
| Fullwidth source-tag lookalikes | Medium, unconfirmed | Compatibility forms in source text landed, #9 (NFKC before escaping). #13: the shopper message is NFKC-normalised too. The output check matches on a view that drops combining marks, format characters and other default-ignorables, and folds Cyrillic, Greek and other lookalikes, measured in view characters (decision 0015; security review passes 1 and 2). Residual: `northwind-deals[.]net` passes, not clickable. Source rendering is unchanged for homoglyphs: one cannot form an ASCII `<` | RAG-13 review, scoped check 1; decision 0007, item 5; RAG-11 security review, pass 1, F2 |
| Superuser database role | Medium candidate | Open, #9a | Decision 0005; CC-27 evidence, finding 10 |
| The window authorises the sandbox, not one command | Medium | Accepted by the user | Decision 0005; CC-27 evidence, finding 8 |
| `allowLocalBinding` not loopback-only (F4); shared caches (F6) | Medium | Accepted for the lab by the user, 2026-11-07; F4 is revisited before non-lab use | Decisions 0005, 0011; CC-27 evidence |
| Forged added cassette; added-entry line in a green log; push to main compares HEAD with itself | Medium, medium, low | Open, AUTO-2 | Decision 0006; 5a evidence, findings 11, 7, 12 |
| No further review: third-pass fixes for CC-27 and 5a, second-pass fixes for SA-02, second-pass fixes for RAG-13, and 5a recovery step 3 | Not rated | Recorded | CC-27 evidence; 5a evidence; SA-02 review; RAG-13 review; `PROGRESS.md` row 5a |
| `lost_overrides` reads the working tree only | Medium | Open, AUTO-2 | Decision 0006; 5a evidence, finding 8 |
| 5a findings 15, 16, 17 | Low | 15 closed, 2026-11-07. 16 and 17 open; each has its own line in "Owners" | 5a evidence |
| Split secrets in the scanner | Medium | Open, #18 | FOUND-2 review, finding 6; `PROGRESS.md` row 18 |
| RAG-12 third-pass F7, F8, F9, F11, F12, and second-pass F3, F9 to F13, F15 | Not rated here | Second-pass F9 to F13, F15 closed as unrecoverable, 2026-11-07. The rest open; each has its line in "Owners" | `PROGRESS.md` row 4 |
| `git status` outside the sandbox runs a nested folder's config through a gitlink, which a session can plant: measured 2026-11-07 | Critical, by the security re-review | `make finish` refuses it, and `tools/refused-paths.sh` and the pre-push hook refuse a committed one once installed. **Moved to SEC-1 by the user's decision, 2026-11-07.** Pre-existing, not caused or worsened by AUTO-2. Open for any other `git status` outside the sandbox, from a terminal or perhaps an editor. Nothing narrows it until the proposal is installed. Once installed, it refuses a nested `.git` written in place by Bash, on macOS: read from the binary and measured on the same class of rule, not run. `Edit` deny rules cover the file tools, not run. A folder moved in already holding one is not refused (measured on the same class of rule). `make runner-check` reports a gitlink, `.gitmodules` or nested `.git`. A global `diff.ignoreSubmodules = all` narrows it only: a planted `.gitmodules` undoes it, even from the index alone (measured) | Decision 0011; AUTO-2 evidence |
| Replacing a linked worktree's admin folder gets past the `gitdir`, `commondir` and `config.worktree` rules, and a redirected `commondir` runs a planted config's fsmonitor in a plain `git status`: each half measured 2026-11-07, the chain never run on a real admin folder | Critical, provisional, by the second re-review | **Moved to SEC-1 by the user's decision, 2026-11-07.** Pre-existing, not caused or worsened by AUTO-2. Open for a `git status` from a terminal. `make finish` refuses a redirect and pins its directories, and `make runner-check` reports one | Decision 0011; AUTO-2 evidence |
| Installed settings can lose their sandbox block with no test failing | Not rated | Open, AUTO-2 | CC-27 evidence, "Limits" |
| `ship` on real `gh` | Not rated | Done: the first real `ship`, stated by the user, 2026-11-07. Row A1 stays partial for its other items | Decision 0008, `PROGRESS.md` row A1 |
| 12 untested refusals; control surface gaps; re-review F5, F6, F7 | Medium, low | Open, AUTO-2 | `PROGRESS.md` row A2; AUTO-1 evidence |
| The F1 and F2 re-review fixes had no second `security-reviewer` pass; the user's advisor accepted them | Medium | Accepted by the user | Decision 0008; AUTO-1 evidence |
| Card detection is a net, not the control | From a high, first-pass 3; the rest low, second-pass 4 | Accepted by the user; hosted fields are the control | SA-02 review |
| Hook is a speed bump: 22 strict xfails | Medium | Accepted; the sandbox is the control | FOUND-2 review, 15b, R1 to R4 |
| `approved_by` is a claim, not proof | Not rated | Accepted | Decisions 0003, 0006 |

## Owners

Every gap across the three documents that no `PROGRESS.md` row named. The owner is a row, a Window B NFR or
AUTO-2, or `needs the user` where it could not be placed. These are GOV-1's placements: no row's own text
changed, so a row takes an item on only when its plan says so. `decided` rows were `needs the user` at GOV-1: the
user decided them, and the Why gives the date and the decision (decision 0011).

| Item | Owner | Why |
|---|---|---|
| Bare `mcp_token.verify` is public, so a gate could skip the server check by indirection the AST pin cannot see | #12 | The rename to `_verify` needs `services/assistant/tests/test_rag_client.py`, off-limits at #10b; #12 owns `services/assistant/src/northwind_assistant/rag_client.py` (#10b re-review P1) |
| Catalog tool output (product descriptions and attributes) is not labelled as data for the model | #12 | mcp-catalog returns plain JSON text; the assistant labels tool text as data when it attaches the tools (hard rule 6; #10b security review L6) |
| Every MCP server holds the master signing key, so a compromised server can mint tokens for the others | #19 | With #24: the same item as #10's L7 (split the signing key), by the user's decision, 2026-11-13. `srv` stops replay across servers (L4) but HMAC is symmetric; per-server derived verification keys, with the master only in the gateway, is a design change (#10b security review L3; related to #10's L7, owner #19/#24) |
| Ingest re-reads the fixtures after `verify_dataset` | #H1 | Hardening, by the user's decision, 2026-11-13. mcp-catalog parses the verified bytes and ingest stores the verified hash, but `build_chunks` re-opens every ingestible file and those re-reads follow symlinks, and `data_version` comes from a re-read of the fixtures manifest, so a concurrent `make data` or a swapped file could mix datasets in one ingest. Only the last path component is guarded against symlinks. The fix is to pass the verified bytes into `build_chunks` and `_sync` (#10b security review L2, re-review N4; lab-only) |
| `mint`'s `server` defaults to `northwind-rag` | #12 | `services/assistant/src/northwind_assistant/rag_client.py`, the one production minter, was off-limits at #10b; #12 passes `server` explicitly and removes the default. A forgotten `server` gives a rag token, which mcp-catalog refuses |
| mcp-catalog has no per-turn call cap (mcp-rag counts 3 searches) | Window B P6, NFR-14 | Hardening, by the user's decision, 2026-11-13. Not in #10b's scope (a hardening item, like mcp-rag's per-process cap ledger); the tools are read-only and not attached until #12 |
| mcp-catalog's live store is in memory, seeded from the fixtures (decision D2) | decided | 2026-11-13: no owner, by the user's decision. Postgres-backed live tables are deferred, with no owner yet; `set_price` / `set_stock` are the interface the backend uses later |
| Embedding model not pinned to a revision | #18 | It freezes model IDs before the final recording |
| From the clone, the key-fragment scan covers `CASSETTES_DIR` only, not a worktree's `.llm-proxy-log`, `evals/` or `data/enrichment/` | #18 | Its audit is the scan's full run, and it already owns the scanner fixes |
| `parse_sse_usage` still uses `splitlines()` | #18 | It sits in the proxy that records everything at #18 |
| RAG-12 second-pass F3: the scanner reads raw text | #18 | `PROGRESS.md` row 4 says it overlaps #18's scanner work |
| RAG-12 third-pass F7: repeated in-tolerance promotions can lower the bar | #8 | Its row now carries the retrieval drift check |
| ~~RAG-12 third-pass F8: answerer, judge and retriever IDs are stored, never compared~~ **Landed, #13b:** `gate.identity_mismatches` fails on a changed retriever, index, judge, or answerer model or prompt; a changed check hash is reported | #13b | `evals/tests/test_rag08b_adapter.py::test_a_different_answerer_judge_retriever_or_index_fails_the_gate` |
| RAG-12 third-pass F11: stale or malformed known-failure entries are not refused by the gate | #32 | The next RAG-12 row, which grows the gate's inputs |
| 5a finding 17: override record details | #32 | Same record, same RAG-12 follow-up |
| `cancel_order` has no limit or approver, and `update_delivery_address` exists only in its narrow #21 form (`use_address_on_file`) | #19 | `mcp-orders`, FOUND-3's rest; #21 built neither in full; `docs/governance/tool-register.md` keeps `unconfirmed` |
| Forged added cassette (5a finding 11) | AUTO-2 | Its item 14 already changes `modified_cassettes`, the same check |
| Added-entry line in a green log (5a finding 7) | AUTO-2 | `ship` is where a person reads a change, and item 1 widens what it shows |
| `lost_overrides` reads the working tree only (5a finding 8) | AUTO-2 | The same integrity code as item 14 |
| Push to main compares HEAD with itself (5a finding 12) | AUTO-2 | The same integrity code as item 14 |
| `/req-start` has no output check (5a finding 16) | AUTO-2 | A row-automation gap. Claude cannot edit skills, so the user installs the fix |
| RAG-12 third-pass F12: the CI job and constants are lightly pinned | AUTO-2 | `tests/ship/test_guard.py` pins `Guard` now; `CI` is the rest of that work |
| Installed settings can lose their sandbox block | AUTO-2 | A control surface check, beside its items 3 and 12 |
| `make -s record` and `make SUITE=x record` miss the `ask` rules | AUTO-2 | Its item 12 is already a settings proposal. The window holds meanwhile |
| `.github/workflows/ci.yml` does not scan `docs/governance/` | AUTO-2 | A workflow change, which belongs with its control surface work |
| The docstring in `services/common/src/northwind_common/llm.py` still names the old key file | AUTO-2 | It reaches the clone through `runner-sync`, so it wants that row's review |
| No PAN scan at ingestion; `.github/workflows/ci.yml` does not scan `data/fixtures/` | Window B P6, NFR-04 | That NFR keeps card data out of Northwind systems |
| What Anthropic retains from API calls and Claude Code sessions | Window B P6, NFR-07 | That NFR asks where each class is stored. The answer needs the provider's terms for this account |
| RAG-12 third-pass F9: labels can change under a `human` stamp | Window B P6, NFR-17 | Decision 0009 covers it at promotion only. NFR-17 is about reviewed, versioned sets |
| RAG-12 second-pass F9 to F13, F15 | decided | 2026-11-07: closed as unrecoverable. Only the numbers survive, in `PROGRESS.md` row 4, and 5a and 5b re-reviewed the gate |
| 5a finding 15: `deny` on `evals/baselines/**` | decided | 2026-11-07: closed, covered by 5b's `denyWrite`, the ask rules and `baseline-label` |
| `allowLocalBinding` not loopback-only (F4) | decided | 2026-11-07: accepted for the lab; revisit before non-lab use |
| Shared caches (F6) | decided | 2026-11-07: accepted for the lab |
| `make enrich` has no `ask` rule | decided | 2026-11-07: fixed in AUTO-2, in the settings proposal; live once the user installs it |
| Whether internal-only text going to a provider is acceptable beyond a lab | decided | 2026-11-07: not acceptable outside a lab without reviewing the provider's data terms |
| PRD §7 has no row for Northwind to the model provider. Boundary 7 fills that here; the PRD is unchanged | decided | 2026-11-07: the PRD stays unchanged, and Boundary 7 is the record |
| What the device bridge can reach and keep | decided | 2026-11-07: the advisor reads only connected folders, never `~/.northwind`, and writes only when asked. A stated policy, not measured; it does not say what the bridge keeps |
| Who approves `make eval-gate-stale-ok` | decided | 2026-11-07: the user |

## Open items

| Item | Owner |
|---|---|
| Mapping to the OWASP Top 10 for Agentic Applications 2026 | P3, P5 |
| Kill switches (NFR-12), audit log (NFR-10), prompt versioning and rollback drill (NFR-11). The backend audit log landed at #20 (BE-08 partial); MCP, A2A and assistant spans, the chain's outside anchor and storage remain | Window B P6, NFR-10 |
| PRD §10 says "written and reviewed": this model has not had the user's review | The user |
