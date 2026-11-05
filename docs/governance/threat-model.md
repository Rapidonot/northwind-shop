# Threat model (PRD §10)

Assets, actors, and STRIDE across each trust boundary, mapped to the OWASP Top 10 for LLM Applications.
Each threat has either a control that exists today, with its evidence, or `open` with the row that owns
it, or `no row owns this`. Residuals are taken from the reviews and decisions and cited, not re-derived.

**Baseline:** main at `f50f7c8`, 2026-11-05. Rows are `PROGRESS.md` rows. A boundary with nothing built
says so: its threats are `open` by design, not by oversight.

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

## Assets

| Asset | Why it matters | Where |
|---|---|---|
| API key and credit | Spend, and access to the provider | `~/.northwind/secrets/anthropic.env`; `docs/governance/data-flow.md` |
| Runner clone and its environment | Code here runs beside the key | `~/.northwind/runner`, decision 0005 |
| Budget state | Caps and the record of spend | `~/.northwind/ledger.jsonl` |
| Eval gate inputs | A weakened gate hides every later regression | `evals/baselines/`, `evals/fixtures/known_failures.json`, `cassettes/`, `evals/contexts/`, and the local promotion candidate |
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

Built: `retrieve_context` and the server's first search, dense-only.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | A review poses as a policy or system source | LLM01 | Source text escaped and labelled: `services/assistant/tests/test_tool_loop.py::test_the_retrieved_text_is_labelled_and_escaped_data`. Reviews cannot set policy: `services/assistant/tests/test_smoke_e2e.py::test_a_review_cannot_set_the_return_policy`. Fullwidth lookalikes of a source tag: **open**, medium, unconfirmed, #9 (NFKC) and #13 (RAG-13 review, scoped check 1) |
| Tampering | Planted instruction in a review is followed or relayed | LLM01 | R00712 not followed in four recordings; that is one review of 15 planted traps. **Open:** its meaning still reaches the shopper; the test is a strict xfail, and the output check is the primary control, #13 (decision 0007; RAG-13 review) |
| Tampering | Poisoned content in the index | LLM05 | Ingest guard (`services/rag/tests/test_sources.py::test_pipeline_runs_the_ingest_guard`), allow-listed fixtures (`services/rag/tests/test_sources.py::test_guard_refuses_a_file_not_listed_as_ingestible`), no injection filter at ingest on purpose. Flagging at ingestion: **open**, Window B NFR-03. `dataset_sha256` copied but not verified: **open**, no row owns this (`PROGRESS.md` row 3) |
| Tampering | Generated context lifts a planted review in ranking | LLM09 | Not on main. In flight, #7: measured, and reviews get no context (`req/RAG-02-enrichment:docs/decisions/0009-enrichment-without-batches.md`) |
| Repudiation | An answer cannot be traced to its sources | LLM07 | Citations limited to sources returned this turn: `services/assistant/tests/test_tool_loop.py::test_an_invented_citation_is_dropped`. Uncited claims stay in the text: **open**, #13 (SA-02 review, finding 5) |
| Information disclosure | Internal documents reach a shopper | LLM02, LLM09 | Filter by server-bound audience: `services/rag/tests/test_retrieve_context.py::test_no_internal_chunk_reaches_a_shopper_even_when_quoted_exactly`, `services/rag/tests/test_retrieve_context_isolation.py::test_an_internal_document_in_the_shoppers_own_region_is_still_hidden`. Red-team set: **open**, #9. Same rule on catalog tools: **open**, #10 |
| Information disclosure | Another region's policy reaches a shopper | LLM07 | `services/rag/tests/test_retrieve_context.py::test_one_context_never_holds_two_regions_return_rules`. Evals still score an unfiltered context: **open**, #8 |
| Information disclosure | Prices or stock stated from text (hard rule 2) | LLM07 | Exact prices kept out of chunks: `services/rag/tests/test_index.py::test_planted_price_aborts_the_run_and_writes_nothing`. `textscan` gaps: **open**, #7. Promotions in reviews rest on the prompt: **open**, #12 (SA-02 review, finding 5) |
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
| Tampering | Session edits its settings, hooks or the runner's code | LLM04 | OS sandbox and deny rules, measured in CC-27 evidence, "R1 and R2 at the OS level", and the 5a evidence rig runs. `tests/hooks/test_sandbox_settings.py` pins the proposal file only; `tests/hooks/test_settings.py::test_weakened_settings_are_caught` covers the guard and rules. **Open:** no test fails if the installed settings lose their sandbox block (CC-27 evidence, "Limits"); `make runner-check` reports it, but it is run by hand and proves things only for an honest session (decision 0005, "Limits"); no row owns this. Guard hook is a speed bump: 22 strict xfails (FOUND-2 review). Runner code moves only through `make runner-sync`, which shows the user the diff |
| Tampering | Baseline or known failures changed | LLM05 | `denyWrite`, `ship` labels: `tests/ship/test_ship.py::test_protected_change_without_label_is_refused`. Committed content changed through git plumbing: **open**, high, 5b (decision 0006, "Open limits"). An added `approved_by: human` entry waives a containment failure, and CI stays green: "can only shrink" is policy (decision 0006, rule 3) |
| Tampering | Promotion runs session-editable code unsandboxed | LLM04 | No promotion until 5b: **open**, high, policy only (decision 0006, rule 5) |
| Tampering | CI runs the PR's own gate, so a green gate proves little | LLM04 | **Open:** 5b (decision 0008; `PROGRESS.md` row 5b) |
| Tampering | Forged new cassette makes a stale item look fresh | LLM05 | `make eval-integrity` refuses modified cassettes, not added ones: **open**, medium, no row owns this (decision 0006, "Open limits") |
| Tampering | Shared caches outlive a worktree | LLM04 | `~/.cache/uv` and the pnpm store: **open**, no row owns this: F6 stands (decision 0005, "Open"). The runner has a private cache |
| Tampering | Embedding model not pinned to a revision | LLM04 | **Open**, no row owns this (`PROGRESS.md` row 3) |
| Repudiation | `approved_by` and override reasons are only claims | LLM05 | Accepted: plain strings, a claim not proof (decisions 0003, 0006). `eval-integrity` prints added entries, and nothing makes anyone read them: **open**, medium, no row owns this (5a evidence, finding 7) |
| Information disclosure | Session reads the key | LLM02 | `denyRead` and deny rules; fake-key test passed, a manual result (decision 0005, "Open"). User-level denies need the sandbox on: accepted (decision 0005, "Limits") |
| Information disclosure | Secret in a cassette | LLM02 | `make scan-cassettes` in CI, regex only; the key-fragment scan needs the user's terminal. Split secrets: **open**, #18 (FOUND-2 review, finding 6; SA-02 review, second-pass 9) |
| Information disclosure | `allowLocalBinding` exposes a listener on the LAN | LLM02 | Measured. No narrower setting found: **open**, no row owns this: F4 stands (decision 0005, "Open"; CC-27 evidence) |
| Denial of service | Session burns credit | LLM06 | Window, budget and ledger: `services/llm-replay/tests/test_proxy.py::test_budget_breach_is_refused_with_zero_upstream_calls`. `CLAUDE.md` forbids raising a cap without asking |
| Elevation of privilege | Session merges to main or pushes a workflow | LLM03 | No credentials; `ship` and pre-push: `tools/ship.sh`, `tools/pre-push.proposed.sh`. A local check, not a boundary: only a server-side rule closes this, and the user is checking the repo settings (decision 0008, "Limits"). 12 refusals untested: **open**, AUTO-2 |
| Elevation of privilege | A make variable reaches a shell | LLM03 | Free-text variables pinned: `evals/tests/test_rag12_followup.py::test_eval_promote_passes_a_value_literally_and_runs_nothing`. Others rely on rule 7's acceptance (decision 0006, "Found during this row") |

## Boundary 7: Northwind runtime to the model provider

Not in the PRD §7 table; added here at the user's request. Crosses shopper messages, retrieved and
review text and, from the #7 branch's run, internal-only text. Which data crosses is in `docs/governance/data-flow.md`.

| STRIDE | Threat | OWASP | Control today, or open |
|---|---|---|---|
| Spoofing | Traffic sent to the wrong host | LLM04 | The upstream is a constant in code, and neither CLI nor environment can set it: `services/llm-replay/tests/test_runner_criteria.py::test_cli_runner_takes_no_upstream_or_key_option`. Client proxy settings ignored (`trust_env=False`, FOUND-2 review, finding A) |
| Spoofing | A runtime call lands on the developer's subscription | LLM02 | Environment built from scratch, placeholder key: `services/common/tests/test_llm_factory.py::test_child_env_is_built_from_scratch` |
| Tampering | Request widened to bill beyond the reservation | LLM06 | Allow-listed fields, models and content blocks; no images, documents or server tools (decision 0005, "What the runner forwards"). The budget bound rests on those lists (decision 0005, "Limits") |
| Tampering | A model response carries a hostile payload to the shopper | LLM10 | The portal renders plain text through React, with no raw HTML or markdown rendering (`apps/portal/src/Chat.tsx`). Output check: **open**, #13 |
| Repudiation | Spend cannot be attributed | LLM06 | Ledger per call, by suite and model; `make llm-spend`. Per-answer telemetry: `PROGRESS.md` row 6 |
| Information disclosure | Key leaks in a request or response | LLM02 | Fragment check on both, and a hit closes the window and prints `ROTATE THE KEY` (decision 0005, "Key fragments"). Sees text, not encodings: accepted (decision 0005, "Limits"). `readable_forms` `splitlines()` change: **open**, in flight on `req/RAG-02-enrichment`, due before #18 (`PROGRESS.md` row 17a) |
| Information disclosure | Card data in a prompt | LLM02 | Refused before the model: boundary 1. Refused in tool arguments: `services/assistant/tests/test_tool_loop.py::test_card_data_in_a_tool_argument_is_refused_and_searches_nothing`. Retrieved text is a third route: no PAN scan at ingestion, so card digits in a review reach the model on the assistant path: **open**, no row owns this (`PROGRESS.md` row 3) |
| Information disclosure | Internal-only text sent to the provider | LLM02 | On main, document bodies have no runtime path: `services/assistant/tests/test_context.py::test_no_canary_reaches_the_model` proves the retrieval filter. Names and topics have crossed, through adversarial eval items, and `_context_leaks` does not stop plain text (`docs/governance/data-flow.md`). **In flight, #7: document text has already gone to Haiku, in the branch's run**, 11 internal entries, no cassette and no proxy log, so main's recordings cannot show it (the enrichment decision on the branch). It stays synthetic, and hard rule 7 covers shoppers, not the provider. Whether that is acceptable beyond the lab: **open**, no row owns this |
| Information disclosure | Shopper data retained by the provider | LLM02 | Retention `unconfirmed`, no row owns this. All data synthetic (hard rule 3). Masking before model calls: **open**, Window B NFR-05 |
| Denial of service | Provider down or rate-limited | LLM06 | Upstream failures become fixed codes: `services/gateway/tests/test_chat.py::test_an_upstream_failure_is_only_a_fixed_code`. Graceful degradation: **open**, Window B NFR-14 |
| Elevation of privilege | Credit used past the cap | LLM06 | `LLM_BUDGET_USD`, read by the runner from the user's terminal; a sandboxed value does nothing (decision 0005) |

## Residuals

Accepted or open, as recorded. Not re-derived; severity is the source's.

| Residual | Severity | State | Source |
|---|---|---|---|
| Promotion from a checkout runs session-editable code unsandboxed | High | Open, 5b | Decision 0006; 5a evidence, finding 1 |
| `denyWrite` protects paths, not committed content | High | Open, 5b; until then the user checks each PR's file list | Decision 0006; 5a evidence, finding 2 |
| CI runs the PR's own gate and integrity code | Not rated | Open, 5b | Decision 0008; `PROGRESS.md` row 5b |
| `services/llm-replay/src/northwind_llm_replay/runner_check.py` M1 to M3, L1 to L6 | Medium, low | Open, 5b. Proves things only for an honest session | 5a evidence, "Merge with RAG-13" |
| The meaning of injected text reaches the shopper | Logged gap | Open, #13 | Decision 0007; RAG-13 review |
| Fullwidth source-tag lookalikes | Medium, unconfirmed | Open, #9, #13 | RAG-13 review, scoped check 1 |
| Superuser database role | Medium candidate | Open, #9a | Decision 0005; CC-27 evidence, finding 10 |
| The window authorises the sandbox, not one command | Medium | Accepted by the user | Decision 0005; CC-27 evidence, finding 8 |
| `allowLocalBinding` not loopback-only (F4); shared caches (F6) | Medium | Open; no row owns this | Decision 0005; CC-27 evidence |
| Forged added cassette; added-entry line in a green log; push to main compares HEAD with itself | Medium, medium, low | Open; no row owns this | Decision 0006; 5a evidence, findings 11, 7, 12 |
| No further review: third-pass fixes for CC-27 and 5a, second-pass fixes for SA-02, second-pass fixes for RAG-13, and 5a recovery step 3 | Not rated | Recorded | CC-27 evidence; 5a evidence; SA-02 review; RAG-13 review; `PROGRESS.md` row 5a |
| `lost_overrides` reads the working tree only | Medium | Open; no row owns this | Decision 0006; 5a evidence, finding 8 |
| 5a findings 15, 16, 17 | Low | Open; no row owns this | 5a evidence |
| Split secrets in the scanner; `readable_forms` line splitting | Medium, not rated | Open, #18; the second is in flight on `req/RAG-02-enrichment` | FOUND-2 review, finding 6; `PROGRESS.md` rows 17a, 18 |
| RAG-12 third-pass F7, F8, F9, F11, F12, and second-pass F3, F9 to F13, F15 | Not rated here | Open. F3 overlaps #18; no row owns the rest | `PROGRESS.md` row 4 |
| Installed settings can lose their sandbox block with no test failing | Not rated | Open; no row owns this | CC-27 evidence, "Limits" |
| No recorded `ship` run on real `gh` | Not rated | Open; row A1 is partial for this reason | Decision 0008, `PROGRESS.md` row A1 |
| 12 untested refusals; `ship` shows no `PROGRESS.md` diff, though that file is imported into `CLAUDE.md`; control surface gaps; re-review F5, F6, F7 | Medium, low | Open, AUTO-2 | `PROGRESS.md` row A2; AUTO-1 evidence |
| The F1 and F2 re-review fixes had no second `security-reviewer` pass; the user's advisor accepted them | Medium | Accepted by the user | Decision 0008; AUTO-1 evidence |
| Card detection is a net, not the control | From a high, first-pass 3; the rest low, second-pass 4 | Accepted by the user; hosted fields are the control | SA-02 review |
| Hook is a speed bump: 22 strict xfails | Medium | Accepted; the sandbox is the control | FOUND-2 review, 15b, R1 to R4 |
| `approved_by` is a claim, not proof | Not rated | Accepted | Decisions 0003, 0006 |

## Open items

| Item | Owner |
|---|---|
| PRD §7 has no row for Northwind to the model provider. Boundary 7 fills that here; the PRD is unchanged | No row owns this |
| Mapping to the OWASP Top 10 for Agentic Applications 2026 | P3, P5 |
| Whether internal-only text going to a provider is acceptable beyond a lab with synthetic data | No row owns this |
| `dataset_sha256` not verified; embedding model not pinned; forged added cassette; no PAN scan at ingestion | No row owns these |
| Kill switches (NFR-12), audit log (NFR-10), prompt versioning and rollback drill (NFR-11) | Window B P6; #20 for the audit log |
| PRD §10 says "written and reviewed": this model has not had the user's review | The user |
