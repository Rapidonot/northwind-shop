# Row #18 step 0: security-reviewer, first pass (verbatim)

Diff: `/tmp/claude-502/northwind-review-18-step0-security.diff`, base `bcc135b`, the uncommitted working tree
of `req/18-record-all` (18 files). The reviewer's report follows unchanged; the triage is in
`docs/reviews/2026-11-26-18-step0-triage.md`.

---

## Security review: row #18 step 0, diff at /tmp/claude-502/northwind-review-18-step0-security.diff (base bcc135b, uncommitted working tree)

I read all 1998 lines of the diff, CLAUDE.md "Hard rules" and decision 0001. For context I also read the full `substitutes.py`, `cart.py` (views, alternatives, `_idempotent`), `server.py` dispatch, `cart_store.py`, `output_check.py` (`_view`, `_sentence_start`, `_cut`), the hedge-report script, and the `northwind_backend` imports that `cart.py` pulls in.

There are no critical and no high findings. There are 2 medium and 6 low findings. Nothing in the diff, the fixtures or the logs tries to steer the review.

### Findings

**M1. medium. `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/cart.py:454-461` (with `server.py:115-120` and `holds.py:29`). Hard rules 9 and 10. Not live yet: `NoHolds` cannot fail.**
- **What is wrong:** `view()` now reads holds (`cart.py:195` → `server.on_hand` → `holds.live`). But `_idempotent` calls `view()` after the write has already changed `cart.lines[...]` and stored `cart.keys[remembered]`. If the holds reader fails at that point, the shopper gets `UNAVAILABLE` ("Stock can't be checked right now. Try again shortly.", transient, retryable), yet the cart has changed. The decrease path of `set_cart_quantity` and `remove_from_cart` never read stock, but their `view` does, so the same thing happens there. Before this diff, `view` could not fail.
- **How it fails:** once a Postgres `Holds` reader exists and the database blips between the write and the view:
  1. `add_to_cart(qty 2, key K)` is applied and the caller is told it failed and can be retried.
  2. Keys are remembered per `(claims.turn, key)`. A retry in the next turn, or with a new key, is a new pair and adds 2 more. The line ends up at 4.
  3. The tool said "access failed" when the write had succeeded.
- **Fix:** read `server.holds.live(region)` once at the top of the write, before any change to the cart. Pass that one reading to the stock check and to `view(...)`, so a reader failure always happens before the cart changes. Alternatively, build the result view before committing the change. Add a test where `Broken` fails only on the second read after a successful add, and assert that the cart is unchanged.

**M2. medium. `/Users/rapido/Projects/northwind-shop/services/assistant/tests/test_output_check.py:531-593`. Security review M4 (it protects the output check behind hard rules 2 and 5). The tests now protect less.**
- **What is wrong:** both M4 tests now count only the characters read through `OutputCheck._view`. They no longer bound any work done outside `_view`:
  - `_sentence_start` runs `_BOUNDARY.finditer` directly over `self._raw`. Its docstring cites M4 as the reason for its scan cache.
  - `_cut` rebuilds `self._raw` and `self._deltas` on every cut.
  - `_index` and `_reindex`.
- **How it fails:** a regression that drops `_sentence_start`'s `_scan` cache, so it rescans from `_plain_since` on every delta, is quadratic on the 31,500-character no-sentence-end input. It does not touch `_view`, so both new tests still pass. The old wall-clock bound covered every path. The failing-first log only shows the `_back` mutant (M-a1), which acts through `_view`.
- **Fix:** keep the counted-reads assertion and add to it, without loosening anything:
  - add a count of characters scanned by `_sentence_start` (patch `_BOUNDARY` or wrap the method) with the same 4x-input ≤ 5x-work ratio;
  - or also keep a `time.process_time()` ratio check between the k=1 and k=4 inputs.
  - Add a mutant that disables the `_scan` cache to the failing-first log.

**L1. low. `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/cart.py:288` against `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/inventory/substitutes.py:9,129`. Hard rule 2 (the meaning of live stock). No rule is broken: the stock is still live.**
- **What is wrong:** `find`'s contract says `reservable(variant)` is "the most any single node of `region` can give now". The cart passes `_on_hand(...)` instead, which is the region total less region holds (`live.py:181` sums the nodes).
- **How it fails:** take US with DC-EAST at 1 free and DC-WEST at 1 free, and a request for 2. The chat offers that variant as an alternative. BE-04's single-node reservation at checkout would refuse it. This contradicts the new module docstring's "so the chat and checkout offer the same ones".
- **Fix:** give `Holds` (or the catalog store) a per-node view and pass the single-node maximum. Otherwise, correct the docstring and record the divergence as an open item with an owner.

**L2. low. `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/src/northwind_mcp_catalog/holds.py:29`. Hard rule 10 (robustness).**
- **What is wrong:** `UNAVAILABLE` is one module-level `ToolError` instance, and the port tells readers to raise it. Raising the same instance again and again grows its `__traceback__` chain and keeps the last request's frames alive (locals such as `claims` and `cart`), as well as `__context__`. That is shared mutable state across requests.
- **How it fails:** while the database is down, every stock call adds frames to the singleton. Memory grows without bound and per-request objects are kept. Nothing reaches the caller, because the envelope has fixed fields.
- **Fix:** turn it into a factory `def unavailable() -> ToolError`, which is the style `server.py` already uses (`not_allowed()`, `tool_failed()`), and update the docstring and the tests.

**L3. low. `/Users/rapido/Projects/northwind-shop/services/rag/tests/test_rag07_tw.py:185-193`. RAG-07 router DoS bound.**
- **What is wrong:** the limit went from 3 s wall time to 30 s CPU time. The docstring says 30 s is "far below what a quadratic pass over 200,000 characters costs". That holds for Python-level loops, not for quadratic work at C speed (repeated `str.find`, slicing or `re` scans over the remaining text). Roughly 2×10^10 memchr-speed steps can finish in well under 30 s of CPU.
- **Fix:** keep `process_time()`, but assert scaling instead of an absolute limit: route n and 4n characters and require at most about 6x the CPU time, like (a). Alternatively, use a CPU limit near the measured 0.84 s with margin (for example 5 s).

**L4. low. `/Users/rapido/Projects/northwind-shop/services/mcp-catalog/tests/test_18_step0_stock.py:278-297`. The user's dependency condition.**
- **What is wrong:** the closure test is a deny-list of four names. The import actually loads more than the substitutes module: `northwind_backend.inventory` runs `__init__`, which loads `inventory.memory` (`InventoryFake`), `inventory.model`, `nodes`, `fakes.catalog`, `fakes._base`, `idempotency`, `recovery` and `errors`. All of these are pure today. But the condition was "only for the pure substitutes module".
- **How it fails:** a later import of, for example, `psycopg` or `claude_agent_sdk` added anywhere in that chain would pass the test. `psycopg` is checked only through `inventory.postgres`, not the package itself.
- **Fix:** add `psycopg` and `claude_agent_sdk` to `FORBIDDEN`. Also assert an allow-list: the loaded `northwind_backend.*` modules must be a subset of the set imported today.

**L5. low. `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/inventory/live_holds.py:44-45,53-56`. Strength of the recording guard.**
- **What is wrong:**
  - `UndefinedTable` returns exit 0. If `DATABASE_URL` points at the wrong database (for example `northwind_enriched`, or a fresh database), the guard reports "0" and lets the window open: it fails open.
  - Errors from psycopg other than `OperationalError` (for example `InsufficientPrivilege`) escape as a traceback with exit 1, which reads as "some live" instead of "not checked".
  - Nothing in this diff wires the guard into the runner or `make record`; only the docstring asserts it. I could not confirm where it is called.
- **Fix:**
  - Make "tables absent" a distinct exit code that the window treats as not checked once a holds reader is configured.
  - Catch `psycopg.Error` and return 2.
  - Wire the guard into the runner under the control-surface process.
- The `DEFAULT_DATABASE_URL` password is the local docker-compose default. It is already used in `infra/docker-compose.yml`, `env.example` and `gateway/settings.py`, and it is never printed. So it is not a hard rule 4 finding.

**L6. low. `/Users/rapido/Projects/northwind-shop/tests/reports/test_hedge_report.py:28-36`. Hygiene.**
- **What is wrong:** `python <docs/reviews/…py.txt>` puts `docs/reviews/` first on `sys.path`. The script imports `glob`, `json`, `re` and `sys`, so a `json.py` or `re.py` in `docs/reviews/` (not a control-surface path) would run under `make test`/`make ci`. This adds an execution path from a directory that sessions write to often.
- **Fix:** run it with `[sys.executable, "-P", str(REPORT), ...]` (Python 3.11 and later). The script itself reads `cassettes/` only, has no network, ledger or key access, and prints synthetic cassette text.

### Checked, no findings
- **Rule 1 (card data):** no PAN or CVV paths touched. `fakes._base` imports `northwind_common.cards` checks only. No card data in the tests.
- **Rule 2 (live price and stock):** prices for the band come from `server.catalog.price` through `price=`. When `price=` is given, the fixture `default_prices()` table is not used (`substitutes.py:136-140`). Every catalog stock read goes through `server.on_hand`, or through `eligible_products` with `held` (grep confirms there are no other `catalog.stock(` reads). The alternatives payload is `_item` plus `on_hand` and `in_stock` only, with no price (`cart.py:172-180, 219-227`). The model does not choose: `find` is deterministic code. Caveat: L1.
- **Rule 3 (synthetic data):** only fixture IDs, order IDs O900xxx and sandbox data. The postal codes "10001" and "M5V 2T6" are bare codes with no person attached.
- **Rule 4 (secrets):** no secret is printed, logged or committed. The local DB default matches the existing pattern. No `.env` access.
- **Rule 5 (no invented attributes):** alternatives come from catalog rows. No model wording.
- **Rule 6 (retrieved text is data):** the refusal block still carries one `<catalog_data>` label. The tool list sha256 is pinned (`test_the_tool_list_is_unchanged`). No tool access depends on content.
- **Rule 7 (internal documents and region):** `from_rows` keeps only public products and variants. The visibility of every result is checked again against the caller (`cart.py:293-297`). Staff callers now get public-only alternatives, which is stricter. The per-server cache (`cart.py:230-266`) can go stale, but the re-check keeps shoppers safe.
- **Rule 8 (least privilege):** no tools or scopes were added. The tool list hash is unchanged.
- **Rule 9 (idempotency):** cart writes still require the key, and `live_holds` is read-only. Caveat: M1.
- **Rule 10 (structured errors):** a failing reader raises a transient, retryable envelope, never a quiet zero. Any non-`ToolError` exception becomes `tool_failed` (transient, retryable) at `server.py:143-145`, so a future reader that raises a psycopg error is also covered. Caveats: M1 and L2.
- **Rule 11 (AI disclosure):** not applicable to this change.
- **Child processes get a placeholder key only:** not applicable. No LLM client, factory or proxy code changed.
- **Makefile does not export the key:** not applicable. The Makefile is not in the diff.
- **No key in CI secrets, CI in replay:** not applicable. `.github/workflows` is not in the diff.
- **Key file never copied into a worktree:** not applicable. No setup or worktree code changed.
- **Budget caps enforced and not raised:** not applicable. No proxy or ledger code changed.
- **Cassettes still covered by the secret/PAN scanner:** not applicable. No cassettes or scanner changed. The hedge test only reads cassettes.
- **uv.lock:** only mcp-catalog's `northwind-backend` workspace entry is added. No new third-party package.
