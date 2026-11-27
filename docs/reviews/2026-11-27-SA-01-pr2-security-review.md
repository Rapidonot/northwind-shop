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
| F5 | low | Fix in part: a per-shopper cap on demo orders before `issue` (bounds every per-pay structure). The cart-store churn by cookieless scripts is logged in PROGRESS "Open items" (owner: the next gateway hardening row). Corrected after the re-review: this PR's `POST /api/cart/items` is what makes the churn cheap; it did not predate the row. |
| F6 | low | Fix: the guard is keyed on shopper and session. |
| F7 | low | Logged in PROGRESS "Open items" (owner: the next gateway hardening row). The same class exists on `/api/chat` and `/api/handoff`, outside this row's files; a `Host` allow-list is app-wide. The `portal_write.py` docstring is corrected to say what the check covers. |
| F8 | low | Fix: `evals/src` and `tools` added to the roots; `_issued` outside `confirmations.py` and `attrgetter` are flagged. |

---

# Scoped re-review of the fixes (`f1db8a5..8d902a2`), verbatim

## Scoped re-review of SA-01 PR 2's security fixes

The diff was `/private/tmp/claude-502/northwind-security-SA-01-pr2-fixes.diff`. Its SHA lines match the request: base `f1db8a53be785d11780465588394a6b09af7b7a0`, head `8d902a2a3134ff73fd531655cb06f3682d60b5a8`.

**Result:** 0 critical, 0 high, 0 medium, 5 low (N1–N5). None is unsafe. F1, F2, F3, F5's first half and F6 are closed. F4 is closed, with one caveat (N2). F8 is only partly closed (N5). N1 is part of F1 that the fixes did not address: its "Also" clause about a gateway restart.

**What I read:** CLAUDE.md "Hard rules" and decision 0001, both fresh. I read these in full: the diff, `checkout.py` at head, the new tests, `_portal_world.py` and the widened pin. I also read the parts that matter of `cart_writes.py`, `catalog_client.py` (`CartReader.read`), `settings.py` (`shopper_caller`), mcp-catalog `cart.py` (`_remove` and `_idempotent`) and `cart_store.py` (how carts are keyed), `orders_place.py` (capture, confirm, escalate, `finish`), `fakes/orders.py`, `demo_orders.py`, `checkoutPages.tsx`, `cartApi.ts` and the Makefile's `dev-gateway`. No text in the diff tried to steer the review.

---

### Findings

**N1: low. Unsafe: no. Hard rule 9 / CHK-05. The F1 "Also" clause is neither fixed nor logged.**
- **Where:**
  - `services/gateway/src/northwind_gateway/checkout.py:311`: `unemptied` lives only in memory.
  - `/Users/rapido/Projects/northwind-shop-b/Makefile:114`: `uvicorn northwind_gateway.app:app --reload`.
- **Scenario:**
  1. A placed order's bag cannot be emptied, so the bag is held.
  2. The gateway reloads after a code edit (`make dev`). mcp-catalog is a separate process that does not reload, so the cart keeps the paid lines. The session cookie stays valid, because the key comes from the environment.
  3. `checkout_desk()` starts empty, so the next Pay (demo) press places a second order for those lines.
- **Limits:** the fake payment state, `demo_orders` and the order numbers are lost too, so this is a demo-only effect.
- **Fix:** log it in PROGRESS "Open items" with an owner (the P4 payment port, or whichever row makes the desk durable). Fixing it in this row would need persistent state.

**N2: low. Unsafe: no. Hard rules 9 and 10. The F4 except path holds the bag even when nothing was charged, and it leaves no record.**
- **Where:**
  - `checkout.py:555-567` (the except path)
  - `checkout.py:103-106` (`ALREADY_PLACED`)
  - `checkout.py:518-527` (the refusal)
- **Scenario:**
  1. `place` raises before authorize, for example in `OrderPlacementTask.__post_init__` or in `validate_order`. Nothing moved.
  2. The except path calls `hold_bag`. `_outcome` never ran, so the order is not in `demo_orders` and was never escalated.
  3. The 503 text says "Please try again", but `isRetryable` is `False`.
  4. On the next press the shopper is told "an order you already paid for ... check your orders", and their lines are removed. Their orders page does not show that order.
- **Also:** the comment "The order stays in the desk for a person" is not accurate. Nothing escalates the order, so no person's queue ever shows it. The original suggestion was to record an escalated outcome where one can be established; that was not done.
- **Fix:**
  - Keep the hold; it is the safe direction.
  - In the except path, try once to escalate through the handlers and record the order in `demo_orders` as `on_hold`.
  - Use a fixed text that does not claim a payment, for example "An earlier payment attempt is being checked."
  - Make the 503's text agree with `isRetryable: false`.
- **Not breaking it:** the hold is not permanent. It is released by the first Pay whose bag holds none of the order's variants, or by a successful removal, and it is bounded by the order cap.

**N3: low. Unsafe: no. Hard rule 9. `_empty_bag`'s fresh read fails open on a malformed cart body.**
- **Where:** `checkout.py:442-452`.
- **Scenario:**
  1. `CartReader.read` returns any dict whose region matches.
  2. If its `lines` key is missing, or is not a list of dicts, then `held` is empty and the function returns `True`, so no hold is written.
  3. The same body in `pay`'s `_summary` raises `Unreadable` and is refused.
- **Reachability:** unconfirmed. It needs mcp-catalog to return a malformed body.
- **Fix:** run the read through `summarize(bag)` and use its `lines`. If it raises `Unreadable` or the bag is `None`, return `False`.

**N4: low. Unsafe: no. Hard rule 9 (UX only). The refusal path removes whole lines, and the portal does not refresh the bag afterwards.**
- **Where:**
  - `checkout.py:518`
  - `_remove` in mcp-catalog `cart.py:333-340` (it deletes the whole line)
  - `apps/portal/src/checkoutPages.tsx:145-146`
- **Scenario:**
  1. The shopper paid for X×1. The bag was held, and the shopper then set X to 3 on purpose.
  2. The next Pay is refused, and the refusal removes all 3, not only the 1 that was paid.
  3. On `already_placed` the portal calls neither `onCartChanged()` nor `reload()`, so the summary and the bag badge show lines that were just removed.
- **Why it is acceptable:** this cannot cause a second charge, because any later order still needs a fresh summary and a press. After one successful refusal the guard is cleared (`kept == []`), so the shopper can buy X again.
- **Fix (optional):** reload the summary on `already_placed`, or lower X by the paid quantity instead of removing it. Otherwise mention it in the PR body.

**N5: low. Unsafe: no. Decision 0027 §1. F8 is only partly closed.**
- **Where:**
  - `services/backend/tests/test_be05_prerequisites.py:196-208`
  - same file, `:218-227`
  - same file, `:240-243`
- **What is still open:**
  - **`_issued` in the backend.** `services/backend/src` is checked only by the older test, which flags `.issue` and `issue` and not `_issued`. A backend module other than `confirmations.py` that writes `book._issued[...]` passes both pins. The original suggestion asked for exactly this check.
  - **Computed names.** The review listed `getattr(book, "is" + "sue")`. It is still not caught, and there is no planted case for it. The same goes for f-strings and `"".join`.
  - **Roots.** The comment says "every other tree of repo Python outside the tests", but `services/llm-replay/spike/*.py` (non-test Python) is not in the roots.
- **Not over-matching:** I grepped every `.py` in the repo for the literals `"issue"` and `"_issued"`. They appear only in the pin's own test file, so the new constant rule flags nothing legitimate today. A future match would fail closed.
- **Fix:**
  - Add the `_issued` attribute and constant check to the backend test, exempting only `confirmations.py`.
  - Fold `BinOp` and `JoinedStr` nodes made only of constants before comparing, and add a planted `"is" + "sue"` case.
  - Either add `services/*/spike` to the roots, or scan the whole repo minus `tests/` directories.
  - Keep the exactly-one-site assertion.

**Unconfirmed, informational:**
- **Cancellation is not caught.** `except Exception` does not catch `asyncio.CancelledError`. If the route were cancelled after `issue`, `paid` and the hold would not be written, and a retry would place a second order. I found no path that cancels it: `SessionMiddleware` is pure ASGI (`session.py:207`), not `BaseHTTPMiddleware`, and uvicorn does not cancel an HTTP handler when the client disconnects. Only a shutdown could trigger it.
- **Hardening idea:** write the hold, and an in-progress `paid` entry, before `issue` inside `try/finally`, and clear them once the bag is proven empty. That would cover this case, and N2's text as well.
- **Outside this diff:** if `confirm_order` failed after a capture, the result would be `on_hold` "Nothing was charged" with no hold. I could not see a way for the checkout's fakes to fail there (`fakes/orders.py:129-170`).

---

### Answers to the four questions

**1. Does each fix close its finding?**
- **F1:** closed in-process.
  - `_empty_bag` tries every line, so `not_in_cart` no longer stops it early.
  - The fresh read decides the result.
  - The guard is keyed on shopper and session, and it matches on variant overlap with the bag, not on the digest.
  - Tests: scenario A (`test_f1_a_line_removed_during_placement_still_leaves_the_bag_empty`) and scenario B (`test_f1_a_line_that_failed_to_clear_is_never_paid_twice`).
  - A live price change no longer drops the guard. The restart residual is N1.
- **F2:** closed. Each attempt's keys are `{order}-clear-{attempt}-{n}`. An `already_applied` or `idempotency_conflict` answer can no longer report success, because only the read decides. Key collisions after a restart also fall back to the read.
- **F3:** closed. The digest is letters a–p (`checkout.py:168-169`) and `DIGEST` is `[a-p]{32}`. The card check is unchanged, and the portal does not check the digest's alphabet (`cartApi.ts:128`).
- **F4:** closed for both parts.
  - `LAST_ORDER_NUMBER` is checked before `issue`, with no `await` between `can_order` and `new_order` (`:538-540`), so no other request can run in between.
  - The except path answers with an envelope and holds the bag. Caveats: N2.
- **F5, first half:** closed. Every per-pay structure grows only after `issue`, and each one is bounded by the 200-order cap. All demo sign-ins are S0188, so the cap is effectively one per process: after 200 orders, Pay refuses everyone with `demo_full` until a restart. That fails closed, which is acceptable.
- **F6:** closed. The guard is keyed `(shopper, session)`, and the test shows session B is neither blocked nor emptied.
- **F8:** partly closed (N5).

**2. Is F1 still reachable another way?** Not within one process.
- **The pop at `:528-529`.** It runs only when the fresh, locked read holds none of the orders' variants.
- **Region.** Carts are keyed `(session, region)` (`cart_store.py:28,73`). Pay is signed-in only, and a signed-in shopper's region is fixed (`settings.py:43`), so the read is always of the cart the order came from. There is no region-switch bypass.
- **Cart writes that don't take the lock:**
  - A concurrent add during clearing makes the read show the variant again, so the shopper is refused again. That is safe.
  - A removal that times out but lands later can only remove a later deliberate re-add. That is UX only.
  - An eviction from cart churn removes the lines, which is the safe direction.
- **Other sessions.** Carts and guards are both per session, so a second browser cannot clear or block another's guard.
- **Remaining paths:**
  - A restart (N1).
  - A fail-open parse in the clear check (N3, unconfirmed).
  - Cancellation (unconfirmed, see above).

**3. Anything new under the 11 hard rules?** Nothing unsafe.
- **The refusal removing lines:** N4, acceptable.
- **The F4 hold:** not permanent, but misleading (N2).
- **`can_order`:** has no race and fails closed. It is effectively global because there is one demo shopper.
- **The widened pin:** flags nothing extra today, but still misses some forms (N5).
- **Logs:** they carry type names only.
- **Clear keys:** at most 6-digit runs, so they cannot trip the card screen.

**4. Are F7 and F5's second half acceptable as logged?** Yes, both. Neither has to be fixed in this row.
- **F7:**
  - It is low and not unsafe: a DNS-rebinding page only gets a new session of its own, on synthetic data.
  - The same class exists on `/api/chat` and `/api/handoff`, and the real fix is a `Host` allow-list for the whole app. `chat.py` is a hard-rule path outside this row.
  - The owner is named (#17, which exists).
  - The docstring now states what the check actually covers.
- **F5's second half:** it is local-only and has no monetary effect; an evicted held cart loses its paid lines, which is the safe direction. One correction: the triage says "it predates this row's routes", but `POST /api/cart/items` is new in this PR (`app.py:72`), and that route is what makes the churn cheap. Fix the wording in the review file and in PROGRESS. Logging it is still right.

---

### Checked, no findings

**Hard rules**
- **Hard rule 1:** the digest is letters only. Clear keys have no 13-digit run. The pay card screen is unchanged. Logs carry type names only. No card field was added.
- **Hard rule 2:** `_empty_bag` reads variant IDs only. Price and stock still come only from that request's `get_cart`.
- **Hard rule 3:** not applicable to this change (no data added).
- **Hard rule 4:** not applicable to this change (no secrets or `.env` touched).
- **Hard rule 5:** not applicable to this change (no model output).
- **Hard rule 6:** not applicable to this change (no retrieved text). The new messages are fixed text.
- **Hard rule 7:** reads and writes stay scoped to the caller's session and region. Not otherwise touched.
- **Hard rule 8:** no scope change. The clear uses `remove_from_cart` as `shopper-portal`, which already holds it.
- **Hard rule 9:** covered by F1, F2, F4 and F6 (closed) and N1–N4 (low).
- **Hard rule 10:** the `demo_full`, `already_placed` and F4 503 responses are all full envelopes. The text mismatch is N2.
- **Hard rule 11:** not applicable to this change.

**Decision 0001 and key handling**
- **Placeholder key for child processes:** not applicable; no LLM client or subprocess change, and Pay still calls no model.
- **Makefile does not export the key:** the Makefile is not in this diff.
- **No key in CI secrets, CI runs in replay:** no `.github/` change.
- **Key file never copied into a worktree:** not touched.
- **Budget caps enforced and not raised:** no proxy, ledger or budget change.
- **Cassettes still covered by the secret/PAN scanner:** no cassette or scanner change.

**Files:**
- `/Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/checkout.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/tests/test_be05_prerequisites.py`
- `/Users/rapido/Projects/northwind-shop-b/services/gateway/tests/sa01_checkout/test_sa01_pr2_security_fixes.py`
- `/Users/rapido/Projects/northwind-shop-b/apps/portal/src/checkoutPages.tsx`
- `/Users/rapido/Projects/northwind-shop-b/Makefile`
- `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-27-SA-01-pr2-security-review.md`

## Triage of the re-review (no critical, high or medium; none unsafe; only lows, so no further re-review)

| Finding | Decision |
|---|---|
| N1 | Logged in PROGRESS "Open items" (owner: the P4 payment-port row that makes the desk durable). In-memory by design (decision 0027, Consequences). |
| N2 | Fixed: the except path records the order as escalated and on hold in the history, with fixed texts that claim no payment and agree with `isRetryable: false`. |
| N3 | Fixed: the clear check reads the bag through `summarize`; an unreadable bag fails closed (held). |
| N4 | Fixed in part: the portal reloads the summary and the bag after `already_placed`. Removing whole lines stays (the safe direction), mentioned in the PR body. |
| N5 | Fixed: a new pin flags `_issued` in the backend outside `confirmations.py`; constant `+` and f-strings are folded (planted `"is" + "sue"` case); `services/*/spike` added to the roots. The old backend check stays unchanged, as the plan says. |
