# 0027: The portal's demo checkout is CHK-02's confirmation control (SA-01 PR 2)

Status: accepted by the user, 2026-11-26 (the SA-01 plan, S1, with Amendment 1) and 2026-11-27 (Amendment 3; the stop 5
answers for the cart writes, `list_orders`, the order and cart-write route pin, and the confirmation-issuer pin). Amends 0014
(confirmation references: until CHK-02, only a test fixture issued one).

## Context
Hard rule 9 and CHK-02 need an order summary confirmed through a portal UI control, never free text in chat. Decision 0014
built the confirmation book behind the payment fake: `authorize` and `capture` refuse without a reference bound to one order,
currency and exact amount, and an AST pin kept every `issue` call out of `src` until the control existed. BE-04 (#25) shipped
the inventory store, and BE-05 (#21) the Orders agent's step handlers, callable without a model. SA-01 PR 2 builds the
portal's checkout on them.

## Decision
1. **The gateway is the one confirmation issuer.** `POST /api/checkout/pay` (`northwind_gateway.checkout.pay`) is the only
   call site of the confirmation book's `issue` in the repo. The backend's own pin stays as it was for `services/backend/src`;
   a second pin covers every other repo Python root (`services/*/src`, `services/*/e2e`, `services/*/spike`, `evals/src`,
   `data/src`, `tools`, the external agents and the partner agent), catches aliases, attribute chains, the book's `_issued`
   store, imports and string constants (folded across `+` and f-strings, so `getattr` and `attrgetter` are caught), and
   allows exactly that one site. A third pin flags `_issued` anywhere in the backend but `confirmations.py`.
2. **CHK-02's control is the Pay (demo) button.** The shopper sees the summary (`GET /api/checkout/summary`: lines, total,
   delivery, the region's returns policy, a digest of what is shown) and presses Pay (demo). The route reads the live bag again
   and refuses a changed digest, an issue, an amount over the order limit or stock already short, all before issuing. Signed in
   only (the CHK-06 demo shopper). No card field exists: the order carries the sandbox token.
3. **No placement is re-implemented and no model runs.** The route calls BE-04's inventory store and `orders_place`'s handlers
   in fixed code, in the system prompt's order, then `finish`. It never references `run_order_placement` (which uses
   `ROUTINE_MODEL`) or `run_subagent`. `orders_place` imports the agent runtime, so the plan's "no LLM module in the import
   closure" cannot hold; condition (3) is restated (Amendment 3): every LLM entry point raises during a full pay, an AST check
   pins the names, and the replay proxy counts zero requests (pytest; and the canned model under `make e2e-scripted`).
4. **The live store, not the fakes' table, prices and stocks the order.** The catalog port for one pay is the bag `get_cart`
   just read from the live store (hard rule 2). The inventory store's own stock holds the units (BE-04's known gap stays: the
   catalog's live stock does not subtract holds, so a lost last unit shows "no longer available" with BE-04's substitutes).
5. **Demo orders are kept apart from the seeded store.** They live in the gateway's process (`demo_orders`), with the
   allow-listed fields only, and join the shopper's history beside mcp-orders' `list_orders`. The assistant's `get_order`
   does not see them: a known limit.
6. **One result per (shopper, key), under one lock per shopper.** The same key with another digest is refused; two keys racing
   on one bag make one order. After a placed order every line is removed, with fresh keys on each attempt, and only a fresh read
   of the bag says it is empty. While a session's bag still holds any line of a paid order it could not empty, Pay is refused
   (`already_placed`) and the refusal removes those lines; the hold is per shopper and session, in the gateway's memory (a
   reload loses it: an open item for the P4 row that makes the desk durable).
7. **Caps before any confirmation:** the order limit, a per-shopper order cap and the order-number cap. A failure after the
   confirmation is a fixed transient envelope, holds the bag, and escalates the order (shown on hold).
8. **The write guard** (`portal_write.from_portal`): an allowed `Host` name (`PORTAL_ALLOWED_HOSTS`, default the local ones), a
   JSON body, the portal header, and same-origin fetch metadata or `Origin`.

## Consequences
- CHK-02 becomes `partial`: the control exists for the portal; the summary has no delivery date (there is no live delivery
  estimate yet) and a real provider's hosted fields are P4 (PAY-05).
- The demo orders and the fakes' states are in memory and reset with the gateway process.
- A future real checkout replaces `CheckoutDesk`'s fakes with the payment port, keeping the route, the pins and the control.
