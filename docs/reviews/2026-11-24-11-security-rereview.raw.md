Scoped security re-review of row #11 (RAG-04) fixes. Diff: /tmp/claude-502/northwind-rereview-11.with-shas.diff, `base 6d86bb38438d75a38dd47dc70bf7a83d6fe82f36`, `head 330f379faf47ebb7ef329383cacefd7469715f68`.

Before reviewing, I re-read my earlier report (/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-24-11-security-review.raw.md), CLAUDE.md "Hard rules", decision 0001, decision 0012 and decision 0022. I also read the changed files in full where the diff did not give enough context, plus the installed `anthropic` SDK's logging code in `.venv`.

**There are no critical or high findings.** Nothing is unsafe.

- **Closed:** M1, L2, L3, L4 and L5 are closed. L7 is closed as far as a fix can reach.
- **Closed, with a narrow residual:** L1 (N2 below).
- **Not fully closed:** L6. The fix runs in the wrong order on the first Haiku call in a process (N1 below). The mechanism is now confirmed from the SDK source.
- **M2:** stays open at medium. **I do not agree with the reason given for deferring it**, because Guard does not vouch. Deferring it is still acceptable, but for different reasons (see "M2" below).

## Findings (most severe first)

### M2 (medium, open, carried over): Haiku's part texts have no frozen vectors
- **Where:** `/Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py:720-729` (`_frozen_queries`) and `:833-836` (`_frozen_texts`).
- **Rule:** decision 0012 (cross-platform ranking); decision 0022 Consequences ("added once W1 has recorded them").
- **Status:** unchanged by this diff. See "M2: the deferral reasoning" below.

### N1 (low, new; mechanism confirmed, run configuration unconfirmed): the L6 fix calls `quiet_client_logs()` before `import anthropic`, so the first Haiku call in a process can still log the shopper's message
- **Where:** `/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/understand.py:259-261`.
- **Rule:** hard rule 4 / NFR-04, the "no shopper message reaches a log at any level" contract in `rag_client.py:7-10`. Not hard rule 1: card text is screened out earlier by `query_ok`.
- **What is wrong:** the order is `quiet_client_logs()` (259), then `self._client_for(base_url)` (260), which runs `anthropic_client`. That is the only place `anthropic` is imported (lazily, `northwind_common/llm.py:252`); nothing else under `services/*/src` or `claude_agent_sdk` imports it. On import, the SDK runs `_setup_logging()` (`.venv/.../anthropic/__init__.py:143`). With `ANTHROPIC_LOG=debug`, that call does `logger.setLevel(logging.DEBUG)` on `anthropic` and `httpx2` (`anthropic/_utils/_logs.py:16-21`), which undoes the quiet call just made. `messages.create` then logs `"Request options: %s"` with `model_dump(options)` at DEBUG (`anthropic/_base_client.py:601-616`). That dump includes `json_data`, the request body, which holds the shopper's message.
- **How it fails:** an operator starts `make dev` with `ANTHROPIC_LOG=debug` in the shell to debug the proxy. The first shopper message routed to Haiku after the gateway starts is written in full to the gateway's stderr log. From the second call on, line 259 runs after the import and holds. The mcp-rag and catalog paths re-quiet before their own requests (`rag_client.py:89`, `understand.py:313`, `catalog_client.py:71`), so only the Haiku body leaks. I found no `ANTHROPIC_LOG` anywhere in the repo, so this needs an operator setting, which keeps it low.
- **Fix:** call `quiet_client_logs()` after the client is built, between `_client_for` and `asyncio.to_thread(client.messages.create, …)`. Alternatively, import `anthropic` in `rag_client.py` before its module-level `quiet_client_logs()` call.
- **Test gap:** `test_the_sdk_logger_is_held_quiet` (`evals/tests/test_rag04_review_fixes.py:123-127`) only checks that `quiet_client_logs()` sets the level. A useful pin: a scripted `client_for` that sets `anthropic` to DEBUG, standing in for the import, and asserts inside `create` that the level is WARNING.

### N2 (low, residual of L1): a refused `take` does not extend the held `exp`
- **Where:** `/Users/rapido/Projects/northwind-shop/services/mcp-rag/src/northwind_mcp_rag/ledger.py:36-38`.
- **Rule:** RAG-10, decision 0022 §7. Not unsafe.
- **What is wrong:** `max(exp, held)` is stored only on an allowed take. When `used >= self._max`, the function returns False without recording the later `exp` of the token that was refused.
- **How it fails:** the planned token A (later `exp1`) is never counted, for example because the server's planned first search failed in transport before `ledger.take`. The CLI's token B (`exp0`) then uses all 3 searches. A stolen A is refused at that point, but its `exp1` is not stored. The entry is purged at `exp0`, and A then gets a fresh 3 until `exp1`.
- **Reach:** A is never given to the CLI, so this needs A to be stolen from the process or the localhost hop. It also needs the uncommon order where A's first search was never counted. In the normal order (planned search first), the fix holds, and `test_the_cap_entry_keeps_the_later_expiry` pins that order.
- **Fix (stricter):** on the refusal branch, also store `(used, max(exp, held))` before returning False.

### N3 (low, records): the stated M2 reasoning is wrong about Guard and should not go into the records as written
- **Where:** the caller's M2 rationale, wherever it lands (PROGRESS.md, PR body, gate report).
- **Rule:** decision 0012's residual (lines 98-101).
- **What is wrong:** writing "Guard / eval-integrity-main would refuse them as unvouched" suggests that Guard vouches frozen vectors. It does not (details below).
- **How it fails:** a later row could rely on Guard to catch a planted vector, which decision 0012 explicitly says it never does.
- **Fix:** state the real reasons below.

## Fix-by-fix verification

**M1: closed.**
- `planner` (`evals/src/northwind_evals/query_plans.py:116-134`) counts every path not in `REPLAYED = {"model", "invalid"}` and raises `SystemExit`. It also raises on `state.fallback_attempts`, which is the same check as `cli.proxy_stops`, written inline. Misses are still reported first, with their own message.
- That covers the cases from my report: the refused-auth 401, refused-scrub, connection errors and client build errors all end as `"error"`. The planner also stops on `"timeout"`, `"skipped"`, `"rules"` and `"cache"`.
- `"invalid"` is deterministic under hash replay, including a replayed `refusal` stop reason, so accepting it is correct.
- **Fails closed on a card-shaped eval query:** such an item would now stop the gate as `"skipped"`. I grepped `evals/**/*.jsonl` for PAN-shaped digit runs and found none, so nothing trips this today.
- **Tests:** they cover all five failing paths and both accepted ones. No test covers the `fallback_attempts` branch. That is a minor gap, because a direct `anthropic` client never sends the CLI's fallback beta.

**L1: closed for the reported scenario.** `max(exp, held)` at `ledger.py:41` keeps the later expiry in the reported order (planned token first). The refusal branch is N2. The change only lengthens how long an entry is kept, so the cap is stricter.

**L2: closed.** `mcp_token.py:142-145` runs `contains_card_data` on every `c` and `s` item. `_claims` (`:261`) re-runs `plan_ok`, so the server refuses such a token as well. Tests are parametrized over `c` and `s`.

**L3: closed.** `_UUID4.fullmatch` is used at `mcp_token.py:203` (`mint`) and `:252` (`_claims`). `fullmatch` on `^…$` cannot consume a trailing `"\n"`, so `"<uuid>\n"` is refused. A test pins `mint`. I found no other `_UUID4.match` in `services/`.

**L4: closed.**
- `_around` (`services/rag/src/northwind_rag/query.py:529-534`):
  - The low bound is at most 0.8 × 10,000 units, so it is always ≤ `MAX_MINOR`. A low bound of 0 or less becomes None.
  - The high bound is capped at `MAX_MINOR`. A high bound of 0 or less becomes None.
  - Both callers drop a price whose bounds are both None (`:601-603`, `:825-826`).
  - Every bound that reaches `eligible_products` is now inside the tool's `_bound` range, 0 < x ≤ 1,000,000 (`mcp-catalog/tools.py:422-431`).
  - Range, min and max bounds come from `_minor` / `_checked_amount`, which are already range-checked.
  - **Side effect:** the gateway now applies (800000, 1000000) for "around $10,000", where it used to drop the filter. That is a narrower search in the shopper's own scope, so there is no security effect.
- `shopper_parts` (`query_plans.py:62-99`) mirrors `plan_claim` / `mint_planned` (`understand.py:341-385`) step by step:
  - the `is_plain` short-circuit;
  - `q` only when the part text differs from the message;
  - `c` / `s` from the same filter tuples;
  - `p` only when products is not None, so an empty set gives `"0"`;
  - a bitmap ValueError drops only that part's `p`;
  - `[{}]` gives the plain search;
  - a `mint` ValueError, from `plan_ok` or `MAX_TOKEN_CHARS`, gives the plain search.
- **Token size:** the measured token matches the gateway's in every claim except the signature key:
  - the agent is `"shopper-assistant"`, which is `rag_client.AGENT`, the same as `RAG_AGENT`;
  - the audience is shopper;
  - the turn is a 36-character UUID, generated rather than passed, but the same length;
  - `exp` has 10 digits;
  - there is no `sid`.
  The signature is always 43 base64url characters, whatever the key. So the length check is exact today.
- **Drift note, not a finding:** if the gateway's planned token ever gains `sid` or another claim, `shopper_parts` would under-measure by that claim's length.
- **Tests:** they cover open bounds, the capped top, the plain plan and the oversized plan. The bitmap-drop path has no new test, but nothing reaches it with the fixtures.

**L5: closed.** `understand.py:331-332` returns None for any non-dict JSON before `.get`. No test was added for it; this is a minor gap.

**L6: partly closed.** `anthropic` is in `QUIET_LOGGERS` (`rag_client.py:30-38`), and `quiet_client_logs()` is called on every Haiku call. The first call in a process is still exposed (N1).

**L7: closed as far as a fix can reach.** In live mode, `max_retries=0` is passed through `anthropic_client(base_url, **retries)` (`understand.py:221-223`). `anthropic_client` still forces `api_key=PLACEHOLDER_API_KEY` and the proxy `base_url` (`llm.py:250-256`). A duplicate `api_key` keyword would raise a TypeError, so kwargs cannot replace the key. Record and replay keep the SDK default. The single request that a timeout abandons is still left out of `TurnUsage`, as `wait_for` cannot cancel a thread. The proxy ledger meters it, so it does not need fixing.

## M2: the deferral reasoning

**I disagree with the stated reason.** `Guard / eval-integrity-main` never vouches frozen vectors:
- `.github/workflows/guard/integrity-main.py:147-157` imports and runs only `integrity_problems`, `added_known_failures` and `replaced_refinements`. It runs on the system `python3 -I`, with no model installed.
- Its only frozen-vector check is `changed_frozen_vectors` (`integrity.py:123-149`, called at `:354`). That is add-only: a key may not be changed or removed. A newly added key passes.
- `_frozen_texts` and `unvouched` run only in `cmd_integrity._vouch` (`cli.py:839-859`). That is `make eval-integrity` and `CI / eval-gate`'s "Eval integrity" step (`ci.yml:123-124`), and both run the PR's own `evals/src`.
- Decision 0012, lines 98-99, says it directly: Guard "enforces add-only after merge but never vouches".
- `Guard / eval-gate-main` cannot object either. Main's `_live` runs the plain search, so it never looks up a part-text key, and `load_frozen` does not refuse unknown keys.

So a PR that extends `_frozen_queries` with the replayed part texts would pass Guard. Its own `make eval-integrity` would vouch the new keys with its own `_frozen_texts`.

**Deferring is still acceptable, for these reasons instead:**
1. **There is nothing to freeze yet.** `cassettes/query-understanding/` does not exist (W1 has not been recorded), so no Haiku part text exists. Without that cassette, the planner stops on a replay miss for every Haiku-routed item, so no drifted ranking can be scored or promoted yet.
2. **Keeping the vouch change separate avoids a known residual.** Extending `_frozen_texts` and adding the vectors in the same PR is exactly decision 0012's residual: "a PR that edits the vouch and plants a vector together is visible only through `make ship`'s control-surface listing". Landing the `_frozen_queries` / `_frozen_texts` change first, on its own, keeps the vouch change reviewable apart from the vectors. That is a good reason for an integrity-first change, but not the reason given.

**Leaving M2 open at medium is acceptable if all of these hold:**
- (a) It is logged in PROGRESS.md "Open items" with a named owning row.
- (b) The `_frozen_queries` extension and the vectors land after W1 and **before any baseline that scores the planned first search is promoted** (W5). Otherwise a macOS-promoted baseline can drift on Linux CI, which is the PR #30 failure.
- (c) The gate report says that `make embed-cache CHECK=1` cannot see this gap.

## `_CHECK_KEY`: not a secret-handling problem

`query_plans.py:59`, used at `:88-96`.
- It is a fixed literal in eval code. It is never read from or written to the environment, never passed to a verifier, and never logged or printed.
- The token it signs is discarded straight away. It is used only for `mint`'s length check, and HMAC-SHA256 output has the same length for any key.
- It is not the `NORTHWIND_MCP_TOKEN_KEY` value. `make dev` mints a fresh key per run, so no server accepts a token signed with it.
- It does not match an API-key pattern, and `evals/src` is not a scan target.
- **Only hypothetical misuse:** someone could copy the literal into a server's environment as a signing key. Its name and comment-free placement make that unlikely. The leading underscore keeps it module-private.

## Checked, no findings
- **Hard rule 1:** no finding. L2 adds card checks to `c` and `s`. `shopper_parts` passes part texts through `mint`, so through `plan_ok`'s card check. The test PAN `4111111111111111` is a standard test card, used only as a refused input.
- **Hard rule 2:** no finding. Bounds come only from the message or Haiku's plan. Products come from `eligible_products` (in-process fixtures in the eval; live mcp-catalog in the gateway). No price or stock value enters a token, the index or a prompt.
- **Hard rule 3:** no finding. Only synthetic data and test values are used.
- **Hard rule 4:** one low finding, N1. `_CHECK_KEY` is not a secret (see above). The new report fields `mode` and `model_ms` in `.llm-proxy-log/rag04-filter-accuracy.json` hold no text, and that directory is scanned.
- **Hard rule 5:** not applicable to this change.
- **Hard rule 6:** not applicable. The prompt and Haiku's request are unchanged.
- **Hard rule 7:** no finding. Audience and region are still taken from the caller and the token. The ledger change makes the cap stricter only.
- **Hard rule 8:** not applicable. No tool scope changed.
- **Hard rule 9:** not applicable. No state-changing tool.
- **Hard rule 10:** no finding. L5 keeps the "any failure drops the filter" contract. The tool error envelopes are unchanged.
- **Hard rule 11:** not applicable.
- **0001, placeholder key only; only the proxy holds the real key:** no finding. `anthropic_client` still forces the placeholder, and `**retries` holds only `max_retries`.
- **0001, Makefile does not export the key:** not applicable. The Makefile is not in the diff.
- **0001, no key in CI secrets; CI in replay:** not applicable. No workflow change. The planner is hard-coded to `proxy_config("replay", …)`.
- **0001, key file never copied into a worktree:** not applicable. Nothing touches `.env*` or `LLM_ENV_FILE`.
- **0001, budget caps enforced and not raised:** no finding. No cap changed. L7 reduces unmetered SDK retries in `TurnUsage`.
- **0001, cassettes still covered by the scanner:** not applicable. No cassettes are added. The future `cassettes/query-understanding/` is under the scanned root.

## Notes (not findings)
- **Pre-existing, not introduced here:** `_claims` still uses `_REGION.match` with `^[A-Z]{2,3}$` (`mcp_token.py:250`), so `"US\n"` would pass. Only a key holder can sign that region, and `mint` takes the region from a server-built `Caller`, so it is not reachable. It is the same trap as L3, if hardening wants to sweep it.
- **Prompt injection:** no text in the diff, tests or fixtures tried to steer this review.

Files referenced:
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/query_plans.py
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/integrity.py
- /Users/rapido/Projects/northwind-shop/evals/tests/test_rag04_review_fixes.py
- /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/understand.py
- /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/rag_client.py
- /Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/mcp_token.py
- /Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/llm.py
- /Users/rapido/Projects/northwind-shop/services/mcp-rag/src/northwind_mcp_rag/ledger.py
- /Users/rapido/Projects/northwind-shop/services/rag/src/northwind_rag/query.py
- /Users/rapido/Projects/northwind-shop/services/rag/src/northwind_rag/embedding.py
- /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/tools.py
- /Users/rapido/Projects/northwind-shop/.github/workflows/guard.yml
- /Users/rapido/Projects/northwind-shop/.github/workflows/guard/integrity-main.py
- /Users/rapido/Projects/northwind-shop/.github/workflows/ci.yml
- /Users/rapido/Projects/northwind-shop/docs/decisions/0012-hybrid-retrieval.md
- /Users/rapido/Projects/northwind-shop/docs/decisions/0022-query-understanding.md
- /Users/rapido/Projects/northwind-shop/.venv/lib/python3.12/site-packages/anthropic/_utils/_logs.py
- /Users/rapido/Projects/northwind-shop/.venv/lib/python3.12/site-packages/anthropic/_base_client.py
