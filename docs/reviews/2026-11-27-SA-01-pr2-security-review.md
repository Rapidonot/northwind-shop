# SA-01 PR 2: security-reviewer's report, verbatim

Diff reviewed: `/private/tmp/claude-502/northwind-security-SA-01-pr2.diff` (`git diff origin/main HEAD`, binary screenshots
left out), base `1909c789fa5fbb5b2eac9d0323650b00909e09dc`, head `f1db8a53be785d11780465588394a6b09af7b7a0`. The triage and
fixes follow the report, below the line.

---

## Security review: SA-01 PR 2 (diff `/private/tmp/claude-502/northwind-security-SA-01-pr2.diff`, base `1909c789fa5fbb5b2eac9d0323650b00909e09dc`, head `f1db8a53be785d11780465588394a6b09af7b7a0`)

**Summary:** 0 critical, 0 high, 1 medium (F1), 7 low (F2–F8). None of them is unsafe: no hard rule or protected path is broken. The worst one, F1, is a second charge for a line that was already paid, and it happens only after the shopper confirms a fresh summary.

**What I read:**
- `CLAUDE.md` "Hard rules", and decisions 0001 and 0005, read fresh for this review.
- Read in full from the repo: `checkout.py`, `cart_writes.py` (via the diff), `portal_write.py`, `orders.py`, `demo_orders.py`, `session.py`, the new code in `shop.py`, mcp-catalog `portal.py` and `live.py`, mcp-orders `list_orders`/`history`, the `tool_scopes.py` change, the issuer pin, `cards.py`, `orders_place.py` (handlers and `finish`), and `cartApi.ts` / `checkoutPages.tsx` (the pay key and review rendering).
- Portal styling, test bodies and the PROGRESS text: skimmed, not read line by line.
- I found no text in the diff that tries to steer the review.

---

### Findings

**F1: medium. Unsafe: no. Hard rule 9 / CHK-05 (double pay).** A paid bag that was only partly emptied can be paid for again. The commit's claim "a paid bag that could not be emptied is never paid twice" holds only when the bag is byte-identical.
- **Where:**
  - `services/gateway/src/northwind_gateway/checkout.py:398-413` (`_empty_bag`)
  - `checkout.py:472-486` (the guard)
  - `checkout.py:534-537` (where `unemptied` is set)
- **What is wrong:**
  - `_empty_bag` returns `False` at the first `Refusal` and never tries the remaining lines.
  - It treats mcp-catalog's `not_in_cart` (a business refusal, `cart.py:109-110`) as a failure.
  - The guard is looked up by `(shopper, shown.digest)`. Any change to the bag gives a new digest, so it misses.
- **Scenario A (concurrent remove):**
  1. The bag holds X and Y. The shopper presses Pay (demo).
  2. During placement (cart writes don't take the shopper lock), the shopper removes X in another tab.
  3. `_empty_bag` gets `not_in_cart` for X, returns `False` and never removes Y.
  4. `unemptied[(S, D)]` is set, but the bag is now `[Y]`, whose digest is D′.
  5. The next Pay (demo) press uses a fresh key (`checkoutPages.tsx:131`). It passes every check and places a second order: Y is authorised and captured twice.
- **Scenario B (transient failure):** removing X succeeds and removing Y hits a timeout. The result is the same as A.
- **Also:** a live price change, or `make dev`'s `--reload` (in-memory state is lost), removes the guard for a bag that still holds paid lines.
- **Tests:** `test_a_bag_that_could_not_be_emptied_still_refuses_a_second_key` (`test_sa01_pr2_tw_checkout_edges.py:451`) covers only a writer that fails completely. There is no partial-failure or concurrent-remove test.
- **Suggested fix:**
  - In `_empty_bag`, try every line, count `not_in_cart` as removed, and return whether all of the order's lines are gone.
  - Key the guard on the shopper and session plus the unemptied order's variant IDs, not the whole digest.
  - Refuse Pay while the bag still holds any variant of an unemptied order, until those lines are removed. Show the shopper the existing order ID.
  - Add failing-first tests for both scenarios.

**F2: low. Unsafe: no. Hard rule 9.** The guard's retry reuses the original removal keys, so it can report success without removing anything.
- **Where:** `checkout.py:404-406` (keys `{order_id}-paid-{n:02d}-remove`) and `checkout.py:477-478`.
- **Scenario:**
  1. The first removal of X reaches the cart and is stored under key K0, but the response is lost (timeout). `_empty_bag` returns `False` and `unemptied` is set, although the bag is in fact empty.
  2. The shopper adds X again, so the bag again has digest D.
  3. On the next Pay press, `_empty_bag` sends K0 again. The cart answers `already_applied` and removes nothing. That is not a `Refusal`, so the function returns `True`.
  4. The guard entry is popped, the shopper is told "already paid", and X stays in the bag.
- **Impact:** wrong state and wrong text, not a silent double charge. The next press is a new, confirmed purchase.
- **Suggested fix:** the retry branch uses fresh keys per attempt (for example with an attempt counter), or treats `already_applied` as "check the bag again". The guard is popped only after a fresh `get_cart` shows none of the order's lines.

**F3: low. Unsafe: no. Hard rule 1 net / availability.** The confirmation digest is hex, and hex can contain a Luhn-valid run of 13–19 digits.
- **Where:** `checkout.py:150-160` (digest = sha256 hex `[:32]`) and `checkout.py:454-456`.
- **Scenario:** `contains_card_data(body.digest)` tests every 13–19-digit window of a digit run (`cards.py:67-72`). For some bags the digest holds such a run. That bag's Pay always raises `CardNumberRefused`, because the digest is deterministic, so the shopper can never pay for that exact bag.
- **Rate:** unconfirmed. My estimate is well under 1% of bags. `orders_place.py:394-396` already avoids hex for this reason ("about 1 run in 40"), and `cartApi.ts:55-61` uses letters only for the same reason.
- **Suggested fix:** encode the digest in letters only (for example, map each nibble to a–p) and update `DIGEST`. Keep the card check exactly as it is.

**F4: low. Unsafe: no. Hard rules 9 and 10.** An exception after `issue` leaves no record and returns an unstructured 500.
- **Where:** `checkout.py:504-510` (no `try`), `checkout.py:87` and `checkout.py:315-318`.
- **What is wrong:** an exception raised inside `place` or `_outcome` escapes the route as FastAPI's plain-text 500, not an envelope. `desk.paid` and `desk.unemptied` are not written, and the bag is not emptied.
- **Concrete trigger:** `new_order` formats `O{number:06d}` from 900 001, and the counter goes up on every attempt that reaches `new_order`, including orders popped at `shortfall`. After 99 999 attempts in one process the ID becomes `O1000000`. That fails `_ORDER_ID = O\d{6}` in `OrderPlacementTask.__post_init__` (`orders_place.py:279-280`), after the confirmation was issued.
- **Other paths:** I did not confirm any other post-capture raise. The handlers return envelopes, and `place` reads only `isError` and `code` after authorize.
- **Suggested fix:**
  - Refuse with a fixed business envelope before `issue` when the counter passes 999 999.
  - Wrap the code after `issue` so that any exception is answered with a transient envelope. That path records an escalated outcome where one can be established, and records `unemptied` if a capture may have happened.

**F5: low. Unsafe: no. Unbounded in-memory state.**
- **Where:** `checkout.py:294-305` and `demo_orders.py:56-57`.
- **What grows and is never pruned:** `desk.paid`, `desk.orders.orders`, the confirmation book's `_issued`, the payment, fraud and inventory records, the `InMemoryAuditSink`, and `demo_orders._by_shopper`. Each grows on every pay that reaches `issue`.
- **What is bounded:**
  - `_locks`: `DEMO_SHOPPERS` has one entry (`signin.py:31`).
  - `unemptied`: bounded by placed orders.
  - Placed orders: bounded by the inventory store's stock.
- **Unconfirmed:** escalated or declined orders release their hold. If a decline or review is reachable for S0188, the same bag can be escalated over and over without using up stock.
- **Related (cheap cart eviction):**
  - `from_portal` (`portal_write.py:34-45`) passes a non-browser client that sends neither `Sec-Fetch-Site` nor `Origin`.
  - A local script that sends `POST /api/cart/items` with no cookie gets a fresh session each time (`session.py:184-187`).
  - With no model turn now needed, the mcp-catalog cart store's 10 000-cart LRU can be churned cheaply, which evicts real sessions' carts.
  - Reach: local only (uvicorn binds 127.0.0.1).
- **Suggested fix:**
  - Cap `paid`, the demo orders and the desk's order map per shopper, with a fixed `business` refusal when a cap is reached rather than dropping silently.
  - Consider a per-session cap on new carts or on write rate at the gateway.

**F6: low. Unsafe: no. Session isolation.** The `unemptied` guard is per shopper, not per session.
- **Where:** `checkout.py:302`, `checkout.py:472-486` and `checkout.py:537`.
- **Scenario:** every demo sign-in is the same shopper, S0188.
  1. Session A's bag (digest D) is placed but not emptied.
  2. Session B, a different browser signed in as S0188, has an identical bag and presses Pay.
  3. B is refused "already paid" with A's order ID.
  4. `_empty_bag(writer, caller=B, earlier)` removes B's lines from B's cart, using A's order keys.
- **Not reachable:** another shopper's cart.
- **Suggested fix:** key `unemptied` on `(shopper, session, …)`.

**F7: low. Unsafe: no. Cross-site write guard.** `from_portal` trusts the `Host` header.
- **Where:** `portal_write.py:38-45`.
- **Scenario (DNS rebinding):**
  - A page at `evil.example:8000` that resolves to 127.0.0.1 sends `Sec-Fetch-Site: same-origin`, and its `Origin` matches `Host`, so the guard passes.
  - The browser sends no localhost cookie, so the attacker acts only in a new session of its own.
  - With the demo sign-in flag on, it can sign in as S0188, add to its bag, pay, and read S0188's order history (synthetic data).
- **Not reachable:** a victim's cart or session.
- **Context:** the same class already exists on `/api/handoff` and `/api/chat`. The module docstring's "never from another site" is stronger than what the code checks.
- **Suggested fix:** an allow-list of `Host` values (`127.0.0.1` / `localhost` on the configured ports), for example Starlette's `TrustedHostMiddleware`. This adds a check; it removes none.

**F8: low. Unsafe: no. Decision 0027 §1 pin coverage.** The issuer pin has gaps.
- **Where:** `services/backend/tests/test_be05_prerequisites.py:218-223` and `226`.
- **Roots not covered:** `evals/src` and `tools/` hold Python (`evals/src/northwind_evals/*.py`, `tools/changed_tests.py`) and are not in `ISSUER_ROOTS`.
- **Forms not caught:** a direct write to `book._issued[...]` (which skips `issue` entirely), `operator.attrgetter("issue")`, and `getattr(book, "is" + "sue")`.
- **Suggested fix:** add `evals/src`, `tools` and any other repo Python outside the tests to the roots, and flag any `_issued` attribute outside `confirmations.py`. Keep the exactly-one-site assertion.

---

### Checked, no findings

**Hard rules**
- **Hard rule 1:**
  - No card field anywhere in checkout.
  - The order carries `SANDBOX_APPROVED` only.
  - Pay, cart-write and query values are card-checked before any use (`checkout.py:454-456`, `cart_writes.py:435-437`, `shop.py` `_int_value` / `_ids_value`).
  - Logs carry type names only.
  - `demo_orders` and `list_orders` drop the token and postal code.
  - The confirmation reference is never in a response or a model input.
  - The digest issue is F3.
- **Hard rule 2:**
  - The summary and `LiveCartCatalog` take price and stock only from the `get_cart` read made in that same request (`checkout.py:258-277`).
  - The confirmation amount is the subtotal that was shown.
  - `browse_products` with `priced: false` reads no price, and only the page on screen is priced.
  - Reviews carry no price field.
- **Hard rule 3:** S0188, sandbox token, fixture data only.
- **Hard rule 4:** the signing key comes from the environment, httpx uses `trust_env=False`, tokens are never logged, and no `.env` is touched.
- **Hard rule 5:** not applicable. No model output; the specs grid shows only the record's own fields.
- **Hard rule 6:**
  - `list_reviews` is portal-only. No model holds a token for it; the server scopes per agent (`mcp-orders/server.py:106-122`, and the catalog likewise).
  - Review text is rendered as React text (`pages.tsx:203-209`). No `dangerouslySetInnerHTML` anywhere.
  - `/api/chat` takes only `{message}`.
- **Hard rule 7:**
  - Internal products and reviews are filtered with `_sees` (`portal.py:184-191`), and `product_ids` is filtered the same way.
  - Substitutes are public only (`substitutes.py:74,87`).
  - `history()` filters by the token's `shp` and region.
  - The gateway never takes a shopper ID from the request.
- **Hard rule 8:**
  - `shopper-assistant` scopes are unchanged.
  - `shopper-portal` gains exactly `CART_WRITE_TOOLS` and `list_orders`.
  - `PORTAL_AGENT` tokens are minted only in gateway modules, plus the assistant package's read-only `CartReader`.
  - No payment, capture, refund or case tool reaches any agent.
- **Hard rule 9:**
  - Pay is confirmed by the Pay (demo) button against the digest of what was shown.
  - The deterministic order limit is checked before `issue` (`DEFAULT_ORDER_LIMITS`).
  - Idempotency is per `(shopper, key)`, the same key with another digest gives a 409, and a per-shopper lock is held.
  - The 8-key and 8-same-key races are tested.
  - Cart writes take a key (16–64 characters) and use a stable per-session turn.
  - Gaps are F1, F2 and F4.
- **Hard rule 10:**
  - Every refusal is an envelope with fixed text.
  - An empty bag is `issues: ["empty"]`, distinct from 503.
  - `/api/orders` gives 401 `permission` when not signed in, and an empty list is valid.
  - `list_reviews` with no store is a transient error, not an empty list.
  - The exception is F4.
- **Hard rule 11:** `AiDisclosure` is still rendered in `Chat.tsx:247`, and the banner is unchanged.

**Other items checked**
- **Sessions:** every PR 2 write and read path is in `SESSION_PATHS` as an exact path. `/api/reviews` has no session and uses the default region; that is harmless, because reviews and product visibility do not depend on region. The cookies are `SameSite=Strict` and `HttpOnly`.
- **F4 fix:** `CartReader` now closes its client (`catalog_client.py`).
- **`/_requests`:** the scripted model's counter route counts only and checks no credential. It is e2e-only.

**Decisions 0001 and 0005, key handling**
- Child processes get a placeholder key only: no change. Playwright `modelEnv` is replay with no key, and `scripted_model.py` still requires the placeholder.
- The Makefile does not export the key: the Makefile is not in this diff.
- No key in CI secrets, and CI runs in replay: no `.github/` change.
- The key file is never copied into a worktree: not touched.
- Budget caps are enforced and not raised: no proxy, ledger or budget change.
- Cassettes are still covered by the secret/PAN scanner: no cassette or scanner change.
- Pay calls no model: `run_order_placement` and `run_subagent` are not referenced in `checkout.py`.

---

## Triage (the session, under CLAUDE.md's triage rules; no critical or high, none unsafe)

| Finding | Severity | Decision |
|---|---|---|
| F1 | medium | Fix (the row's files), with failing-first tests for both scenarios; a scoped re-review follows. |
| F2 | low | Fix (a real bug in the row's files): fresh keys per attempt, and the guard dropped only after a fresh `get_cart` shows none of the order's lines. |
| F3 | low | Fix: a letters-only digest. |
| F4 | low | Fix: refuse before `issue` at the order-number cap; any exception after `issue` is a transient envelope and holds the bag. |
| F5 | low | Fix in part: a per-shopper cap on demo orders before `issue` (bounds every per-pay structure). The cart-store churn by cookieless scripts is logged in PROGRESS "Open items" (owner: the next gateway hardening row; it predates this row's routes). |
| F6 | low | Fix: the guard is keyed on shopper and session. |
| F7 | low | Logged in PROGRESS "Open items" (owner: the next gateway hardening row). The same class exists on `/api/chat` and `/api/handoff`, outside this row's files; a `Host` allow-list is app-wide. The `portal_write.py` docstring is corrected to say what the check covers. |
| F8 | low | Fix: `evals/src` and `tools` added to the roots; `_issued` outside `confirmations.py` and `attrgetter` are flagged. |
