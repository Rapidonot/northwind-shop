# 0019: Cart tools on mcp-catalog, and the shopper's session (row #16, CHK-01)

Status: accepted by the user with the row #16 plan (`docs/reviews/2026-11-17-16-plan.md`), D1–D3 as recommended,
with the amendments recorded there, 2026-11-17. 0018 is reserved for row #32.

## Decision

1. **The cart lives in mcp-catalog (D1).**
   - Four tools: `get_cart` (read) and `add_to_cart`, `remove_from_cart`, `set_cart_quantity` (reversible writes
     of the caller's own cart). They are in `northwind_mcp_catalog/cart.py`; the state is in `cart_store.py`.
   - Why there: the live store is per-process memory (`FixtureCatalog`, decision D2 at #10b). In a separate server,
     a second store would never see `set_stock`, or the stock check would need a call between servers. In
     mcp-catalog, the stock read and the cart write happen under one lock.
   - PRD §7 lists "cart tools" for the Shopper Assistant beside the catalog reads, and gives the catalog subagent
     only catalog writes. `tool_scopes.CART_TOOLS` is its own set: the assistant gets all four, the new agent
     `shopper-portal` gets `get_cart` only, and the catalog subagent gets none.
   - mcp-catalog is no longer read-only. The three writes are the only tools annotated as writes, and they touch
     only the caller's cart: never the catalog, an order or a payment.
   - **Idempotency (hard rule 9).** Each write requires an `idempotency_key` of 8–64 characters from
     `[A-Za-z0-9_-]`:
     - the same key with the same arguments returns the stored result, and the cart changes once;
     - the same key with other arguments, or for another tool, gets `validation` / `idempotency_conflict`;
     - the last 50 keys per cart are kept;
     - only a write that changed the cart is stored, so a refusal retried with the same key checks stock again;
     - what is stored is the outcome only (`result`, `variant_id`, `quantity`), taken from the write's own value
       (re-review N2). A replay answers `"result": "already_applied"`, the first outcome under `"original"`, and the
       cart read live, never a stored view: it cannot carry an old price, stock level or line, nor read as a new
       change (security review F1, F5; re-review N1). The key's description says keys are remembered for the cart
       across turns, and its examples use a placeholder, not a copyable key; the prompt says an
       `already_applied` result changed nothing.
     - Open (a scope question for the user, re-review N1): a model with no memory across turns cannot guarantee a
       key it has never used for this cart. A collision is safe (nothing changes; the result says so; the panel
       reads live) but can make one change a no-op or an `idempotency_conflict` until 50 newer keys evict it.
     There is no `dry_run`: a reversible cart write doesn't need one ("where useful"). The user restored the key on
     2026-11-17, reversing an earlier scope cut.
2. **Live values only (hard rule 2).**
   - A cart stores variant IDs and quantities, never a price. Each line's unit price, line total and stock, and
     the subtotal, are read from the live store when the cart is shown.
   - An add, and a quantity increase, are checked against live stock in the token's region. Stock is checked, not
     reserved: reservation belongs to checkout (CHK-05).
   - Bounds: 1–10 units a line, at most 20 lines, at most 10,000 carts (least recently used dropped). Only a
     write creates a cart; a read of an unknown cart stores nothing, so reads cannot evict carts (security review
     F4). Carts live in memory and do not survive a restart, like the live store.
3. **Out of stock.**
   - The refusal is the fixed envelope (`out_of_stock`, or `insufficient_stock`), alone in the first content
     block, then one `<catalog_data>` label with the alternatives *the server* chose:
     - up to 3 other variants of the same product, same colour and fit first, then by variant ID;
     - then similar products by product ID, matched on department and subcategory as `search_products` matches,
       each with its same-size variant first;
     - up to 5 in all, each visible, sold and in stock for the quantity asked.
   - `catalog_tool.labelled_result` is the one reader. It accepts a label after an error only behind our own
     refusal codes.
   - What a cart result vouches in the output check (security review F2): each line's **unit price**, and a
     refusal's **requested variant's stock**. Nothing else. Line totals and the subtotal depend on quantities the
     model chose, so they vouch nothing, not even the cart's own; a cart line's stock vouches nothing either.
     #12's check vouches "out of stock" only when every variant read in the turn is at zero, so recording the
     alternatives' stock would block the one stock fact a refusal states.
   - The prompt offers alternatives by product name and variant ID as the result gives them, with no other
     attribute and no stock claim; the judge sees the same two fields (security review F8).
4. **The session (D2).**
   - The gateway binds an anonymous session to `/api/chat` and `/api/cart`: the `nw_session` cookie, holding 128
     random bits plus an HMAC-SHA256 over them. It is HttpOnly, SameSite=Strict and Path=/api, with no Secure flag
     (plain HTTP on localhost).
   - The cookie key is derived from `NORTHWIND_MCP_TOKEN_KEY` with the label `northwind-session-v1`. The user
     accepted this for the lab under the per-purpose-keys item (#10 L7 / #10b L3, owner #19/#24).
   - A missing, malformed or forged cookie gets a new session. Without the key there is no session, so the cart
     tools refuse (`session_required`) and `/api/cart` answers the fixed 503 (security review F6: the reader reads
     the key per request and never raises a 500).
   - The MCP token gains an optional signed `sid` claim (shopper tokens only). A token minted without one keeps its
     old claim set. The cart is keyed on `(sid, region)` from the verified token, and no schema takes a cart,
     session or shopper ID.
   - **No random value reaches the model (amendment 2).** `sid` lives only in the cookie and the token, never in a
     prompt, a tool argument or a tool result. Eval items derive their session from the item ID
     (`eval_session`), and each item's cart starts empty, so a rerun sends byte-identical requests.
5. **The chat panel (D3).** `GET /api/cart` reads `get_cart` as `shopper-portal` and returns it. The portal reads
   it on mount and after every turn, so the panel never shows text a model wrote. Any failure is one fixed 503
   ("Cart unavailable"), never an empty cart.
6. **Prompt (model input).** The cart may change only when the shopper asks, and only with the cart tools. If the
   shopper hasn't said which variant, the assistant asks first. A refused add offers only the listed alternatives.
   "Orders can't be placed in chat" is said only when the shopper asks to order or check out (amendment 4).

## Exceptions and open items

- None to hard rule 9: every cart write takes an idempotency key. `tests/mcp/test_cc10_contract_errors.py` pins
  that every state-changing tool requires one and that a read-only tool takes none.
- **Injection into a cart write** (amendment 5): a threat-model row, owned by Window B P6, NFR-03. A recorded
  check covers it: no cart write in any `assistant-redteam` cassette, nor in any `eval-answers` cassette for an
  adversarial item.
- No per-turn catalog call cap yet: `MAX_TURNS` (6) is still the bound (Window B P6, NFR-14).
