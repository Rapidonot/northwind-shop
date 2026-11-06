# Threat model (PRD §10)

Assets, actors, and STRIDE across each trust boundary, mapped to the OWASP Top 10 for LLM Applications.
Each threat has either a control that exists today, with its evidence, or `open` with its owner. What no
`PROGRESS.md` row named an owner for is in "Owners", below. Residuals are taken from the reviews and
decisions and cited, not re-derived.

**Baseline:** main at `6f920a1`, 2026-11-06, with 5b (decision 0009) and #7 (decision 0010) landed. Landed
is not done: both rows are `partial` in `PROGRESS.md`, and the cells below say what is left. Rows are
`PROGRESS.md` rows. A boundary with nothing built says so: its threats are `open` by design, not by
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
| Tampering | Prompt injection in the shopper's message | LLM01 | Message escaped inside its own block: `services/assistant/tests/test_first_request.py::test_hostile_source_and_shopper_text_stay_inside_their_own_blocks`. No state-changing tool exists to abuse: `docs/governance/tool-register.md`. Deterministic output check: **open**, #13 |
| Tampering | Attached file or slash command in a message | LLM01 | `services/assistant/tests/test_tool_loop.py::test_a_shopper_cannot_attach_a_file_with_an_at_path`, `services/assistant/tests/test_tool_loop.py::test_a_slash_command_stays_shopper_text` |
| Repudiation | No record of who asked what | LLM03 | **Open:** no audit log by design at this stage, #20 (BE-08, NFR-10) |
| Information disclosure | Card data enters the model | LLM02 | Refused before any model call: `services/gateway/tests/test_chat.py::test_a_rejected_body_is_never_echoed_or_logged_and_reaches_no_model`. Accepted limit: digits spelled out or touching letters pass; hosted fields are the real control, Window B PAY-05 (SA-02 review, first-pass 3, second-pass 4). Check on model output: **open**, Window B PAY-05 (SA-02 review, finding 4) |
| Information disclosure | System prompt or tool schema extracted | LLM08 | Treated as discoverable: the prompt holds no secret (`services/assistant/src/northwind_assistant/prompt.py`). No test measures extraction: **open**, #9 red-team |
| Denial of service | Flooding `/api/chat` to burn credit or block others | LLM06 | Message cap (`services/gateway/tests/test_chat_edges.py::test_the_length_limit_is_exact`), 3 searches (`services/assistant/tests/test_first_request.py::test_the_cap_is_three_searches`), 4 turns (`services/assistant/tests/test_tool_loop.py::test_a_fifth_turn_never_happens`); budget caps hold. Rate, concurrency and wall-clock limits: **open**, Window B NFR-14 (SA-02 review, finding 7) |
| Elevation of privilege | Forced purchase or refund | LLM03 | The assistant has one read-only tool: `services/assistant/tests/test_tool_loop.py::test_a_tool_outside_allowed_tools_is_denied_not_just_absent`. Portal confirmation and spend limits: **open**, Window B CHK-02, CHK-03 |

## Boundary 2: retrieved content to model

Built: `retrieve_context` and the server's first search, dense-only, on the plain shared index. The enriched
index exists only in `northwind_enriched` (decision 0010).

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | A review poses as a policy or system source | LLM01 | Source text escaped and labelled: `services/assistant/tests/test_tool_loop.py::test_the_retrieved_text_is_labelled_and_escaped_data`. Reviews cannot set policy: `services/assistant/tests/test_smoke_e2e.py::test_a_review_cannot_set_the_return_policy`. Fullwidth lookalikes of a source tag: **open**, medium, unconfirmed, #9 (NFKC) and #13 (RAG-13 review, scoped check 1) |
| Tampering | Planted instruction in a review is followed or relayed | LLM01 | R00712 not followed in four recordings; that is one review of 15 planted traps. **Open:** its meaning still reaches the shopper; the test is a strict xfail, and the output check is the primary control, #13 (decision 0007; RAG-13 review) |
| Tampering | Poisoned content in the index | LLM05 | Ingest guard (`services/rag/tests/test_sources.py::test_pipeline_runs_the_ingest_guard`), allow-listed fixtures (`services/rag/tests/test_sources.py::test_guard_refuses_a_file_not_listed_as_ingestible`), no injection filter at ingest on purpose. Flagging at ingestion: **open**, Window B NFR-03. `dataset_sha256` copied but not verified: **open**, #10 (`PROGRESS.md` row 3) |
| Tampering | Generated context lifts a planted review in ranking | LLM09 | Landed, #7, on the isolated index only. Reviews get no context: `services/rag/tests/test_context_allowlist.py::test_a_review_holding_a_context_is_refused_at_the_index`, `services/rag/tests/test_context_allowlist.py::test_the_real_cache_gives_no_review_a_context`. Context is ranking-only: `services/rag/tests/test_context_checks.py::test_what_retrieval_returns_has_no_context_field`. That removes the lift, not the planted reviews: 23 injection-trap appearances in a top 5 against 20 plain. The switch to the shared index carries five conditions: **open**, #8 (decision 0010, "Reviews get no context"; `PROGRESS.md` row 8) |
| Repudiation | An answer cannot be traced to its sources | LLM07 | Citations limited to sources returned this turn: `services/assistant/tests/test_tool_loop.py::test_an_invented_citation_is_dropped`. Uncited claims stay in the text: **open**, #13 (SA-02 review, finding 5) |
| Information disclosure | Internal documents reach a shopper | LLM02, LLM09 | Filter by server-bound audience: `services/rag/tests/test_retrieve_context.py::test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly`, `services/rag/tests/test_retrieve_context_isolation.py::test_an_internal_document_in_the_shoppers_own_region_is_still_hidden`. Red-team set: **open**, #9. Same rule on catalog tools: **open**, #10 |
| Information disclosure | Another region's policy reaches a shopper | LLM07 | `services/rag/tests/test_retrieve_context.py::test_one_context_never_holds_two_regions_return_rules`. Evals still score an unfiltered context: **open**, #8 |
| Information disclosure | Prices or stock stated from text (hard rule 2) | LLM07 | Exact prices kept out of chunks: `services/rag/tests/test_index.py::test_planted_price_aborts_the_run_and_writes_nothing`. `textscan` gaps: landed, #7, `data/tests/test_textscan_extensions.py`. Promotions in reviews rest on the prompt: **open**, #12 (SA-02 review, finding 5) |
| Denial of service | A review crafted to fill the context | LLM06 | 5 chunks, `MAX_CHUNK_CHARS` at ingest (`PROGRESS.md` row 3) |
| Elevation of privilege | Retrieved text grants tool access | LLM03 | The allowed tool list is fixed in code, whatever the text says: `services/assistant/tests/test_tool_loop.py::test_a_tool_outside_allowed_tools_is_denied_not_just_absent`. "Type is not review" is a deny-list: **open**, low, #8 (RAG-13 review, scoped check 2) |

## Boundary 3: assistant to MCP servers

Not built as a network boundary: the only server is in-process. `mcp-catalog` and `mcp-rag` arrive at #10.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Stolen or replayed tool token | LLM03 | No tokens exist. Per-user, short-lived tokens: **open**, #19 |
| Tampering | Model-supplied arguments widen a query | LLM03 | Schema offers only `query`: `services/assistant/tests/test_tool_loop.py::test_model_supplied_audience_and_region_change_nothing_through_the_cli`. Carried over to `mcp-rag`: **open**, #10 (SA-02 review, second-pass 5) |
| Repudiation | Tool calls not attributable | LLM03 | **Open:** #20 |
| Information disclosure | Tool error leaks a connection string | LLM02 | Fixed text only: `services/assistant/tests/test_tool_loop.py::test_a_retrieval_failure_tells_the_model_nothing_about_why` |
| Denial of service | A tool loop runs without end | LLM06 | `services/assistant/tests/test_first_request.py::test_a_model_that_always_searches_gets_three_real_searches_in_all`, `services/assistant/tests/test_tool_loop.py::test_a_fifth_turn_never_happens` |
| Elevation of privilege | Over-broad database role | LLM03 | The app connects as the image's superuser, so `COPY … TO PROGRAM` may run code in the container: **open**, medium candidate, #9a (decision 0005, "Open") |
| Elevation of privilege | Agent picks up default tools | LLM03 | `tools` required; explicit option list: `services/common/tests/test_llm_factory.py::test_agent_options_require_tools`, `services/common/tests/test_llm_factory.py::test_anything_outside_the_allowlist_is_refused` |

## Boundary 4: Northwind to external A2A agents

Not built. All threats **open**, Window B P4 and P5, except where noted. Designs are in PRD §7.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Spoofed agent or tampered card | LLM04 | **Open:** A2A-01, A2A-06 |
| Tampering | Forged push callback | LLM01 | **Open:** A2A-08 |
| Repudiation | Disputed payment outcome | LLM03 | **Open:** #20, A2A-07 |
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
| Repudiation | No per-partner trail | LLM03 | **Open:** #20, NFR-10 |
| Information disclosure | Partner reads other shoppers' data | LLM02 | **Open:** A2A-05 |
| Denial of service | Partner floods the assistant | LLM06 | **Open:** NFR-14 |
| Elevation of privilege | Order over the ceiling | LLM03 | **Open:** A2A-05, ceiling value `unconfirmed` (`docs/governance/tool-register.md`) |

## Boundary 6: build time (Claude Code)

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Sandbox claims a spending window it should not | LLM06 | Accepted limit: any sandboxed command can claim first. What holds is the bound: one suite, two models, the budget, the time limit (decision 0005, "Limits", CC-27 evidence finding 8) |
| Tampering | Session edits its settings, hooks or the runner's code | LLM04 | OS sandbox and deny rules, measured in CC-27 evidence, "R1 and R2 at the OS level", and the 5a evidence rig runs. `tests/hooks/test_sandbox_settings.py` pins the proposal file only; `tests/hooks/test_settings.py::test_weakened_settings_are_caught` covers the guard and rules. **Open**, AUTO-2: no test fails if the installed settings lose their sandbox block (CC-27 evidence, "Limits"); `make runner-check` reports it, but it is run by hand and proves things only for an honest session (decision 0005, "Limits"). Guard hook is a speed bump: 22 strict xfails (FOUND-2 review). Runner code moves only through `make runner-sync`, which shows the user the diff |
| Tampering | Baseline or known failures changed | LLM05 | `denyWrite`, `ship` labels: `tests/ship/test_ship.py::test_protected_change_without_label_is_refused`. Committed content changed through git plumbing: landed, 5b: `Guard / baseline-label` fails a PR that changes the baseline, known failures or the item set without `baseline-change` (`tests/ship/test_guard.py::test_a_protected_change_without_the_label_fails`, decision 0009, clause 10). A merge outside `ship` is stopped only once these checks are required in the ruleset: **open**, 5b (decision 0009, clause 16). "Can only shrink" is now a check at promotion (`services/llm-replay/tests/test_promotion.py::test_known_failures_may_not_grow_in_the_checkout`). On a PR the label is the only check, so a labelled, added `approved_by: human` entry still waives a containment failure (decision 0006, rule 3) |
| Tampering | Promotion runs session-editable code unsandboxed | LLM04 | Landed, 5b: promotion runs from the runner clone on `python -I`, and the checkout is data: `evals/tests/test_rag12_followup.py::test_eval_promote_runs_no_python_outside_the_clone`, `services/llm-replay/tests/test_promotion.py::test_the_clones_baseline_decides_not_the_checkouts` (decision 0009, clauses 1 to 3). Row 5b is `partial`, and its log says nobody promotes until its left items are done: **open**, 5b, policy only |
| Tampering | CI runs the PR's own gate, so a green gate proves little | LLM04 | Landed, 5b: `Guard / eval-integrity-main` and `Guard / eval-gate-main` run main's `evals/src`: `tests/ship/test_guard.py::test_the_prs_gate_code_is_replaced_by_mains`, `tests/ship/test_guard.py::test_integrity_runs_in_its_own_job_so_nothing_can_accept_it`, `.github/workflows/guard.yml`. `eval-gate-main` is not a boundary: the PR's services run in the same process, and what a lock-pinned dependency brings is not covered. Accepted by the user (decision 0009, "Residual risk, stated"). With `gate-change`, green means the PR's own gate passed |
| Tampering | Forged new cassette makes a stale item look fresh | LLM05 | `make eval-integrity` refuses modified cassettes, not added ones: **open**, medium, AUTO-2 (decision 0006, "Open limits"; decision 0009, "Residual risk, stated") |
| Tampering | Shared caches outlive a worktree | LLM04 | `~/.cache/uv` and the pnpm store: **open**, needs the user: F6 stands (decision 0005, "Open"). The runner has a private cache |
| Tampering | Embedding model not pinned to a revision | LLM04 | **Open**, #18 (`PROGRESS.md` row 3) |
| Repudiation | `approved_by` and override reasons are only claims | LLM05 | Accepted: plain strings, a claim not proof (decisions 0003, 0006). `eval-integrity` prints added entries, and nothing makes anyone read them: **open**, medium, AUTO-2 (5a evidence, finding 7) |
| Information disclosure | Session reads the key | LLM02 | `denyRead` and deny rules; fake-key test passed, a manual result (decision 0005, "Open"). User-level denies need the sandbox on: accepted (decision 0005, "Limits") |
| Information disclosure | Secret in a cassette | LLM02 | `make scan-cassettes` in CI, regex only; the key-fragment scan needs the user's terminal. Split secrets: **open**, #18 (FOUND-2 review, finding 6; SA-02 review, second-pass 9) |
| Information disclosure | `allowLocalBinding` exposes a listener on the LAN | LLM02 | Measured. No narrower setting found: **open**, needs the user: F4 stands (decision 0005, "Open"; CC-27 evidence) |
| Denial of service | Session burns credit | LLM06 | Window, budget and ledger: `services/llm-replay/tests/test_proxy.py::test_budget_breach_is_refused_with_zero_upstream_calls`. `CLAUDE.md` forbids raising a cap without asking |
| Elevation of privilege | Session merges to main or pushes a workflow | LLM03 | No credentials; `ship` and pre-push: `tools/ship.sh`, `tools/pre-push.proposed.sh`. A local check, not a boundary: only a server-side rule closes this, and the user is checking the repo settings (decision 0008, "Limits"). 12 refusals untested: **open**, AUTO-2 |
| Elevation of privilege | A make variable reaches a shell | LLM03 | Free-text variables pinned: `evals/tests/test_rag12_followup.py::test_eval_promote_passes_a_value_literally_and_runs_nothing`. Others rely on rule 7's acceptance (decision 0006, "Found during this row") |

## Boundary 7: Northwind runtime to the model provider

Not in the PRD §7 table; added here at the user's request. Crosses shopper messages, retrieved and
review text and, from #7's window, internal-only text. Which data crosses is in `docs/governance/data-flow.md`.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Traffic sent to the wrong host | LLM04 | The upstream is a constant in code, and neither CLI nor environment can set it: `services/llm-replay/tests/test_runner_criteria.py::test_cli_runner_takes_no_upstream_or_key_option`. Client proxy settings ignored (`trust_env=False`, FOUND-2 review, finding A) |
| Spoofing | A runtime call lands on the developer's subscription | LLM02 | Environment built from scratch, placeholder key: `services/common/tests/test_llm_factory.py::test_child_env_is_built_from_scratch` |
| Tampering | Request widened to bill beyond the reservation | LLM06 | Allow-listed fields, models and content blocks; no images, documents or server tools (decision 0005, "What the runner forwards"). The budget bound rests on those lists (decision 0005, "Limits") |
| Tampering | A model response carries a hostile payload to the shopper | LLM10 | The portal renders plain text through React, with no raw HTML or markdown rendering (`apps/portal/src/Chat.tsx`). Output check: **open**, #13 |
| Repudiation | Spend cannot be attributed | LLM06 | Ledger per call, by suite and model; `make llm-spend`. Per-answer telemetry: `PROGRESS.md` row 6 |
| Information disclosure | Key leaks in a request or response | LLM02 | Fragment check on both, and a hit closes the window and prints `ROTATE THE KEY` (decision 0005, "Key fragments"). Sees text, not encodings: accepted (decision 0005, "Limits"). `readable_forms` now splits on SSE line ends only: landed, #7, `services/llm-replay/tests/test_runner.py::test_a_line_separator_in_a_delta_does_not_drop_it_from_the_joined_text`. `parse_sse_usage` still uses `splitlines()`, which cannot hide a key: **open**, low (`PROGRESS.md`, "Open items") |
| Information disclosure | Card data in a prompt | LLM02 | Refused before the model: boundary 1. Refused in tool arguments: `services/assistant/tests/test_tool_loop.py::test_card_data_in_a_tool_argument_is_refused_and_searches_nothing`. Retrieved text is a third route: no PAN scan at ingestion, so card digits in a review reach the model on the assistant path: **open**, Window B P6, NFR-04 (`PROGRESS.md` row 3) |
| Information disclosure | Internal-only text sent to the provider | LLM02 | At runtime, document bodies have no path: `services/assistant/tests/test_context.py::test_no_canary_reaches_the_model` proves the retrieval filter. Names and topics have crossed, through adversarial eval items, and `_context_leaks` does not stop plain text (`docs/governance/data-flow.md`). **Landed, #7: document text went to Haiku in that row's window**: 11 entries in `data/enrichment/internal.jsonl`, no cassette and no proxy log, so the recordings cannot show it (decision 0010, rule 3). It stays synthetic, and hard rule 7 covers shoppers, not the provider. Whether that is acceptable beyond the lab: **open**, needs the user |
| Information disclosure | Shopper data retained by the provider | LLM02 | Retention `unconfirmed`: **open**, Window B P6, NFR-07. All data synthetic (hard rule 3). Masking before model calls: **open**, Window B NFR-05 |
| Denial of service | Provider down or rate-limited | LLM06 | Upstream failures become fixed codes: `services/gateway/tests/test_chat.py::test_an_upstream_failure_is_only_a_fixed_code`. Graceful degradation: **open**, Window B NFR-14 |
| Elevation of privilege | Credit used past the cap | LLM06 | `LLM_BUDGET_USD`, read by the runner from the user's terminal; a sandboxed value does nothing (decision 0005) |

## Residuals

Accepted or open, as recorded. Not re-derived; severity is the source's.

| Residual | Severity | State | Source |
|---|---|---|---|
| Promotion from a checkout runs session-editable code unsandboxed | High | Closed in code by 5b; nobody promotes until the left items below are done | Decisions 0006, 0009; 5a evidence, finding 1 |
| `denyWrite` protects paths, not committed content | High | Closed through `ship` by 5b; for a merge outside `ship`, only once the required checks are added | Decisions 0006, 0009; 5a evidence, finding 2 |
| CI runs the PR's own gate and integrity code | Not rated | Closed by `Guard`, which runs main's copy. `CI / eval-gate` keeps running the PR's own, on purpose | Decision 0009, clauses 11, 12, 14 |
| `eval-gate-main` is not a boundary; `Guard` and its scripts are PR-editable; promotion cannot prove scores are real | Not rated | Accepted by the user | Decision 0009, "Residual risk, stated" |
| Row 5b's left items: `Guard` on GitHub, the first real `ship`, the settings install, `eval-promote CHECK=1` on the clone, the pre-push reinstall, the required checks | Not rated | Open, 5b; no later record in `PROGRESS.md` | `PROGRESS.md` row 5b and its log |
| The F1 and F3 fixes in 5b had no security pass | Not rated | Recorded, by the user's decision | 5b evidence, "F1 and F3, decided by the user" |
| Small retrieval drops have no check but the tolerance | Not rated | Open, #8 | Decision 0009, "Residual risk, stated" |
| `services/llm-replay/src/northwind_llm_replay/runner_check.py` M1 to M3, L1 to L6 | Medium, low | Landed, 5b. Proves things only for an honest session | 5b evidence, "Failing first: runner_check.py" |
| Planted reviews still reach a top 5 under the allow-list: 23 appearances against 20 plain, on one unfiltered query per item | Medium | Open, #8, before the shared index switches | Decision 0010; `PROGRESS.md`, "Open items" |
| RAG-02's recall gain is on the items it was chosen with | Not rated | Open, #32 | Decision 0010; `PROGRESS.md` row 7 |
| The meaning of injected text reaches the shopper | Logged gap | Open, #13 | Decision 0007; RAG-13 review |
| Fullwidth source-tag lookalikes | Medium, unconfirmed | Open, #9, #13 | RAG-13 review, scoped check 1 |
| Superuser database role | Medium candidate | Open, #9a | Decision 0005; CC-27 evidence, finding 10 |
| The window authorises the sandbox, not one command | Medium | Accepted by the user | Decision 0005; CC-27 evidence, finding 8 |
| `allowLocalBinding` not loopback-only (F4); shared caches (F6) | Medium | Open; needs the user | Decision 0005; CC-27 evidence |
| Forged added cassette; added-entry line in a green log; push to main compares HEAD with itself | Medium, medium, low | Open, AUTO-2 | Decision 0006; 5a evidence, findings 11, 7, 12 |
| No further review: third-pass fixes for CC-27 and 5a, second-pass fixes for SA-02, second-pass fixes for RAG-13, and 5a recovery step 3 | Not rated | Recorded | CC-27 evidence; 5a evidence; SA-02 review; RAG-13 review; `PROGRESS.md` row 5a |
| `lost_overrides` reads the working tree only | Medium | Open, AUTO-2 | Decision 0006; 5a evidence, finding 8 |
| 5a findings 15, 16, 17 | Low | Open; each has its own line in "Owners" | 5a evidence |
| Split secrets in the scanner | Medium | Open, #18 | FOUND-2 review, finding 6; `PROGRESS.md` row 18 |
| RAG-12 third-pass F7, F8, F9, F11, F12, and second-pass F3, F9 to F13, F15 | Not rated here | Open; each has its line in "Owners" | `PROGRESS.md` row 4 |
| Installed settings can lose their sandbox block with no test failing | Not rated | Open, AUTO-2 | CC-27 evidence, "Limits" |
| No recorded `ship` run on real `gh` | Not rated | Open; row A1 is partial for this reason | Decision 0008, `PROGRESS.md` row A1 |
| 12 untested refusals; `ship` shows no `PROGRESS.md` diff, though that file is imported into `CLAUDE.md`; control surface gaps; re-review F5, F6, F7 | Medium, low | Open, AUTO-2 | `PROGRESS.md` row A2; AUTO-1 evidence |
| The F1 and F2 re-review fixes had no second `security-reviewer` pass; the user's advisor accepted them | Medium | Accepted by the user | Decision 0008; AUTO-1 evidence |
| Card detection is a net, not the control | From a high, first-pass 3; the rest low, second-pass 4 | Accepted by the user; hosted fields are the control | SA-02 review |
| Hook is a speed bump: 22 strict xfails | Medium | Accepted; the sandbox is the control | FOUND-2 review, 15b, R1 to R4 |
| `approved_by` is a claim, not proof | Not rated | Accepted | Decisions 0003, 0006 |

## Owners

Every gap across the three documents that no `PROGRESS.md` row named. The owner is a row, a Window B NFR or
AUTO-2, or `needs the user` where it could not be placed. These are GOV-1's placements: no row's own text
changed, so a row takes an item on only when its plan says so.

| Item | Owner | Why |
|---|---|---|
| `dataset_sha256` copied but not verified | #10 | Its index-manifest resource is the first reader of that manifest |
| Embedding model not pinned to a revision | #18 | It freezes model IDs before the final recording |
| Key-fragment scan reads `cassettes/` only, not a worktree's `.llm-proxy-log` or `evals/` | #18 | Its audit is the scan's full run, and it already owns the scanner fixes |
| RAG-12 second-pass F3: the scanner reads raw text | #18 | `PROGRESS.md` row 4 says it overlaps #18's scanner work |
| RAG-12 third-pass F7: repeated in-tolerance promotions can lower the bar | #8 | Its row now carries the retrieval drift check |
| RAG-12 third-pass F8: answerer, judge and retriever IDs are stored, never compared | #13 | Its adapter changes the answerer and re-records all 100 items |
| RAG-12 third-pass F11: stale or malformed known-failure entries are not refused by the gate | #32 | The next RAG-12 row, which grows the gate's inputs |
| 5a finding 17: override record details | #32 | Same record, same RAG-12 follow-up |
| `update_delivery_address` and `cancel_order` have no row | #21 | The closest order work; the row does not name them, so `docs/governance/tool-register.md` keeps `unconfirmed` |
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
| RAG-12 second-pass F9 to F13, F15 | needs the user | Only the numbers survive, in `PROGRESS.md` row 4. Their text is in no file, so they cannot be placed |
| 5a finding 15: `deny` on `evals/baselines/**` | needs the user | The 5a evidence records it as the user's decision |
| `allowLocalBinding` not loopback-only (F4) | needs the user | No narrower setting was found, so it is accept or change the settings |
| Shared caches (F6) | needs the user | The sandbox's write list is the user's to change |
| `make enrich` has no `ask` rule | needs the user | `PROGRESS.md`, "Open items", leaves this to the user, and the change spans three files |
| Whether internal-only text going to a provider is acceptable beyond a lab | needs the user | A policy call that no PRD requirement covers |
| PRD §7 has no row for Northwind to the model provider. Boundary 7 fills that here; the PRD is unchanged | needs the user | Only the user changes the PRD |
| What the device bridge can reach and keep | needs the user | Nothing in the repo records it |

## Open items

| Item | Owner |
|---|---|
| Mapping to the OWASP Top 10 for Agentic Applications 2026 | P3, P5 |
| Kill switches (NFR-12), audit log (NFR-10), prompt versioning and rollback drill (NFR-11) | Window B P6; #20 for the audit log |
| PRD §10 says "written and reviewed": this model has not had the user's review | The user |
