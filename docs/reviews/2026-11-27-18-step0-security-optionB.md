# Row #18 step 0: security-reviewer, scoped read of option B and the exit-3 decision (verbatim)

Diff: `/tmp/claude-502/northwind-review-18-optionB.diff` (working tree against `704be22`, 2026-11-27). The report
follows unchanged; the triage is at the end of `docs/reviews/2026-11-26-18-step0-triage.md`.

---

Scoped security read: row #18 step 0, option B and the exit-3 decision
Diff: /tmp/claude-502/northwind-review-18-optionB.diff (working tree against 704be22). I re-read the CLAUDE.md "Hard rules" and decision 0001 for this pass. I also read the changed files in full where the diff lacked context: holds.py, app.py, live_holds.py, server.py, and the parts of tools.py, live.py and backend inventory/postgres.py that the reader depends on.

Overall: there are no critical or high findings. Two mediums need an owner or a fix: committed sales are not subtracted, and the reader blocks the server's only event loop. The narrowed test is still a real guard for rag_chunks, but it is a text check and not a database privilege boundary.

## Findings

**Critical: none.**

**High: none.**

**M1. Medium. Hard rule 2 (RAG-09). The catalog overstates stock by every committed reservation, yet PROGRESS.md now marks the item closed.**
- Where:
  - /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/holds.py:69-77 counts `held` reservations only.
  - /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/server.py:145 subtracts them from `self.catalog.stock(...)`.
  - That base figure is `FixtureCatalog`'s in-memory total, seeded once from `stock.jsonl` (/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/live.py:180-182, 203-205).
- What is wrong: BE-04's commit marks the holds swept, sets the reservation to `committed`, and lowers `inventory_stock.on_hand` (/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/inventory/postgres.py:323-348). The catalog never reads that `on_hand`. So once a sale commits, the reader stops subtracting it and nothing else takes its place.
- How it fails: US has 5 units of variant V. Shopper A reserves 5, so `check_stock` reads 0. A's checkout commits: the database `on_hand` goes to 0 and the holds are swept. The reader now returns {}, so `check_stock`, `search_products`' `in_stock` and the cart's alternatives say 5 in stock while BE-04 has 0. Stock even reappears after the sale.
- Backstop: shopper B's checkout is refused as `out_of_stock` at reserve. So this is the same "safe but late" pattern as L1, but it now hits every committed sale.
- The test enshrines it: `test_released_committed_and_expired_holds_are_not_read` (/Users/rapido/Projects/northwind-shop/services/mcp-catalog/tests/test_18_postgres_holds.py:111) asserts committed means {}. That is right for BE-04's `available`, which subtracts from the database `on_hand`, but wrong for the catalog's fixture base.
- Reachability today, confirmed by grep: no non-test code constructs `PostgresInventory`, so no running service commits yet. It becomes real when checkout (#19 or #21) wires BE-04.
- Ask:
  - Reopen PROGRESS.md's row #25 item (1) as partial, with an owner.
  - Either the catalog takes `on_hand` from `inventory_stock` when the reader is active, or commits push into `FixtureCatalog.set_stock` ("the backend later", live.py:207).
  - Add a test: reserve, commit, then catalog stock equals fixture minus committed.

**M2. Medium. Hard rule 10. The sync reader runs on the single-worker server's event loop, with no statement or lock timeout.**
- Where:
  - /Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/holds.py:94 and 102-104 call blocking `psycopg.connect` and `execute`.
  - They are called from async handlers: tools.py:312 (`check_stock` through `server.on_hand`), tools.py:460, and server.py:152 and 158. Nothing goes through `to_thread`.
  - The server runs `workers=1` (/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/app.py:39).
  - BE-04's own reads set `lock_timeout = '2s'` and `statement_timeout = '5s'` (postgres.py:74). The reader sets neither.
- How it fails:
  - Database down or blackholed: each stock read stalls every concurrent tool call of every shopper for up to `connect_timeout=3` seconds. If `localhost` resolves to two addresses, that can be once per address (unconfirmed).
  - Lock: anything holding ACCESS EXCLUSIVE on an `inventory_*` table (TRUNCATE, ALTER, VACUUM FULL, a migration) blocks the SELECT indefinitely. The whole MCP server then hangs instead of returning the transient `inventory_unavailable` error that hard rule 10 and holds.py's docstring promise.
- Ask:
  - Run the reading off the loop (`asyncio.to_thread`, or an async connection).
  - Use `SET LOCAL` with BE-04's timeouts inside a transaction, so that a timeout raises `psycopg.Error` and becomes `unavailable()`.
  - Add a test where a lock is held and the call returns `transient` within a bound.

**Low: four findings.**

**L1. Low. Hard rules 2 and 10. Absent tables read as no holds and are invisible to the operator.** (/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/holds.py:105-106)
- The catch is `UndefinedTable` for any of the three tables, so a half-applied schema also reads as {}.
- How it fails: `DATABASE_URL` names a database that exists but has no BE-04 schema, or a `search_path` that omits it (for example a scratch database such as `northwind_16`, or `options=-csearch_path=` in the URL or `PGOPTIONS`). If BE-04's holds live in another database, every stock read overstates by those holds, with no log and no error.
- Why this is Low:
  - Overstatement is backstopped at reserve.
  - No running service creates holds today.
  - The window guard's exit 3 now blocks, so a recording cannot open on such a database. That only holds if the guard and the runner's mcp-catalog read the same database (open item N2, not yet wired).
- Asymmetry: the guard defaults to `DEFAULT_DATABASE_URL` when `DATABASE_URL` is unset (live_holds.py:57), while `app.holds` falls back to `NoHolds`. With the variable unset, the guard checks `northwind` and the reader reads nothing. This direction is harmless for recordings, but they do differ.
- Is the choice acceptable? It is defensible for GitHub's browser job, which sets `DATABASE_URL` to an index-only database (/Users/rapido/Projects/northwind-shop/.github/workflows/ci.yml:38/81/116/155). Suggestion, which does not weaken anything: one warning when tables are absent, naming only the database (as the guard does, never the URL).

**L2. Low. Hard rules 7 and 8 (defence in depth). The narrowed guard is a text check, and the connection's privileges are not scoped.** (/Users/rapido/Projects/northwind-shop/services/mcp-catalog/tests/test_tools.py:545, 554, 557)
- What still holds: `rag_chunks` and `northwind_rag` are still banned in every source file, including holds.py. The reader's SQL is a fixed constant whose only parameters are bound (`region` from the signed claims, `now`). So I found no code path from the catalog to the index.
- Gaps in the guard:
  - The table allowlist and the write ban only apply to `FROM|JOIN|INTO|UPDATE|TABLE` in upper case, followed by an unquoted name. Lower-case SQL (`from rag_index_manifest`), a quoted name (`FROM "rag_index_manifest"`), or `psycopg.sql.Identifier` pass both tests. Only `rag_chunks` is caught, by the substring ban.
  - The allowlist covers holds.py only. Another module could call `server.holds._connect().execute(...)` without ever writing "psycopg" in its text.
- Database side: the connection uses the same role as the index (`northwind`). `conn.read_only = True` is a session setting that any SQL on that connection could undo. It is not a privilege boundary, so at the privilege level this connection can SELECT `rag_chunks`.
- Ask: make both regexes case-insensitive and quote-aware. Later, give the catalog a role with SELECT on the three `inventory_*` tables only. Both tighten things; neither weakens a check.

**L3. Low. Steering to another database or schema is operator-only.** The reader follows `DATABASE_URL` and the libpq environment (`PGOPTIONS`, `PGSERVICE` and so on). Unqualified table names resolve through `search_path` (`"$user", public` by default), so a schema named after the role could shadow `public.inventory_*`. No shopper or tool input reaches the connection string or the schema. This is consistent with the guard, which resolves the same way. No change needed beyond L1's visibility.

**L4. Low. Note on the failing-first log.** The diff deletes five stray lines of CPython stdlib comments from the M-m1 entry (diff lines 52-56). They were harmless captured noise, not instructions. I found no text in the diff, log or tests that tries to steer the review.

## Checked, no findings
- Hard rule 1 (card data): not applicable to this change.
- Hard rule 2: no price or promotion path is touched. On stock, see M1 and L1.
- Hard rule 3 (synthetic data): the test fixtures are synthetic. The URLs `northwind-local`/`northwind-ci` are local dev and CI values that predate this diff.
- Hard rule 4 (secrets): checked, no finding. `DATABASE_URL` is read from the environment only and never printed or logged. Errors are `from None` with a fixed message. A non-psycopg exception is logged by class name only (server.py:182). The test URL `u:p@127.0.0.1:1` is a dummy.
- Hard rule 5 (product claims): not applicable.
- Hard rule 6 (retrieved text as data): not applicable. No retrieved text reaches the reader.
- Hard rule 7: no index read exists in code. Defence-in-depth gaps are in L2.
- Hard rule 8 (tool scoping): the tool list is unchanged (sha256-pinned test untouched), and no new tool or grant was added. Database-role scoping is in L2.
- Hard rule 9 (idempotency and limits): not applicable. The reader is read-only and exposes no state-changing tool.
- Hard rule 10: unreachable or other `psycopg.Error` becomes the structured transient error `inventory_unavailable`, and M-p3 covers it. The blocking and hang case is M2. Absent tables are classed as a valid empty result (L1).
- Hard rule 11 (AI disclosure): not applicable.
- Child processes get a placeholder key only, and only the proxy holds the real key: not applicable to this change.
- The Makefile does not export the key: not applicable (Makefile unchanged).
- No key in CI secrets, and CI runs in replay: not applicable (workflows unchanged). The browser job's `DATABASE_URL` now activates the reader; see L1.
- The key file is never copied into a worktree: not applicable.
- Budget caps enforced and not raised: not applicable. No cap changed.
- Cassettes still covered by the secret/PAN scanner: not applicable. No cassette or scanner change.
- Exit 3 (live_holds.py:17-19, the runbook, PROGRESS.md): consistent everywhere. Only a real 0 opens a window, and exit 3 is now stricter. Remaining gap: the guard is not yet wired to the runner's database (open item N2).
- Import-closure split (test_18_step0_stock.py): the whole server is still checked for model code, and `northwind_backend.inventory.substitutes` alone is checked for psycopg plus the 13-module allowlist. No finding.

Unconfirmed: the claim in M2 that a connect can take 3 seconds once per resolved address of `localhost`.
