# Decision memo: what the A2A-05 partner gets

**Decided by the user, 2026-11-25: option B** (answers at the end). **Needed before** RECORD-ALL (#18, about Dec 13) re-records `a2a-05-partner`; the script cannot get new
answers after the credit ends. Nothing here was built or run.

## The question
A2A-05 shows the assistant to a partner agent. Seven product questions, one refund request (never served) and one order turn
are frozen (decision 0002). Protocol checks run for real later; only the answer **text** is recorded now. Today the partner turn
(`run_partner_turn`) gets mcp-rag alone, while the prompt describes cart tools. So in #16's attempt the order turn said "I can
add it to your cart", which was false there. The suite has been stale since #12 and skips.

## Options
| | Surface | Prompt change | Cost at RECORD-ALL (estimate) | For | Against |
|---|---|---|---|---|---|
| A | mcp-rag only, with a partner prompt without the cart lines | Yes: prompt part moves, a $0 rescore and promote | About $0.05 (9 calls cost $0.046485 in #15) | Smallest surface | A second prompt to keep; no live price; no cart tools for the "scoped order through the cart/order tools" decision 0002 expects |
| **B** | **A shopper's anonymous surface: mcp-rag, mcp-catalog and the cart tools** | **No** | **$0.10 to $0.20** | **One prompt; the cart lines are true; live prices and stock are possible; fits 0002** | **A partner turn can write to a cart (its own, reversible)** |
| C | B plus query understanding | No | B plus up to 9 Haiku calls, cents; new frozen part-text vectors | Same first search as the portal | Every question names one product, so little to gain, and one more thing that can miss in replay |
| D | B plus northwind-orders and northwind-policy | No | More than B | None for this script | The tools answer only for a signed-in shopper (`shp`). A partner has none, so they would all be refused; giving it one would show a partner a shopper's orders (NFR-01, hard rule 8) |

Estimates are from this ledger's past calls, not a measurement. B's calls carry more tool schema and may add a catalog call.

## Recommendation: B, without query understanding and without the orders tools
- Each turn gets its own random cart session, so no partner cart meets a shopper's.
- Least privilege at protocol level stays with A2A-05: scopes decide what a partner may ask, before any model call.
- Region stays US; other partners are refused (the fixtures are US only).

## What B needs
1. **In the recorder:** pass a `CatalogEndpoint` and a session in `run_partner_turn`.
2. **Fix the offered-tools check (P3-1, logged in #15).** `offered_order_tools` matches "cart" or "order" anywhere in a tool
   name and asks only that a tool was offered. Match the exact cart tools. This matters more since CHK-06: a read-only
   `get_order` would satisfy it.
3. **At recording:** clear `cassettes/a2a-05-partner/`, update the index pins, confirm every fixture carries its cassette hash,
   and read `order-01` for "available" beside a variant (L9).

Effort: about a quarter day. Items 1 and 2 touch `evals/tests` and go in **18a** (the user), so RECORD-ALL only records. Item 3 is
#18's, with `evals/fixtures/a2a-05-partner/**` and the cassettes (control surface).

## A limit that no option removes
The order turn's text will probably still say the assistant cannot place orders, because the prompt says so. A2A-05's order
outcome is a structured artifact from the cart and order tools. **If the recorded text denies an order the artifact shows, A2A-05
returns the artifact alone** (already allowed for that turn). Confirmed by the user as the rule.

## The user's answers (2026-11-25)
1. **Option B:** an anonymous shopper's surface (mcp-rag, mcp-catalog, cart tools); no query understanding, no orders or policy
   tools. 2. **Artifact alone** when the recorded order text contradicts it: yes. 3. The recorder change and P3-1 go in **18a**, not
   #18. A decision record (or 0002's amendment) is written in 18a.
