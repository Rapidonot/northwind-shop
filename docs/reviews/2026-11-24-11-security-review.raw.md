Security review of row #11 (RAG-04 query understanding). Diff: /tmp/claude-502/northwind-review-11.diff, `base 6e46b8a3204fc8e26cd60ba006b024664c193078`, `head 6d86bb38438d75a38dd47dc70bf7a83d6fe82f36`.

Before reviewing I re-read CLAUDE.md "Hard rules", decision 0001, decision 0022 and the plan at docs/reviews/2026-11-24-11-plan.md.

**There are no critical or high findings.** I found 2 mediums and 7 lows. One low (L6) is unconfirmed. None of them breaks a hard rule as the code stands.

## Findings (most severe first)

### M1 (medium): a proxy problem that is not a cassette miss silently swaps Haiku's plan for the rules plan in the gate and the snapshot
- **Where:** `/Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/query_plans.py:65-77`. **Rule:** decision 0001 §3 (replay must be faithful); decision 0022 §9 ("a miss stops the run"). **Not unsafe.**
- **What is wrong:** `planner` stops only when `running.proxy.state.misses` is non-empty. `Understander.plan` turns every other failure into `path="error"` and returns the rules plan (`understand.py:264-266`). `planner` stores `understood.plan` without checking `understood.path`. Failures that leave `misses` empty include:
  - a 401 `refused-auth` (only logged, not added to any state list);
  - a `refused-scrub` 400;
  - a connection error or proxy start-up problem;
  - an exception while building the client.
- **How it fails:** say a change to `anthropic_client` or to the proxy's credential check makes the planner's requests come back 401. All 7 Haiku-routed eval items (EV-018, EV-067, …) then run the rules plan, and nothing is printed. If `make eval-snapshot` and `make eval-gate` both run under that condition, the saved and live contexts agree. The gate then passes on, and reports recall for, a search no shopper gets.
- **Fix:** refuse unless every needed message ends with `path == "model"` (or `"invalid"`, which is deterministic). Also apply `cli.proxy_stops` to the planner proxy's state. There is no test for this today: `evals/tests/test_rag04_query_plans_tw.py` covers misses only.

### M2 (medium): Haiku's rewritten part texts are never frozen, so the gate ranks them on live float32 embeddings
- **Where:** `/Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py:720-729` (`_frozen_queries`) and `cli.py:732-781` (`cmd_embed_cache`). **Rule:** decision 0012 (cross-platform ranking) and 0022's Consequences. **Not unsafe.**
- **What is wrong:** decision 0022 says "frozen vectors are added once W1 has recorded them", but nothing in this diff can add them. `_frozen_queries` lists only item queries, smoke questions, traps and red-team queries. For a missing key, `CachedEmbedder.embed` (`services/rag/src/northwind_rag/embedding.py:202-207`) falls back to the live model. `make embed-cache CHECK=1` checks the same list, so it cannot see the gap either.
- **How it fails:** the 7 Haiku-routed items rank their parts with platform-dependent vectors (the PR #30 drift). A baseline promoted on macOS can show stale contexts or recall drift on Linux CI.
- **Fix:** have `_frozen_queries` include the planned part texts from the replayed `query-understanding` plans, so both `embed-cache` and `CHECK=1` cover them.

### L1 (low): two tokens in one turn with different `exp` let the planned token outlive its search-cap entry
- **Where:** `/Users/rapido/Projects/northwind-shop/services/mcp-rag/src/northwind_mcp_rag/ledger.py:36-39`, together with `services/assistant/src/northwind_assistant/understand.py:364-379` and `turn.py:303-325`. **Rule:** RAG-10, 0022 §7. **Not unsafe today.**
- **What is wrong:** `SearchLedger.take` saves the `exp` of the most recent token each time. The planned token is minted after the Haiku call and the live-filter calls, so its `exp` can be later than the plain token's.
- **How it fails, step by step:**
  1. The first search uses the planned token: entry `(1, exp1)`.
  2. Two model refinements use the plain token: `(2, exp0)`, then `(3, exp0)`.
  3. The entry is purged at `exp0`.
  4. Between `exp0` and `exp1` the planned token is still valid and gets a fresh count of 3.
- **Reach:** only the server process holds the planned token. The CLI is given only the plain `token` (`turn.py:344`, `:367`), and `McpRagSearcher.__repr__` hides it. So this needs the token to be stolen from the process or from the localhost hop. The gap is the time between the two mints: well under 1 s in live mode, but unbounded in record/replay, where Haiku has no deadline.
- **Fix (makes the cap stricter):** keep the larger of the stored and new `exp`.
- **The rest of the question:** reusing the turn id does not otherwise get around the cap. The planned search counts once against the same `turn`, and only one first search runs.

### L2 (low): `plan_ok` checks `q` for card data but not the `c` / `s` items (defence in depth)
- **Where:** `/Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/mcp_token.py:137-143`. **Rule:** hard rule 1. **Not unsafe:** nothing reaches it today.
- **What is wrong:** an item of up to 32 characters fits a 16- to 19-digit PAN. `mint_planned(key, caller, turn, {"parts":[{"c":["4111111111111111"]}]})` would sign card text into a token.
- **Why it is unreachable now:** both places that fill `c` and `s` (`rules_plan` and `validate_plan`) only allow catalog vocabulary.
- **Test gap:** `test_mint_planned_on_its_own_never_mints_card_text` reads only `q` (`_qf_texts`), so it does not cover this. It is the same layer as test-writer's finding 2.
- **Fix:** run `contains_card_data` on every `c` / `s` item too.

### L3 (low): `mint(turn=…)` uses `_UUID4.match`, whose `$` accepts a trailing `"\n"`
- **Where:** `/Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/mcp_token.py:200` (same pattern already at `:249`). **Rule:** RAG-10. **Not unsafe:** nothing reaches it.
- **How it fails:** a turn of `"<uuid>\n"` would be a separate ledger key, so it would get its own count of 3 searches. The only caller today passes `claims.turn` from a fresh `mint`. `SESSION_ID`'s comment already names this trap.
- **Fix:** use `fullmatch`, or `\Z`, for the new parameter.

### L4 (low): the eval planner runs plans the gateway would drop, so the gate scores searches no shopper gets
- **Where:** `/Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/query_plans.py:34-53`, compared with `understand.py:282-379`. **Rule:** decision 0022 §9 ("as a shopper gets it"). **Not unsafe.**
- **What is wrong:** `search_parts` calls `live.eligible_products` in-process with no bounds check and never builds a token. The gateway drops the filter or the whole plan in these cases:
  - **Price bounds:** the tool's `_bound` refuses any bound outside 0 < x ≤ 1,000,000. "around $10,000" gives `_around(1_000_000)` = (800000, 1200000), and "around $0.50" gives a minimum of 0. The gateway drops the price filter; the eval applies it.
  - **Token size:** `MAX_TOKEN_CHARS` makes the gateway fall back to the plain search.
  - **Claim shape:** `plan_ok`'s `PLAN_MAX_LIST = 16` refuses longer `c` / `s` lists.
  - **Bitmap:** product IDs outside the bitmap. Not reachable today; no fixture product is at or above P0250.
- **Fix:** route the eval through the same claim checks: `plan_claim` with a store-backed filter that applies `_bound`, then `mint_planned`.

### L5 (low): `LiveFilter.eligible` raises `AttributeError` on a reply that is not a dict
- **Where:** `/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/understand.py:329`. **Not unsafe:** it fails closed.
- **What is wrong:** `body.get("region")` runs even when `body` is not a dict; line 328 guards only `products`. A JSON list from mcp-catalog therefore escapes the "any failure drops the filter" contract.
- **How it fails:** the turn ends with the gateway's `internal` error rather than a plain search. Only a misbehaving mcp-catalog can send that reply.

### L6 (low, unconfirmed): the shopper's message can reach logs through the `anthropic` SDK's DEBUG logging
- **Where:** `/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/understand.py:255-261`; `rag_client.py:29` (`QUIET_LOGGERS`). **Rule:** hard rule 4 / NFR-04.
- **What is new:** this row puts a direct `anthropic` client in the gateway process, and its request body contains the shopper's message. Card text is already screened out by `query_ok`.
- **Why it matters:** `QUIET_LOGGERS` holds the MCP and httpx loggers at WARNING because "no shopper message reaches a log at any level". `anthropic` is not in that list. As far as I know, the SDK logs request options, including the JSON body, at DEBUG when `ANTHROPIC_LOG=debug` or the root logger is at DEBUG.
- **What I could not confirm:** whether any run configuration turns that on. I found no `basicConfig` or `ANTHROPIC_LOG` in the source.
- **Fix:** add `anthropic` to `QUIET_LOGGERS` and call `quiet_client_logs()` before the Haiku call.

### L7 (low): in live mode a timed-out Haiku call keeps running and is left out of TurnUsage
- **Where:** `/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/understand.py:258-263`. **Rule:** decision 0001 spend accounting. **Not unsafe:** the proxy ledger and `LLM_BUDGET_USD` still meter every request.
- **What is wrong:** `asyncio.wait_for` cannot cancel the `asyncio.to_thread` call. After the 700 ms deadline the thread finishes, and the anthropic client's default `max_retries=2` can send up to two more requests.
- **Effect:** only `TurnUsage` / `cost_usd` undercounts, so the turn's cost is reported low. Record and replay are not affected, because the deadline is live-only.

## Answers to the three focus areas

**1. evals/src**
- **Silent degradation:** a replay miss cannot degrade the gate silently; it stops the run with `SystemExit`. Other proxy or client failures can (M1).
- **Spend:** nothing in this path can spend. `proxy_config("replay", …)` (`query_plans.py:65`, `runner.py:705-706`) builds a config with no upstream, no key and hash matching (`LLM_REPLAY_MATCH` is not read). The client is `anthropic_client`, which sends the placeholder key. A miss returns 400 before the upstream branch (`proxy.py:446-460`).
- **RAG-03 and E1:** `_arms` and `cmd_enrichment_report` now call `_live_plain`, which has the old `_live` body word for word. With `NO_FILTER`, every added `_FILTER` clause is `NULL IS NULL`, so both measure what they measured before. Only the retriever id string changed (version 3). Separately, the planned side has M2 (embedding determinism) and L4 (it scores a different search than the gateway runs).

**2. The `qf` claim**
- **Widening access:**
  - The claim cannot widen access. The planned token takes audience and region from `caller` (`understand.py:364-376`).
  - `plan_parts` reads only `q`, `c`, `s` and `p`.
  - `retrieve_planned` takes region and audience from the token and calls `_regions`.
  - In `_FILTER`, the new clauses are each ANDed after `visibility = ANY` and the region test. The products clause is fully parenthesised, so the `document` pass-through cannot let internal documents through.
  - `_claims` refuses `qf` on any server other than northwind-rag.
  - An empty bitmap (`"0"`) narrows the search to documents only.
  - Hard rule 7 holds.
- **Holding or forging:** the CLI gets only the plain token (`turn.py:367`), and the signing key is not in the CLI's environment (`child_env`'s allowlist). Neither a model nor the CLI can hold or forge a planned token.
- **RAG-10 cap:** see L1 and L3. Otherwise the planned first search counts once, against the turn's own id.
- **Token size:** handled closed. `mint` raises above 2,048 characters, and `mint_planned` then returns None and the plain search runs. The plain search is the existing, already-authorised scope.

**3. `eligible_products`**
- **Price or stock leaks:** the tool returns `{"region", "products":[ids]}` only (`tools.py`). It reads the live store on every call, includes public products and public variants only, and uses the token's region and that region's own currency. No price or stock value reaches the model, a token or the index. The index's text and price band are not read. Hard rule 2 holds.
- **Least privilege:** the `shopper-query` agent gets only `eligible_products` on the catalog, nothing on northwind-rag, no resources, and has the forbidden-fragment list.
- **Model access:** no model can reach the tool. The tool is gated server-side by `_offered` (`mcp-catalog/server.py:112-130`). The only place that mints a `shopper-query` token is `LiveFilter`, which runs in the server process.
- **Note:** `shopper-query` is not in PRD §7. This follows the `shopper-portal` precedent and is recorded in decision 0022.

## Checked, no findings
- **Hard rule 1:**
  - test-writer's three fixes in f698484 are present: `Understander.plan` calls `query_ok` first (`understand.py:246-249`); `plan_ok` card-checks `q` (`mcp_token.py:134`); `_checked_part` calls `card_shaped`, and `_read_reply` calls `_parts_safe` (`query.py:738-781`, `understand.py:1749` in the diff).
  - `_first_searcher` also gates on `query_ok`, and the gateway's own check runs first.
  - Logs carry only type names and fixed strings.
  - The only residual gap is L2.
- **Hard rule 2:** no finding (see focus area 3).
- **Hard rule 3:** the fixtures and tests use synthetic data and standard test PANs only. The injection items in `rag04_queries.jsonl` are test data. I read them as data, not instructions.
- **Hard rule 4:** no secret is printed or logged. `LiveFilter.key` and the endpoint keys use `repr=False`. `understand.py` logs only failure names. `TurnUsage` adds only `qu_path` / `qu_ms`. The one gap is L6, unconfirmed.
- **Hard rule 5:** no product claims are generated. The model is not told which filters ran.
- **Hard rule 6:**
  - `build_request` escapes the message (`html.escape`, so `</shopper_message>` cannot close the tag). The region comes from the server.
  - Haiku replies through a JSON schema, and `validate_plan` checks it fully: exact key sets, vocabulary, integer minor units within 0 < x ≤ 1,000,000, the region's own currency, and 1 to 3 parts.
  - `strip_injection` keeps injected messages away from Haiku. It is a heuristic, but a bypass only yields a narrowing filter within the shopper's own scope.
  - Part texts become search queries only and never reach the assistant's prompt.
- **Hard rule 7:** no finding (see focus area 2).
- **Hard rule 8:** no finding (see focus area 3). The assistant's tool lists are unchanged.
- **Hard rule 9:** not applicable to this change. No state-changing tool was added; `eligible_products` is marked read-only.
- **Hard rule 10:** `eligible_products` returns structured errors (`invalid_argument`, `region_required`). The client tells `is_error` apart from an empty list.
- **Hard rule 11:** not applicable to this change (`prompt.py` is untouched).
- **0001, placeholder key:** child processes and the new in-process Haiku client present only the placeholder key (`llm.py:250-256`). `child_env` is unchanged.
- **0001, Makefile:** not changed in this diff, so it does not export the key.
- **0001, CI:** no workflow changes and no key in CI secrets. CI stays in replay, and the eval planner is hard-coded to replay.
- **0001, key file:** nothing in the diff touches `.env`, `LLM_ENV_FILE` or a worktree copy of either.
- **0001, budget caps:** no change to `LLM_BUDGET_USD`, `LLM_RUN_BUDGET_USD` or the proxy.
- **0001, scanner:** the diff adds no cassettes. The future `cassettes/query-understanding/` is under the scan root, which covers all of `cassettes/` (`llm-replay/cli.py:89-90`).
- **Gateway wiring** (`chat.py:113`, `:154`): the change only adds `Understander()`. The card check (`checked_message`) and the error handling are unchanged.
- **Record/replay determinism:** the deadline and the cache are both gated on `self.live`, which is only true for `LLM_MODE == "live"` and is off by default (`understand.py:224-238`, `:260`). The cache key holds only the message, region and prompt version, with no per-shopper data.

## Notes, not findings
- QU-097 and QU-100 in `evals/fixtures/rag04_queries.jsonl` carry `reviewed_by: "human"`. The code cannot show who set that. advisor-reviewer should confirm it came from the user's label review (CLAUDE.md: Claude writes only `model`).
- The test double's fallback in `northwind_mcp_rag/testing.py` (`CallerSearcher.search_planned`) silently runs the plain search when a test retriever has no `search_planned`, so a test could pass with its plan dropped. Production's `RagServer.search` raises instead (fails closed).

Files referenced:
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/query_plans.py
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py
- /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/understand.py
- /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/turn.py
- /Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/mcp_token.py
- /Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/tool_scopes.py
- /Users/rapido/Projects/northwind-shop/services/mcp-rag/src/northwind_mcp_rag/server.py
- /Users/rapido/Projects/northwind-shop/services/mcp-rag/src/northwind_mcp_rag/ledger.py
- /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/tools.py
- /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/live.py
- /Users/rapido/Projects/northwind-shop/services/rag/src/northwind_rag/retrieval.py
- /Users/rapido/Projects/northwind-shop/services/rag/src/northwind_rag/query.py
- /Users/rapido/Projects/northwind-shop/services/gateway/src/northwind_gateway/chat.py
