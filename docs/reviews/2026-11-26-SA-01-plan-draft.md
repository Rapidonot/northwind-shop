# SA-01 plan draft: the portal pages (paperwork only; not started)

Status: a **draft** for the user, written 2026-11-26 while row #23 (SA-06) waits for its phase C window. No branch,
no code. When it starts, it goes through `/req-start SA-01`, plan mode and `advisor-reviewer` as usual.

## Criterion
PRD SA-01: "Portal with home, search results, product detail, cart, checkout, order history and a chat panel."
Acceptance: *scripted browser journeys cover every page without error.* PROGRESS: Window B, "Portal (P2)". The cart
page comes from #16 (CHK-01: "the cart page is SA-01, Window B").

## Shape: $0, no model
- **No page calls the model.** The chat panel is mounted on every page as it is today (`ChatPanel`), unchanged.
- Every journey runs under `make e2e-scripted` (a canned model) and `make e2e` (replay). No new recording, no window,
  no spend, nothing under `cassettes/`.
- **Pages read only through the gateway, as `shopper-portal`, never a model:**

| Page | Reads | New gateway route | Notes |
|---|---|---|---|
| Home | none (static copy, featured links) | none | entry point; the AI disclosure in the chat panel |
| Search results | `search_products` (mcp-catalog) | `GET /api/products?text=&department=…` | public records only (hard rule 7); no price in the list (prices are live, below) |
| Product detail | `get_product`, `get_variant`, live `get_price` and `check_stock` | `GET /api/products/{id}` | price and stock come from live tools only (hard rule 2); attributes from the catalog record, not model text |
| Cart | `get_cart` (row #16's `CartReader`) | existing `GET /api/cart` | read-only in this row: changes stay in chat (scope question 2) |
| Checkout | the cart, read-only | none | a stub. "Place order" stays disabled and says checkout is not available yet. CHK-02 to CHK-05 own the payment port, hosted fields and the UI confirmation (hard rules 1 and 9). No card field of any kind |
| Order history | the signed-in shopper's orders | `GET /api/orders` (scope question 3) | demo sign-in only (decision 0023); anonymous gets a sign-in prompt |

- **Routing:** a small hash router in `apps/portal/src/` (`#/`, `#/search`, `#/product/P0011`, `#/cart`,
  `#/checkout`, `#/orders`), so there is no new dependency. A new package would change `package.json` and
  `pnpm-lock.yaml`, both on the control surface.
- **Gateway errors:** the structured envelope with fixed text (hard rule 10). A page shows "unavailable", never an
  empty list, as `GET /api/cart` does.

## Tests
- **Playwright, one journey per page, in a new `apps/portal/e2e/pages.e2e.ts`,** each `[SA-01]`, each through
  `journey.ts`, so each ends by asserting the AI disclosure:
  - home → search → product detail → cart → checkout (disabled order button) → order history (signed in, and
    anonymous);
  - each page's empty and unavailable states;
  - a product's live price changed while the server runs is the one shown (row #12's `set_price` pattern).
- **Vitest** per page component, and the router.
- **pytest** for each new gateway route:
  - caller from the server only;
  - an internal product is the same `not_found` as an unknown one;
  - no price from anything but `get_price`;
  - card data refused in query parameters;
  - fixed envelopes;
  - no session gives a fixed 503.
- The journey guard (`src/journeyGuard.test.tsx`) already makes every `*.e2e.ts` use `journey.ts`.

## Must not touch until SA-06 ships
SA-06 (row #23, `req/SA-06-human-handoff`) is mid-row and owns these. SA-01 must not edit them until it is merged:
- `apps/portal/src/Chat.tsx` and `apps/portal/src/chatStream.ts` (the user's list);
- also `apps/portal/src/HandoffNotice.tsx`, `apps/portal/e2e/handoff.e2e.ts`,
  `services/gateway/src/northwind_gateway/{chat,handoff,handoff_trigger,session}.py`, and `app.py`'s router lines
  (SA-01 adds its routers in `app.py` too, so it starts from main after SA-06's merge, or the merge resolves one
  block).

SA-01 mounts `ChatPanel` and does not change it. If a page needs the chat panel to behave differently, that waits for
SA-06's merge.

## Control surface it would touch
- `services/common/src/northwind_common/tool_scopes.py`: `shopper-portal` gains the read tools above (today it has
  `get_cart` only). This is runner-sync, and a hard rule 8 path.
- `services/gateway/pyproject.toml` only if a new dependency is needed (none expected).
- No `package.json` change, given the hash router.

**Not routine:** `tool_scopes.py` and the gateway are protected paths.

## Scope questions for the user
1. Do search results show a live price per result? That would mean N `get_price` calls per page, or a batched
   read-only tool. The draft's answer: no, the price shows on the product detail page only.
2. Does the cart page change quantities with its own UI controls? That needs gateway write routes and cart write
   tools for `shopper-portal`, which is a new write path. The draft's answer: no, changes stay in chat (#16).
3. Order history needs a list read. PRD §7 has `list_orders` for no agent today (`get_order` only). Should
   `shopper-portal` get a list tool on mcp-orders (own orders, by the token's `shp`)? Or should the page show only
   the orders the demo shopper has, one `get_order` each from a fixed list? The draft's answer: a list tool for
   `shopper-portal` only, never the assistant.
4. Checkout: is a disabled stub enough for SA-01's "covers every page", with CHK-02 to CHK-05 building the real one?

## Estimate
About 1 day. Pages and router about 0.4, gateway routes and scope about 0.3, journeys and tests about 0.3. $0.
