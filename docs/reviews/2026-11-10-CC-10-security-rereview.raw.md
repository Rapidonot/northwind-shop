# CC-10 scoped security re-review, raw output (2026-11-10)

Saved verbatim as returned by `security-reviewer` (the user's D5). Reviewed diff:
`docs/reviews/2026-11-10-CC-10-security-fixes.patch`, base `071682e85d954d806a6cc097a2942db3c8a90a2e`, head
`d5e144b2a722ae36367ef39db1e9f932ee1adb0a`.

---

## Scoped re-review: fixes for the CC-10 security findings

**Result:** one new **medium** finding. The L5 fix is a regression in the hard rule 1 card check on mcp-rag. Every other fix closes its finding as I specified and adds nothing new. The triage of the unfixed findings is acceptable.

**What I read:** the fix diff `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-10-CC-10-security-fixes.patch` (base `071682e85d954d806a6cc097a2942db3c8a90a2e`, head `d5e144b2a722ae36367ef39db1e9f932ee1adb0a`), the triage table in `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-09-CC-10-evidence.md` (lines 312-334), `services/common/src/northwind_common/cards.py` and the existing card tests in `services/mcp-rag/tests/test_retrieve.py` and `tests/mcp/test_cc10_contract_errors.py`. I did not run anything.

---

## New finding

### M1 (medium). Running the card check on the JSON dump misses cards the old check on the raw query caught
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/mcp-rag/src/northwind_mcp_rag/retrieve.py:53-54` (patch lines 246-247). `contains_card_data(json.dumps(dict(args), ensure_ascii=False, default=str))` replaced the old `contains_card_data(query)`, which the patch removes at its lines 254-255.
- **Rule:** hard rule 1 ("No PAN/CVV in … tool arguments"). It also breaks the claim just added to threat-model Boundary 3 and to the mcp-rag data-flow row: that a model-written query is checked on the server "over every argument".
- **Why it misses:**
  - `json.dumps` escapes newline, tab, carriage return, backspace and form feed as two characters, `\n`, `\t`, `\r`, `\b` and `\f`. It escapes other control characters as `\u00XX`, whatever `ensure_ascii` is set to. The second character of each escape is a letter, or for `\u00XX` digits too.
  - `cards.py` is built to join digits across one separator, and its own comment names "tab, newline" (`cards.py:21-24`).
  - After escaping, two things fail:
    - `_DIGIT_GROUP` stops at the letter, so every group is 4 digits and none reaches 13.
    - `_wide_groups_pass` cannot see a run that follows `\n`, because a letter precedes it (`_STANDALONE_RUN`, `cards.py:26`).
- **How it fails:** the model calls `retrieve_context` with `{"query": "4242\n4242\n4242\n4242"}`, a Luhn-valid synthetic PAN split by newlines. A tab-split PAN behaves the same way.
  - Before the fix, the server refused this as `card_data`.
  - After the fix, the dump reads `4242\n4242\n4242\n4242` with a literal backslash and `n` between the groups, and the check finds nothing. The bad-query check passes it, the ledger counts it, and the query goes on to fastembed and the Postgres FTS query.
  - Since L6, the server is the only check on a query the model writes (`tools.py` docstring). So this path now has no working card net.
  - The shopper's own message is unaffected: the gateway and `search_sources` still check the raw text.
  - No test covers this. The new tests use only space-separated or unbroken digits (`test_cc10_review_fixes.py:89-95`), and so do the old ones (`test_retrieve.py:120`, `test_cc10_contract_errors.py:103`).
- **Why medium:** the fix introduced it, and it weakens an existing hard rule 1 check on the main argument below where it stood before the fix. Exposure is still limited: there is no log or echo, the data is synthetic, and the model would have to write the card itself.
- **Fix:**
  - Run `contains_card_data` on **raw** text, not on the JSON encoding. Walk `args` recursively, collect every key and every scalar leaf (`str(value)` for numbers and bools), and check each one.
  - Also check the leaves joined with `"\n"`, so that a card split across fields still fails closed.
  - Keep the dump only if you want it as an extra check, never in place of the raw one.
  - Add tests: a newline-split PAN and a tab-split PAN in `query` and in an extra key, plus a `\u0001`-separated case. Also add a parity test that runs the shared card corpus from `services/common` tests through the handler's argument check and expects the same verdicts as `contains_card_data` on the raw string.

---

## The fixes, each checked

- **L2: closed.** `ledger.py:32-33` returns `False` when `exp <= now`, before eviction and counting. An expired turn can no longer open a new count, and nothing is added to the ledger. The two tests pin both cases (`test_cc10_review_fixes.py:34-54`). No new issue: expired entries are still evicted on the next valid `take`.
- **L3: closed. The new drop of unknown scope types adds nothing.**
  - Only `lifespan` passes now (`server.py`, patch lines 273-281).
  - A websocket gets a `websocket.close` with code 1008 before any accept. Under ASGI, the server must then reject the handshake with a 403, so nothing inside is reached.
  - Any other scope type returns without calling the app or sending anything. uvicorn sends no such scopes, and returning early grants nothing. HTTP still goes through the token check unchanged.
  - The log line is fixed text.
- **L5 (part): the length cap is sound.** `MAX_QUERY_CHARS = 2000` is pinned to the gateway's `MAX_MESSAGE_CHARS`, and an over-long query becomes `bad_query` before the ledger. The card part is not closed: see M1. Ignoring extra keys without card data is the approved B2 behaviour, so leaving it to the user is acceptable. It should appear in the gate report as an open low item awaiting the user's decision.
- **L6: closed.** The `tools.py` docstring and comment, Boundary 3 and the data-flow row now describe both paths correctly. The "over every argument" wording becomes accurate once M1 is fixed.
- **L9: closed.** `dev-portal` runs under `env -u NORTHWIND_MCP_TOKEN_KEY` (`Makefile:103`). That also strips a copy exported from an included `.env`, and both macOS and GNU `env` support `-u`. The test pins the recipe text.
- **Unconfirmed item 3: closed.**
  - `rag_client.py` builds `httpx2.AsyncClient(headers=…, timeout=<SDK defaults>, trust_env=False)`. Apart from `trust_env`, that is exactly what `create_mcp_http_client` set: redirects stay off, and the transports' same-origin redirect rule is unchanged.
  - `httpx2` and `httpcore2` are now in `QUIET_LOGGERS`.
  - The dead-proxy test covers it. The CLI side was already covered, because `env -i` drops proxy variables and `NO_PROXY` is set.
- **Unconfirmed item 1: tested in part. Acceptable as triaged.**
  - `test_cc10_token_exposure.py:145-177` shows the token's signature is in no file under the runtime root, and not on the test process's stdout or stderr. `capfd` sees the CLI's inherited stderr, and the test checks the CLI really called the tool.
  - Two gaps remain, both low and unconfirmed:
    - It does not scan `HOME`. `child_env` passes `HOME` through (`llm.py:33`), so files the CLI might write outside `CLAUDE_CONFIG_DIR` are not covered.
    - It covers only the success path. The case I raised, `--verbose` stderr when an MCP connection fails, for example a 401, is not tested.
  - Optional: repeat the same assertions on a turn whose token is refused, and scan a temporary `HOME`.

## Triage of the unfixed findings: acceptable
- **L1:** accepted as low under the plan's rule, so there is no stop 6.
- **L4:** logged in Boundary 3, owner #10b (a signed `srv` claim). That is the right owner, because nothing shares the key until then.
- **L7:** recorded as a plan deviation for `advisor-reviewer` and the gate report, with the per-audience key split owned by #19 or #24. Acceptable, because the scope grants no tools and nothing mints it.
- **L8:** logged in Boundary 3 as lab-only, the same loopback trust as the dev LLM proxy. Acceptable.

No high or critical finding is open, so stop 10 is not triggered. M1 does break a hard rule 1 check, so under routine-row triage it is "unsafe at any severity: fix it". The fix stays inside `retrieve.py` and does not change the design.
