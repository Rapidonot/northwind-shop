# 0023: The demo sign-in, and the order tool for signed-in turns only (row #27, CHK-06)

Status: accepted by the user with the row #27 plan (`docs/reviews/2026-11-24-27-plan.md`), D1 and D3, with D3's
condition answered in the plan ("D3 at $0"), 2026-11-24. 0022 is #11's (query understanding).

## Context

CHK-06 asks for order status and tracking "for the shopper's own orders", and PRD §7 gives the assistant
`get_order (own orders)`. Until now a chat session was anonymous (decision 0019): it had a session to key a cart on,
but no shopper, so "own orders" had no meaning. Accounts (SA-04) are not built.

## Decision

1. **A demo sign-in behind a flag, with one fixed synthetic shopper (D1).**
   - `POST /api/session/shopper` takes no body. With `NORTHWIND_DEMO_SIGNIN=1` (set by `make dev`, the tests and,
     when a signed-in journey exists, e2e), it binds **S0031** (US, from `data/backend/shoppers.jsonl`) to the
     caller's session. With the flag unset, or any other value, it is the same 404 as a path that does not exist.
   - The binding is a second cookie, `nw_shopper=<shopper>.<mac>`. It is `HttpOnly; SameSite=Strict; Path=/api`.
     The mac is HMAC-SHA256 under the session cookie's derived key, over `northwind-shopper-v1`, the session ID and
     the shopper ID. It is valid only with the session it was issued for, and only while the flag is on: a new
     session, a forged or other-key mac, a non-demo shopper, or the flag turned off all leave the caller anonymous.
     A request carrying two values of either cookie gets neither: another localhost app can plant one, because
     cookies are scoped by host, not port (security review pass 1, L3). With the flag off the route gets no session
     and is the same 404 for every method, with no `Set-Cookie`.
   - The gateway's caller then gets `shopper_id`, and its region becomes the shopper's own (RAG-11).
   - There is no portal picker (SA-01, window B). This row tests through the API.
2. **`shp`, a northwind-orders-only token claim.** The signed-in shopper rides in one MCP token, northwind-orders',
   as `shp`. `mint(shopper=…)` refuses any other server or a staff audience, and `verify` refuses a token carrying
   `shp` for any other server. So `shp` can never sit beside #11's `qf` (northwind-rag only). The shopper ID never
   reaches a prompt, a tool argument, a tool result or a log; no schema takes it.
3. **northwind-orders (`services/mcp-orders`) with `get_order` only.** It is read-only, with no list tool. It answers
   only for the token's `shp` and region. Another shopper's order, an unknown number and a malformed one all get
   the same `not_found` bytes. It returns allow-listed fields only. The store and the carrier are the backend fakes,
   seeded from `data/backend` with the labelled outcomes applied, and they are read live on every call.
4. **Tools and prompt section for a signed-in turn only (D3).**
   - `stream_answer` attaches mcp-orders, and `system_prompt(region, signed_in=True)` adds `ORDERS_RULES` before
     the last rule, only when the caller has a shopper.
   - An anonymous turn's request is unchanged, byte for byte, as the replay proxy hashes it. The four regions'
     anonymous prompt hashes are pinned to main's. So no existing cassette suite is re-recorded.
5. **The answerer identity change is handled at $0.**
   - The eval answerer's ID hashes `prompt.py`, so its prompt part changes even though no eval request does.
   - The baseline moves by `make eval-rescore` (replay, $0) and the user's promote, which runs with `CHECK=1`
     first. Before the promote, every score must equal main's baseline exactly, and the answerer ID must be the only
     identity that changed; anything else is a stop.
   - The PR carries the user's `baseline-change` label.
   - A side effect, stated: per decision 0017, `eval-integrity` then lets refinements change. This row changes none.
6. **The output check (decision 0015, row #27 amendment).**
   - "Your order was placed/confirmed" and "order confirmed" pass only when the claim's subject is bound to an
     order ID written directly before it, at the sentence's start ("O000064: your order …"), and that order and every order named earlier
     in the sentence (within 64 characters) is one this turn's `get_order` returned with a confirmed status
     (`confirmed` through `delivered`). Only "placed" and "confirmed" are vouched.
   - Refund authority, "I've confirmed …" and a payment subject stay blocked always.
   - With no order read this turn, the check is unchanged.

## Consequences

- The demo is one shopper. A second shopper is a one-line change to `DEMO_SHOPPERS`; a picker is SA-01's.
- Each later edit to `prompt.py` needs its own $0 rescore and promote. Row #27's PR 2 (CHK-07) is one.
- NFR-01 is cut by 0021. Tests carry its tag where they prove own-orders-only, but no NFR-01 progress is claimed.
