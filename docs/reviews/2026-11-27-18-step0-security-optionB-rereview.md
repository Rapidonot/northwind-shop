# Row #18 step 0: security-reviewer, scoped re-review of option B's fixes (verbatim)

Diff: `/tmp/claude-502/northwind-review-18-optionB-2.diff` (working tree against `704be22`, 2026-11-27). The report
follows unchanged; the triage is at the end of `docs/reviews/2026-11-26-18-step0-triage.md`.

---

Scoped re-review: row #18 step 0, option B fixes (working tree against 704be22)

Diff read: /tmp/claude-502/northwind-review-18-optionB-2.diff. Before this pass I re-read the "Hard rules" in /Users/rapido/Projects/northwind-shop/CLAUDE.md and /Users/rapido/Projects/northwind-shop/docs/decisions/0001-llm-first-order-and-record-replay.md. I also read these in full or in the relevant parts: holds.py, server.py, tools.py (all handlers), cart.py (handlers and views), live.py `eligible`, and BE-04's postgres.py `_settle_in` and schema.sql.

Overall: M1, M2 and L1 are closed. L2 is closed in part, and the part left open is an accepted won't-do. I found no new critical, high or medium finding, and three new lows.

## Status of each prior finding

**B-M1: closed.** The query is at /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/holds.py:76-87. I walked every reservation state against postgres.py `_settle_in` (lines 264-351) and schema.sql:
- **Held, live:** counted once by the first clause.
- **Committed, normal path:** the holds are swept, `on_hand` is lowered and the status is `committed`. The OR clause counts it, and the fixture base never drops, so nothing is subtracted twice.
- **Commit after expiry that re-takes the units (postgres.py:311-331):** swept and `committed`, so it is counted. That is correct.
- **Commit after expiry that fails (`EXPIRED`, postgres.py:313-320):** the status stays `held` and the holds are swept or past expiry, so it is not counted. That is correct.
- **Commit then release:** a release on a non-`held` reservation does nothing (postgres.py:277-279). The units stay counted, which is correct because they are still sold.
- **Partly swept reservation:** `_SWEEP` only sweeps rows where `expires_at <= now`. The reader's live clause needs `expires_at > now`, so the unswept rest of an expired reservation is not counted either. This is the same predicate as BE's `_AVAILABLE`; the only difference is clock skew between processes.
- **Region:** it comes from the stock row (`s.region`), never from `r.region`. That matches how the fixture base is summed, by the stock rows' region.
- **Double counting through the joins:** not possible. The primary keys are holds (idempotency_key, variant_id), stock (variant_id, node) and reservations (idempotency_key), so each hold row joins once, and an OR inside one WHERE counts a row once.
- **Tests:** test_18_postgres_holds.py:771-791 and 794-815 cover it, and mutant M-q1 fails both.

**B-M2: closed.**
- **Off the loop:** server.py:183-187 calls `asyncio.to_thread(self.holds.live, claims.region)`. `to_thread` copies the context, and `live` does not use `_READING`.
- **ContextVar scope:** `_Reading` is set in call_tool's own task, around a handler that is awaited directly, so the handler sees it. It is reset in `__exit__`, also on an exception. Each MCP request runs in its own task with a copied context, so concurrent calls cannot see each other's reading. cart's `server.reading(region)` (cart.py:348 and 445) sets the same value again and resets it. That nesting is harmless.
- **No stock read left on the loop:** every handler takes its region from the claims: check_stock at tools.py:308, eligible_products at tools.py:451, and cart's `_caller` at cart.py:133-139. So `held()`'s check `taken[0] == region` (server.py:154) always hits, and server.py:156 is never reached inside a call. When `claims.region` is None, call_tool skips the read, and every stock handler raises `region_required` before it reads stock. Of the tools not marked, search_products, get_product, get_variant and get_price read no stock (tools.py:204-301).
- **read_only before the transaction:** `conn.read_only = True` (holds.py:120) is set right after connect, before any execute. psycopg's `transaction()` then issues `BEGIN READ ONLY`, and `SET LOCAL` (holds.py:122) applies inside that transaction.
- **Timeouts:** a lock or statement timeout raises `LockNotAvailable` or `QueryCanceled`. Both are subclasses of `psycopg.Error`, which holds.py:129-130 turns into `unavailable()` (transient and retryable). `UndefinedTable` is caught earlier, which is correct.
- **Tests:** the lock test is test_18_postgres_holds.py:818-834, with a bound under 10 s. M-q2 hangs past a 30 s watchdog, and M-q3 fails test_18_postgres_holds.py:885-930.

**B-L1: closed.** There is one warning (holds.py:124-128) that names only `dbname`, parsed with `conninfo_to_dict` at holds.py:109-112. It never logs the URL or the password, and a parse failure falls back to "?". The `_warned` flag can race between worker threads and log twice. That is harmless.

**B-L2: closed in part.**
- **Done:** the regex now ignores case and accepts quoted names (test_tools.py:548). It reads every non-docstring string, and GRANT and COPY are added to the write ban (test_tools.py:551). A schema-qualified quoted name such as `"public"."x"` gets `public` captured, so the check fails closed.
- **Still open, accepted as won't-do under decision 0021 and owned by the PR body:**
  - the database role;
  - another module can still reach `server.holds._connect()` without ever writing "psycopg" in its text.
- One more gap is in the new findings below (N-L2).

## New findings (all low)

**N-L1. Low. Hard rule 10. A stock tool now fails as transient when the database is down, even when the call needs no stock or should get a non-transient error.**
- **Where:** server.py:183-185 reads the holds before the handler runs, for every `reads_stock` tool. That includes eligible_products, marked at tools.py:490, even though it only uses the reading when `in_stock` is set (tools.py:461).
- **How it fails, case 1:** with the database down, `eligible_products({"price_max": 5000})`, a price-only filter, now returns `inventory_unavailable`. Before this fix it returned the product IDs.
- **How it fails, case 2:** with the database down, `check_stock` on a variant that is invisible or not sold in the region now returns transient and retryable. It should return `variant_not_found` or `not_sold_in_region` (tools.py:307-311), so the caller retries a call that can never succeed. The same happens to `remove_from_cart` with an unknown line (`not_in_cart`) and to a validation error.
- **Fix:** only mark eligible_products as reading stock when `in_stock` is true, or take the reading lazily in the worker thread. Neither change weakens a check.

**N-L2. Low. Hard rules 7 and 8 (defence in depth). The table allowlist does not see a comma join.**
- **Where:** test_tools.py:548 captures only the first name after FROM, JOIN, INTO, UPDATE or TABLE.
- **How it fails:** `FROM inventory_holds h, rag_index_manifest m` captures `inventory_holds` only, so the test passes. A name built through `psycopg.sql.Identifier(...)` also passes. `rag_chunks` is still caught by the substring ban, so this gap does not reach the index chunks.

**N-L3. Low. Hard rule 2. The committed clause has no end and is tied to BE-04's base, so the two can drift apart.**
- **Where:** holds.py:84 counts every `committed` reservation ever made, with no expiry. PROGRESS.md row #25 item (1) is marked "Closed" on the assumption that BE-04's `inventory_stock` is loaded from the same fixtures.
- **Reload case:** BE-04's `load_stock` upserts `on_hand = EXCLUDED.on_hand` (postgres.py:110-113). If the stock is reloaded after a sale, BE-04's `on_hand` goes back to the fixture value, but the catalog keeps subtracting the old committed units. The catalog then understates stock forever. That is the safe direction, with no oversell, but it is a wrong live figure, and no test or guard covers it.
- **Future change (unconfirmed):** if #19's cancel_order (open item 3) restocks `on_hand` and leaves the status as `committed`, the catalog undercounts. If a later change makes `FixtureCatalog.set_stock` or the catalog's base follow BE's `on_hand`, every sale is subtracted twice.
- **Ask:** name an owner (#19) in PROGRESS.md for keeping the reader's status predicate in step with any cancel or restock path. Keep "Closed" only with that dependency written down.

## Checked, no findings
- Hard rule 1 (card data): not applicable to this change.
- Hard rule 2: price paths are unchanged. On stock, M1 is closed and the residual is N-L3.
- Hard rule 3 (synthetic data): the test rows and postal codes are synthetic, and `u:p@127.0.0.1:1` is a dummy.
- Hard rule 4 (secrets): no finding. `DATABASE_URL` is read from the environment only (app.py). The warning logs only `dbname`. Errors are raised `from None` with fixed text, and server.py:192 logs only the class name.
- Hard rule 5 (product claims): not applicable.
- Hard rule 6 (retrieved text as data): not applicable.
- Hard rule 7: no index read exists in code. The gaps are N-L2 and the L2 residual.
- Hard rule 8 (tool scoping): `reads_stock` is a ToolSpec field, not part of `t.Tool`. The tool list and its sha256 pin are unchanged, the permission check still runs before the read (server.py:178-180), and no grant was added.
- Hard rule 9 (idempotency and limits): the cart's idempotency path is unchanged, and the reader is read-only.
- Hard rule 10: timeouts and an unreachable database become structured transient errors. The one finding is N-L1.
- Hard rule 11 (AI disclosure): not applicable.
- Child processes get a placeholder key only, and only the proxy holds the real key: not applicable to this change.
- The Makefile does not export the key: not applicable (the Makefile is unchanged).
- No key in CI secrets, and CI runs in replay: not applicable (the workflows are unchanged).
- The key file is never copied into a worktree: not applicable.
- Budget caps are enforced and not raised: not applicable. No cap changed.
- Cassettes are still covered by the secret/PAN scanner: not applicable. No cassette or scanner changed.
- Exit 3: live_holds.py:17-19, the runbook and PROGRESS.md agree that it always blocks a window.
- Data versus instructions: the new log entries (M-q1 to M-q3), the triage and the tests hold no text that tries to steer the review.

Unconfirmed: the #19 cancel or restock interaction in N-L3. It depends on code that does not exist yet.
