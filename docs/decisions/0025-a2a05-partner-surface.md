# 0025: What the A2A-05 partner turn gets (row 18a, part 6)

Status: accepted by the user, 2026-11-25 (`docs/reviews/2026-11-25-a2a05-surface-memo.md`, "The user's answers": option B,
the artifact alone, the change in 18a). Amends 0002.

## Context
0002 records A2A-05's answer text now and runs its protocol checks later. The partner turn (`run_partner_turn`) got mcp-rag
alone while the assistant's prompt describes cart tools, so the order turn's text offered a cart that did not exist there (#16,
W3). `offered_order_tools` matched "cart" or "order" anywhere in a tool name and asked only that one was offered, so since
CHK-06 a read-only `get_order` satisfied it (P3-1).

## Decision
1. **An anonymous shopper's surface:** mcp-rag, mcp-catalog and its cart tools, region US. No query understanding, and no
   northwind-orders or northwind-policy: they answer only a signed-in shopper, and a partner has none (NFR-01, hard rule 8).
2. **A fresh random cart session per partner turn,** so no partner cart meets another's or a shopper's. It reaches only the
   MCP token, never the model, so replay is unaffected.
3. **An order turn is one whose every call was offered northwind-catalog's exact cart tools (`CART_TOOL_NAMES`) and that
   called one of them.**
4. **The artifact alone:** the order narrative is served beside its order artifact only when it plainly agrees with it. That
   means a plain confirmation of the cart action ("I added", "is now in your cart", "your cart now holds"; security
   re-review N8), no negating word in any sentence that names an order or cart action (security L6), a citation of the turn's product, and exactly the variants the artifact names,
   all of them that product's. Otherwise A2A-05 returns the artifact alone (text `None`). It is deterministic and fails closed.
   The artifact's full shape is A2A-05's to define; only the variant IDs in it are read here.

## Consequences
- The 9 fixtures and cassettes are unchanged; the suite stays stale and skips until RECORD-ALL (#18) records it, which also
  clears `cassettes/a2a-05-partner/`, updates the index pins and reads `order-01` for "available" beside a variant (L9).
- The order turn's text will probably still say the assistant cannot place orders. Then rule 4 returns the artifact alone.
