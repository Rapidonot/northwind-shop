# Row #18 step 0: security-reviewer, scoped re-review (verbatim)

Diff: `/tmp/claude-502/northwind-review-18-step0-rereview.diff` (base `bcc135b`, uncommitted, after the first
pass's fixes). The report follows unchanged except one phrase: under "Rule 4", the reviewer's "No `.env`
access" reads "No environment-file access" here, because the repository's hook refuses that file name in
a shell command. The triage of N1-N4 is at the end of
`docs/reviews/2026-11-26-18-step0-triage.md`.

---

## Scoped re-review: row #18 step 0, fixes to the first pass

**Inputs:** the diff `/tmp/claude-502/northwind-review-18-step0-rereview.diff` (all 2381 lines; base bcc135b, uncommitted working tree), the first pass, the triage and the failing-first log.

**Files read in full or in the relevant parts:**
- `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/server.py`
- `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/cart.py` (lines 160-559)
- `/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py` (`_sentence_start`, `_cut`, `_index`, `_view`, every use of `_BOUNDARY`)
- `cart_store.py` (the `_Open` docstring)
- CLAUDE.md "Hard rules" and decision 0001, both read again for this pass.

**Result:** both mediums are closed. No new critical, high or medium finding. Four new lows (N1-N4). Nothing in the diff, the logs or the review files tries to steer the review.

### First-pass findings

**M1: closed.**
- `cart.py:444` uses `with server.reading(region), server.carts.open(session, region) as cart:`. `reading()` calls `self.holds.live(region)` when the `_Reading` object is built (`server.py:158`), which is before `__enter__` and before the cart lock is taken. So a reader that fails raises before any change to the cart, and no ContextVar has been set at that point.
- Every stock read inside the block goes through the call's one reading, so no write path reads holds after the cart changes:
  - `view()` (`cart.py:197`)
  - `_add` and `_set_quantity` (`cart.py:365`, `393`)
  - `refusal` (`cart.py:315`)
  - `alternatives` (`cart.py:278`)
  - All of these reach it through `server.on_hand` → `server.held` → `_READING` (`server.py:143-152`).
- Grep confirms that nothing else in `src` calls `carts.open` for a write.
- **ContextVar reset:**
  - `_Reading.__exit__` always resets its token and returns None, so exceptions propagate (`server.py:116-118`).
  - With `with A, B`, if `carts.open(...).__enter__` raises, `A.__exit__` still runs.
  - The handler body has no `await` inside the block, so no other request can run in between on the event loop.
  - A ContextVar set inside a request task does not leak into the parent context or into other tasks.
  - The region check in `held()` (`server.py:150`) keeps one region's reading from being used for another.
  - I found no path where a stale reading leaks.
- The plain class follows the same pattern as `cart_store._Open`, for the same frozen-`ToolError` reason.
- **Read-only paths are unchanged from the first pass:**
  - `check_stock` (`tools.py:312`): one fresh read.
  - `eligible_products` (`tools.py:460`): one direct read, only with `in_stock`.
  - `get_cart` (`cart.py:347-348`): one read per line, as before. See N3.
- **Tests:**
  - `test_a_reader_that_fails_leaves_the_cart_as_it_was` covers all four write shapes, including the decrease and remove paths that never check stock.
  - `test_a_write_reads_holds_once_so_a_later_failure_cannot_follow_it` asserts `reads == 1`.
  - Mutant M-m1 fails 4.
- **Consequence to know about (not a finding):** `remove_from_cart` and quantity decreases now fail with `inventory_unavailable` while the holds reader is down, although they need no stock. This fails safe: the error is truthful, transient and retryable, and the cart is unchanged. Hard rule 10 is met.

**M2: closed for the path I named.**
- `test_output_check.py` (new lines 56-65) wraps the module's `_BOUNDARY`. Methods look up `_BOUNDARY` as a module global when they run, so the patch takes effect.
- The count is the full span each `finditer` call is given, which is an upper bound on what it scans. The test also counts the `_view` reads.
- The limits are 4x input ≤ 5x work and ≤ 4L per character. L = 64 (`output_check.py:35`), so the large input is capped at about 8.06M characters.
- Mutant M-a2 (scan cache off) now fails at 126.3M against 41.6M. M-a1 still fails both tests.
- **Weaker than bcc135b?** The inputs are identical at k=4: 31,500 and 30,006 characters. The bcc135b test had a 5 s wall-clock limit, which the 3 s-per-call slow-clock run in the log shows was not a reliable bound. The new count bounds the `_view` and `finditer` work absolutely. One thing is weaker: work outside the counted paths no longer has an absolute limit, only the 8x CPU ratio. See N4.

**L1: closed.**
- The cart module docstring now states the gap: region total against BE-04's single-node maximum, owner row #21.
- `PROGRESS.md` Open items has "From #18 step 0, L1", owner row #21 (CHK-05), with a concrete fix.
- `substitutes.find`'s own contract still says "single node". That is correct: it is the contract the cart does not yet meet.

**L2: closed.**
- `holds.py:29-37` defines `unavailable()`, which builds a new error for each raise.
- Nothing in mcp-catalog refers to a module-level `UNAVAILABLE` any more. Backend's `inventory/model.py:69` `UNAVAILABLE` is a value that is returned, never raised, so this finding does not apply to it.

**L3: closed.**
- `test_rag07_tw.py:185-198` routes 50,000 and then 200,000 characters and requires at most 6x the CPU time (floor 0.02 s). The 30 s CPU backstop is kept.
- This follows my suggested fix. A C-speed quadratic part smaller than about a fifth of the linear cost at 50K still passes, as any ratio test allows.

**L4: closed.**
- `FORBIDDEN` adds `claude_agent_sdk` and `psycopg`, matched exactly or by prefix.
- `BACKEND_ALLOWED` is an allow-list of 13 modules, and `backend <= BACKEND_ALLOWED` is asserted (`test_18_step0_stock.py`, new lines 1698-1743).

**L5: partly closed. See N1 and N2.**
- Exit codes are 0, 1, 2 and 3 (`live_holds.py:951-966`).
- `psycopg.Error` returns 2 and prints only the error class name, never its message.
- Exit 3 names the database.
- The tests cover 3 with the database named, 2 on `InsufficientPrivilege`, 2 when the database cannot be reached, and the end-to-end sequence 3, 0, 1, 0.

**L6: closed.**
- `tests/reports/test_hedge_report.py:353` runs `[sys.executable, "-P", str(REPORT), ...]` with `cwd=REPO`. A script run is never given the working directory on `sys.path` anyway, so `-P` removes the only implicit entry, the script's own folder.

### New findings

**N1. low. `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/inventory/live_holds.py:53` (diff line 953). L5 bullet 2 / hard rule 4 (the message part is unconfirmed).**
- **What is wrong:** `database = psycopg.conninfo.conninfo_to_dict(url).get("dbname", "?")` runs before the `try`. A malformed `DATABASE_URL` raises `psycopg.ProgrammingError`, a `psycopg.Error`, outside the handler. The guard then dies with a traceback and exit 1, which means "some hold is live", instead of 2, "not checked". L5 asked for exactly that to be 2.
- **How it fails:** a typo in `DATABASE_URL`, for example a bad percent-escape in the password, gives exit 1 and a traceback. That fails closed, because the window does not open, but the window record reads as live holds. Unconfirmed: libpq's parse errors (`missing "=" after "…"`, `invalid percent-encoded token: "…"`) echo a fragment of the connection string, and that fragment can include the password. It would go to stderr.
- **Fix:** move the `conninfo_to_dict` call inside the `try`, or into its own `except psycopg.Error` that returns 2 with the class name only. Add a test with a malformed URL that asserts exit 2, and that asserts stderr does not contain the password.

**N2. low. `/Users/rapido/Projects/northwind-shop/PROGRESS.md` Open items (diff lines 10-12), against the triage's "wiring logged" for L5. CLAUDE.md "Running a row", triage of lows logged elsewhere.**
- **What is wrong:** grep finds no `live_holds`, hold-guard or exit-3 entry in `PROGRESS.md`. Two things are recorded only in the triage and in the module docstring:
  - the runner wiring;
  - the rule that exit 3 has to count as "not checked" once a Postgres `Holds` reader is wired.
- **How it fails:** item (1) says the Postgres reader is waiting on the user's choice. Suppose a later row wires that reader and `DATABASE_URL` points at a fresh database or at `northwind_enriched`. The guard prints exit 3, "none possible", and nothing on the open-items list says that is now a stop. The window opens while the server reads real holds from another database. This is the fail-open path L5 described, now one wiring step away.
- **Fix:** add an Open item with a named owner (the row that wires the Postgres reader, or the runner/control-surface row). It should cover wiring the guard into the window, and the rule that exit 3 blocks a window once a reader is configured.

**N3. low. `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/cart.py:347-348` (with `cart.py:197` and `server.py:143-152`). Hard rule 2 (consistency of one live answer), robustness.**
- **What is wrong:** `get_cart` does not take a reading. `view()` reads holds once per line through `server.held` (up to `MAX_LINES` reads), so the lines of one cart view can come from different readings. Once a Postgres reader is wired, each `get_cart` makes one query per line, and that is synchronous work on the event loop. The behaviour is the same as at the first pass, so this is not a regression from the fix.
- **How it fails:** a hold settles between line 1's read and line 2's. One response then shows stock from two different moments. The figures are still live, so no hard rule is broken.
- **Fix:** wrap `get_cart`'s block in `server.reading(region)`, the same way as the writes. Add a reads-count test like `test_one_holds_read_per_refusal`.

**N4. low. `/Users/rapido/Projects/northwind-shop/services/assistant/tests/test_output_check.py` new lines 37-42 and 80-88 (the `_viewed` and `_assert_linear` docstrings). Security review M4 (hard rules 2 and 5): how far the guard reaches.**
- **What is wrong:** the docstring and the triage say the CPU ratio bounds work such as `_cut` and `_index`. That is overstated in three ways:
  - `_cut` runs only from `reset` (`output_check.py:1038`) and from marker repair (`:1467`, `:1472`). Neither input contains a marker or a reset, so `_cut` and its `_reindex` never run in these tests.
  - `_BOUNDARY.search` (`:1377` in `_policy_context`, `:1694` in the dropping state) goes through `Scanned.__getattr__` and is not counted. Neither path runs on these inputs either.
  - The 8x ratio only catches a quadratic part that is at least about a third of the k=1 total. The old absolute limit is gone, so a slowdown that is linear but much slower per character in an uncounted path now passes.
- None of this makes the tests weaker than bcc135b's on the paths those tests ran, apart from the dropped absolute limit.
- **Fix:** make the docstrings say only what the tests prove. Then, without loosening anything:
  - add a generous absolute `process_time()` limit on the k=4 input;
  - count `search` the same way as `finditer`;
  - add an input with markers and one sentence that gets dropped, so `_cut`, `_reindex` and the dropping-state `search` come under the counts.

### Checked, no findings
- Rule 1 (card data): not applicable to this change. No card paths, and no card data in tests or fixtures.
- Rule 2 (live price and stock): every catalog stock read subtracts holds through `server.on_hand`, or through `eligible_products` with `held`. Prices come from the live store through `price=`. Alternatives carry no price. Caveats: L1 (logged) and N3.
- Rule 3 (synthetic data): only fixture IDs, O900xxx orders, bare postal codes and a local docker-compose URL.
- Rule 4 (secrets): no secret is printed or logged. The guard prints the database name and the error class only. Caveat: N1 (unconfirmed). No environment-file access.
- Rule 5 (no invented attributes): not applicable to this change. Alternatives come from catalog rows, chosen by deterministic code.
- Rule 6 (retrieved text is data): the refusal block's `<catalog_data>` label is unchanged, the tool-list sha256 is pinned, and no tool access depends on content.
- Rule 7 (internal documents and region): `from_rows` keeps public rows only. Each alternative is checked again against the caller. Readings are keyed by region (`server.py:150`).
- Rule 8 (least privilege): no tools or scopes changed. The import closure is now an allow-list (L4).
- Rule 9 (idempotency): keys are unchanged. A reader that fails cannot follow a write any more (M1 closed).
- Rule 10 (structured errors): `unavailable()` gives a transient, retryable envelope, never a quiet zero. The guard's exit codes separate "not checked" from "none". Caveat: N1.
- Rule 11 (AI disclosure): not applicable to this change.
- Child processes get a placeholder key only: not applicable. No LLM client, factory or proxy code changed.
- Makefile does not export the key: not applicable. The Makefile is not in the diff.
- No key in CI secrets, CI in replay: not applicable. `.github/workflows` is not in the diff.
- Key file never copied into a worktree: not applicable. No setup or worktree code changed.
- Budget caps enforced and not raised: not applicable. No proxy or ledger code changed.
- Cassettes still covered by the secret/PAN scanner: not applicable. No cassette or scanner changed. The hedge test only reads cassettes.
