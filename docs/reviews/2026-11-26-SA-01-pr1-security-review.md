# SA-01 PR 1: security-reviewer's report, verbatim (2026-11-26)

Diff reviewed: `/tmp/claude-502/northwind-review-SA-01-security.diff` (base 361d396; head was the uncommitted working
tree). Triage and fixes: see the end of this file.

---

## Security review: SA-01 PR 1 (portal shop pages), read-only

Diff: /tmp/claude-502/northwind-review-SA-01-security.diff (base 361d396, head is the uncommitted working tree). Before reviewing I re-read CLAUDE.md "Hard rules" and docs/decisions/0001-llm-first-order-and-record-replay.md. Nothing in the diff, the fixtures or the plan tried to steer the review.

There is one high finding. It breaks hard rule 1, so it is unsafe and is stop 10 until it is fixed. The other findings are low.

### Findings

**F1. High, unsafe (hard rule 1 / NFR-04: no PAN in logs). Card data typed into search reaches the gateway's access log in full, even though the request is refused.**
- **Where:**
  - `apps/portal/src/App.tsx:615` (diff line 615) puts the search box text into the hash.
  - `apps/portal/src/shopApi.ts` `fetchProducts` (diff 1624-1629) sends it as `GET /api/products?q=…`. `fetchProduct` and `#/d/<name>` → `?department=` do the same.
  - `services/gateway/src/northwind_gateway/shop.py` `no_card_data` (diff 2653-2657) and its module docstring (diff 2601-2602) say "before any lookup or log".
- **What is wrong:** the refusal runs inside the app, and uvicorn's access logger runs outside it. `make dev` (`Makefile:114`) and Playwright's webServer (`apps/portal/playwright.config.ts:106`) both start uvicorn with its default access log on. Nothing in `services/` turns off or filters `uvicorn.access`; I grepped for `uvicorn.access`, `log_config` and `access_log` and found nothing. uvicorn logs the full path with its query string (`.venv/.../uvicorn/protocols/http/httptools_impl.py:482-486`, `get_path_with_query_string`).
- **How it fails:** a shopper pastes `4242 4242 4242 4242` into the search box. The gateway returns the fixed card refusal, but stderr already holds `"GET /api/products?q=4242+4242+4242+4242 HTTP/1.1" 422`. A crafted link such as `#/d/4242424242424242` does the same through `?department=`.
- **Why it is new:** until now free text reached the gateway only in a POST body (`/api/chat`), which the access log does not record. The tests use TestClient, so they never exercise the access log and cannot catch this.
- **Two related points:**
  - `no_card_data` scans query values only, not parameter names. `?4242424242424242=x` passes the check and is logged the same way.
  - Unconfirmed: when the gateway is down, Vite's dev proxy logs the URL it failed to proxy, query included. I did not check Vite's exact format.
- **Fix direction (none of these weakens a check):**
  - refuse card data in the portal before navigating or fetching;
  - and/or carry search text in a POST body rather than the URL;
  - and/or run the gateway with an access-log filter that drops or redacts query strings;
  - add a test that runs a real uvicorn and asserts that no access-log line holds a Luhn-valid number.

**F2. Low, not unsafe (hard rule 10 / defence in depth). The gateway does not check that product and variant IDs from mcp-catalog are well formed before passing them to the browser.**
- **Where:** `shop.py` `_tile` (diff 2788-2798) checks only `isinstance(..., str)` for `product_id`, and `_product_view` (diff 2901-2903) does the same for `variant_id`. `ProductTile.tsx` (diff 722, 730) and `shopApi.ts` `tileImage`/`heroImage` (diff 1659-1660) put `product_id` into an `<img src>` and an `href` without encoding.
- **How it fails:** only if mcp-catalog returns a bad ID, for example `../../api/cart?x=`. The `<img>` would then make a same-origin GET to another path. The `href` always starts with `#`, so it cannot become a `javascript:` URL, and React escapes attributes, so this is not XSS.
- **Fix:** in the gateway, treat an ID that fails `PRODUCT_ID.fullmatch` as `Unreadable`, and add a matching pattern check for variant IDs.

**F3. Low, not unsafe. The first page load opens up to three sessions at once.**
- **Where:** `session.py:50-51` adds the three shop paths to `SESSION_PATHS`. `App.tsx` (diff 631-634) fetches `/api/departments` and `/api/products` while `ChatPanel` fetches `/api/cart`, all before the browser has a cookie.
- **How it fails:** each request gets a new session and its own `Set-Cookie`, and the browser keeps whichever arrives last. The first cart read can therefore belong to a session that is then dropped. No data leaks, because every new session is empty and unsigned-in, but the session bound to the chat and the cart is effectively picked at random on first load.
- **Fix direction:** fetch one session path first, before the others. A design question for PR 2, where the cart writes depend on a stable session.

**F4. Low, unconfirmed (resource handling).** `ShopReader.call` (diff 2732-2738) builds an `httpx2.AsyncClient` and never closes it. This copies the existing `CartReader` (`services/assistant/src/northwind_assistant/catalog_client.py:72-78`). I did not confirm whether `streamable_http_client` closes a client passed in to it. If it does not, every shop request leaks a connection pool. A product page makes two reads, so it leaks two.

**F5. Low, not unsafe (hard rule 7 hardening).** No photo exists today for P0009, the one internal product (I checked the stat list and `data/fixtures/products.jsonl:9`). But `apps/portal/public/products/**` is served statically by URL, whatever the product's visibility. If a P0009 photo were committed later, anyone could fetch it at `/products/800/P0009.webp`, even though no API ever sets `photo: true` for it. Fix: a test that no committed photo is named after a non-public product ID.

### Checked, no findings
- **Hard rule 1:** apart from F1, `no_card_data` runs as a router dependency before `shopper_caller` and `shop_reader`, and covers every repeat of every value. mcp-catalog `_checked` refuses card data before any lookup. The checkout placeholder has no card field, and the e2e test asserts there are 0 card textboxes.
- **Hard rule 2:** prices come only from `get_prices` and `browse_products`, which call `server.catalog.price(...)` on every request, keep only the region's currency and skip non-public variants. The gateway validates the money shape (`_money`). `formatMoney` only formats the server's string, and the browser never computes a price or a subtotal (the bag subtotal is the server's). Fixture descriptions and attributes hold no price or stock text (grepped).
- **Hard rule 3:** synthetic data only.
  - The fonts are OFL, each with its licence file.
  - C2PA is present (`P0001.webp`).
  - I searched two photos explicitly (`800/P0001.webp`, `1600/P0089.webp`) for an email pattern, `/Users/` and personal names, and found none. I did not search all 200 explicitly, so the rest are spot-checked, not confirmed.
- **Hard rule 4:** the key comes from the environment on each request through `signing_key()`, `ShopReader.key` is `repr=False`, and no key appears in logs or responses.
- **Hard rule 5:** not applicable. No model writes product claims on these pages, and all facts come from the catalog record.
- **Hard rule 6:** catalog strings are rendered only as React text and attributes. The portal source has no `dangerouslySetInnerHTML`, `innerHTML` or `eval`. Router parsing is strict (`PRODUCT_ID` regex, `decodeURIComponent` in a try block, extra segments are `not-found`). Catalog content grants no tool access.
- **Hard rule 7:**
  - `_sees` lets only `public` through. `browse_products` never lists P0009, and `get_prices`/`get_product` give the same `not_found` for an internal ID as for an unknown one.
  - The region comes only from `shopper_caller` (the signed `nw_session` and `nw_shopper` cookies, with the demo-signin flag) or `DEV_SHOPPER_REGION`. No query value or header can set it, and the gateway refuses a tool result whose region differs from the caller's.
  - The token's audience is always `"shopper"`.
  - The exact-path `SESSION_PATHS` change is limited to these three read routes.
- **Hard rule 8 / NFR-01:**
  - `shopper-portal`'s catalog row is exactly `{get_cart, get_product, browse_products, get_prices}`, with no cart write.
  - The two portal tools are in no other agent's row, and the server refuses them to other agents (`call_tool` re-checks `_offered`).
  - The digest and allowed-tools pins for the assistant are in place.
  - The widened pins in `tests/mcp/test_contract.py` and `test_chk01_criteria.py` are still exact sets (`==`). `SERVER_ONLY_TOOLS` is unchanged, and the new `PORTAL_ONLY_TOOLS` check is `holders == {owner}` plus an exact row equality. They are no weaker in any other way.
- **Hard rule 9:** not applicable. No state-changing tool or route is added, and both new tools are annotated read-only.
- **Hard rule 10:** the envelopes have fixed text (503 `transient`, 404 `not_found`, 422 validation, and the existing card refusal). Unavailable (503) is distinct from an empty list (`products: []`). Logs record `type(error).__name__` only. Every response is `Cache-Control: no-store`.
- **Hard rule 11:** the banner "Demo store. AI assistant. …" is on every page, and every e2e journey ends on the disclosure. `ChatPanel`'s `onCart` change is a pass-through callback with stable dependencies, and `Chat.tsx`/`chatStream.ts` are unchanged.
- **ShopReader token:** agent `shopper-portal`, audience `shopper`, `server=CATALOG_SERVER`, session optional (anonymous browsing works and the cart tools still refuse without one), `trust_env=False`.
- **Photo listing:** `photo_ids` accepts only regular files whose names fully match `P\d{4}\.webp` (ASCII), and a missing directory gives an empty set, never an error. It only marks tiles the tool already returned, so it cannot add a product.
- **Decision 0001, child processes get a placeholder key only:** not applicable to this change (no LLM client or `llm.py` change).
- **Decision 0001, the Makefile does not export the key:** not applicable; `Makefile` is unchanged.
- **Decision 0001, no key in CI secrets, CI runs in replay:** not applicable; `.github/workflows/**` is unchanged.
- **Decision 0001, key file never copied into a worktree:** not applicable; no key-file handling is touched.
- **Decision 0001, budget caps enforced and not raised:** not applicable; no proxy or budget change.
- **Decision 0001, cassettes covered by the secret/PAN scanner:** not applicable; no cassette or scanner change. The assistant's tool list is pinned unchanged, so no cassette goes stale.

---

# Scoped re-review, verbatim (2026-11-26)

Diff reviewed: `/tmp/claude-502/northwind-review-SA-01-security-fixes.diff` (the files touched by the F1–F5 fixes, each against 361d396).

## Scoped re-review: SA-01 PR 1, fixes for F1 to F5 (read-only)

**Verdict:** F1 is closed for every path the portal can produce, and F2 to F5 are closed. Nothing is weakened. The fix code has two new low findings, neither unsafe in the current flows. No high or critical is open, so stop 10 does not apply.

**Scope:** I reviewed `/tmp/claude-502/northwind-review-SA-01-security-fixes.diff` (base 361d396, head is the uncommitted working tree). Before reviewing I re-read the "Hard rules" in `CLAUDE.md` and `docs/decisions/0001-llm-first-order-and-record-replay.md`, plus the original report `docs/reviews/2026-11-26-SA-01-pr1-security-review.md`. Nothing in the diff or the tests tried to steer the review.

### Is F1 closed?

Yes, for the gateway's HTTP access log and the gateway's own logs. What I checked:

- **Every HTTP protocol writes to the filtered logger.** httptools (the one `auto` picks, since httptools is installed), h11, zttp and zttp_h2 all log the request line through `uvicorn.access`, with the path as a positional `str` argument (`httptools_impl.py:482-486`, `h11_impl.py:479-481`, `zttp_impl.py:464-468`, `zttp_h2_impl.py:479-481`). `DropQueryStrings` rewrites exactly that argument.
- **The filter is attached under `make dev` and Playwright.**
  - Both commands (`Makefile:114` with `--reload`, `playwright.config.ts:106` without) build `Config`, whose `__init__` calls `configure_logging()` (`uvicorn/config.py:303`) before the app is imported. With `--reload`, the child calls `configure_logging()` (`_subprocess.py:76`) before `target()`, which imports the app.
  - So `install()` at import runs after `dictConfig`, and the lifespan `install()` runs later still.
  - Even in the reverse order (which the test uses: it imports the app first), the filter survives. Python 3.12's `common_logger_config` removes handlers but never filters (`logging/config.py:906-922`), and uvicorn's config uses `disable_existing_loggers: False`.
  - Logger filters run in `Logger.handle` before propagation, so any parent or root handler also gets the rewritten record.
- **The gateway's own logs are clean.** Every `log.*` call in `services/gateway/src/northwind_gateway/` writes fixed text or `type(error).__name__`. None reads `request.url` or the query.
- **uvicorn's HTTP error lines are clean.** "Invalid HTTP request received.", "Exception in ASGI application" and "Exceeded concurrency limit." carry no request target. A traceback has no locals.
- **`make dev`'s other servers** (mcp-rag, mcp-catalog) receive only POSTed MCP calls from the gateway or assistant, no shopper URL. Card text in `q` is refused by `no_card_data` before it can become `browse_products`' `text` argument.

### Findings

**N1. Low, not unsafe in the current flows (hard rule 1 / NFR-04, defence in depth). WebSocket handshake lines go to `uvicorn.error` with the full query string, and the filter does not cover that logger.**
- **Where:**
  - `services/gateway/src/northwind_gateway/access_log.py:41-44`: `install()` attaches only to `uvicorn.access`.
  - `.venv/.../uvicorn/protocols/websockets/websockets_sansio_impl.py:464-468`: `self.logger` is `uvicorn.error` (line 70), and it logs `'%s - "WebSocket %s" 403'` with `get_path_with_query_string(self.scope)`. Lines 440-444 and 479-483 do the same.
  - `websockets` is installed, so `ws="auto"` picks this implementation (`websockets/auto.py:18-21`).
  - `session.py:153` passes non-HTTP scopes through, and Starlette closes an unmatched WebSocket scope, which produces the 403 line.
- **How it fails:** `websocat 'ws://127.0.0.1:8000/x?q=4242424242424242'`, or `new WebSocket(...)` from any page, puts `"WebSocket /x?q=4242424242424242" 403` on the gateway's stderr at INFO, the default level.
- **Why it is low:**
  - The portal opens no WebSocket to the gateway.
  - Vite's proxy is `{"/api": "http://localhost:8000"}` with no `ws: true` (`apps/portal/vite.config.ts:9`).
  - Only a client that already holds the number can do this, so no shopper's card data takes this path today.
- **Confirmation:** I confirmed this by reading the code, not by running it.
- **Fix direction:** install the same query-stripping on `uvicorn.error` for the `"WebSocket %s"` records (or for any `str` argument containing `?`). Extend `test_sa01_access_log.py` with a WebSocket connect carrying `?q=<PAN>` against the real uvicorn.
- **Related point, informational:** the filter strips only the query, so a number in a path segment (`GET /api/4242424242424242` → 404) is still logged. No portal flow puts shopper text into a gateway path: product IDs go in `?id=`, and photos are served by Vite after the `P\d{4}` check. Like N1, only a client that already holds the number can reach it.

**N2. Low, not unsafe (hard rule 1, defence in depth). The portal guard is narrower than the server's detector, although its comment says it is "deliberately broader".**
- **Where:**
  - `apps/portal/src/cardGuard.ts:6`: `/\d(?:[\s.+_-]*\d){11,}/` accepts only whitespace, `.`, `+`, `_` and `-` as separators.
  - `services/common/src/northwind_common/cards.py:26`: `_DIGIT_GROUP = \d(?:[\W_]?\d)*` accepts any single non-word character, so `/`, `,`, `:`, `%`, `*` and `#` count too.
- **How it fails:**
  - A shopper types `4242/4242/4242/4242` (or `4242,4242,4242,4242`) into search.
  - `mightBeCardData` is false in `SearchBox`, in `parseHash` (the decoded hash) and in `getJson` (the raw and the decoded URL). So the hash goes into browser history and `GET /api/products?q=4242%2F4242%2F4242%2F4242` leaves the browser through Vite's proxy.
  - The gateway refuses it (422), and N1 aside, the access log strips the query. So no log the gateway controls receives it, and F1 still holds.
  - But the shopper sees no `CARD_REFUSED` message, and if the gateway is down, Vite's proxy-error line may log the percent-encoded URL. That Vite line is still unconfirmed, as in the original review: Vite's sources were not readable from here.
- **Fix:** make the portal pattern at least as broad as `_DIGIT_GROUP`, for example a separator class of `[\W_]`. Correct the comment, and add `/`, `,` and `:` cases to `PANS` in `shopSecurity.test.tsx`. This only makes the check stricter.

### F2 to F5

- **F2: closed.** `shop.py` `_tile` checks `PRODUCT_ID.fullmatch` and `_product_view` checks `VARIANT_ID.fullmatch`, both before use. `VARIANT_ID` is byte-identical to `services/mcp-catalog/src/northwind_mcp_catalog/tools.py:44`. A failure raises `Unreadable`, which becomes the fixed 503. The forged-reader tests cover `../`, `javascript:`, an extra digit and an injected `?`.
- **F3: closed.** `App.tsx` renders `<Page>` and fetches departments only once `cart.kind !== "loading"`. A failed cart read becomes `unavailable` (`cartApi.ts:39-43`), so the page cannot hang on an error. The test asserts `/api/cart` is the first session-path request.
- **F4: closed.** `async with (httpx2.AsyncClient(...) as http, Client(...) as client)` closes the MCP client first, then the HTTP client. The same pre-existing leak in `CartReader` (`services/assistant/src/northwind_assistant/catalog_client.py`) is outside this row's files. Logging it in PROGRESS "Open items" with an owning row is the right triage.
- **F5: closed.** `test_sa01_photos.py` requires every file in both sizes to be WebP-magic, `.webp` and `visibility == "public"` in `products.jsonl`, the two sizes to match, and `photo_ids()` to equal the 800 set.

### Is anything weakened?

No.
- `no_card_data` is broader than before: it checks names as well as values, and the joined text.
- No test was loosened. The fail-first log (6 failed with `install` made a no-op, 11 passed with it) fits the tests depending on the filter.
- No control-surface file is in the diff: no `Makefile`, `package.json`, `conftest.py`, `.claude/**`, workflows or cassettes.
- No hard-rule-1 path is changed: `chat.py` is only imported.
- On test coverage: `test_sa01_access_log.py` imports the app (and so calls `install()`) before uvicorn's `dictConfig`, which is the reverse of the CLI's order. The CLI order is covered by the reasoning above (filters survive `dictConfig`, and the lifespan re-installs). I do not count this as a gap.

### Checked, no findings
- **Hard rule 1:** apart from N1 and N2, the access-log filter covers every HTTP protocol, and the portal guard is applied at the route, the API client and the search box.
- **Hard rule 2:** not changed by these fixes. Prices still come only from the live tool reads in `shop.py`.
- **Hard rule 3:** not applicable to this change. The test fixtures use the sandbox number 4242… and the Amex test number 378282246310005.
- **Hard rule 4:** `ShopReader.key` is still `repr=False`, and no key appears in the new logs or tests beyond the test key fixture.
- **Hard rule 5:** not applicable to this change.
- **Hard rule 6:** an ID from mcp-catalog is now checked by pattern before it reaches a URL (F2), and catalog text is still rendered only as React text.
- **Hard rule 7:** F5's test holds, and the internal-product 404 is unchanged.
- **Hard rule 8:** not applicable to this change. No tool scope changed.
- **Hard rule 9:** not applicable to this change. No state-changing route was added, and the 405 test holds.
- **Hard rule 10:** the malformed-ID paths give the fixed 503 `transient` envelope.
- **Hard rule 11:** the banner is unchanged in `App.tsx`.
- **Decision 0001, child processes get a placeholder key only:** not applicable to this change.
- **Decision 0001, the Makefile does not export the key:** not applicable; the `Makefile` is unchanged.
- **Decision 0001, no key in CI secrets, CI runs in replay:** not applicable; the workflows are unchanged.
- **Decision 0001, key file never copied into a worktree:** not applicable to this change.
- **Decision 0001, budget caps enforced and not raised:** not applicable to this change.
- **Decision 0001, cassettes covered by the secret/PAN scanner:** not applicable; no cassette or scanner change.

---

# Triage (the session, 2026-11-26; decision 0021's rule)

| Finding | Severity | Unsafe | Action | Evidence |
|---|---|---|---|---|
| F1 | High | Yes (hard rule 1) | **Fixed.** `access_log.py` strips every query string from `uvicorn.access`. `no_card_data` checks query names, values and the joined text. The portal's `cardGuard.ts` blocks card-like text in the route, the API client and the search box. | `test_sa01_access_log.py`: 6 failed with the filter off, 11 passed with it on. `shopSecurity.test.tsx`. The uvicorn CLI logged `GET /api/products?[query removed] … 422`, with no `4242` in stderr. |
| F2 | Low | No | **Fixed** (a real bug in the row's files). Product and variant IDs from mcp-catalog are checked by pattern. | Forged-reader tests in `test_sa01_shop.py` |
| F3 | Low | No | **Fixed.** The page and the nav wait for the first `/api/cart` read. | `shopSecurity.test.tsx` |
| F4 | Low | No | **Fixed** in `ShopReader` (confirmed: `streamable_http_client` leaves a client it was given open). `CartReader` has the same leak from before this row; it is outside the row's files and is logged in PROGRESS Open items. | `shop.py` |
| F5 | Low | No | **Fixed** with a test. | `test_sa01_photos.py` |
| N1 | Low | No | **Fixed.** `DropWebSocketQueryStrings` on `uvicorn.error`. | `test_sa01_access_log.py`: 2 failed with it removed, 13 passed with it in |
| N2 | Low | No | **Fixed.** The portal guard's separators are `[\W_]`, with `/`, `,` and `:` cases added. | `shopSecurity.test.tsx` (41 passed) |
| N1, informational | – | No | A number in a path segment is still logged. Only a client that already holds the number can do that, and no portal flow puts shopper text in a gateway path. Mentioned in the PR body. | – |

No medium or higher was fixed after the re-review, so no further re-review runs (CLAUDE.md "Running a row").
