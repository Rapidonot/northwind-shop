# 0024: Return requests, and the policy server for signed-in turns (row #27 PR 2, CHK-07)

Status: accepted by the user with the PR 2 plan (`docs/reviews/2026-11-25-27b-plan.md`: D5, D6, D11, D14, D15 and D8's
`shp` line), 2026-11-25. Amends 0023, decision item 2.

## Context
CHK-07 asks for "return initiation with eligibility from the policy resource and an explanation of the outcome". PRD §7
gives the assistant `evaluate_return_eligibility` on a read-only `northwind-policy`. It lists no tool that records a return.

## Decision
1. **`request_return` on northwind-orders is outside PRD §7's assistant list (D6).** It records the shopper's own request
   and nothing else: non-monetary, no label, refund or case (BE-06 and Care). The server decides eligibility again itself.
   It needs an idempotency key, no portal confirmation (hard rule 9 asks that for monetary actions), and takes no
   `dry_run` (D15): `evaluate_return_eligibility` is the dry run.
2. **`shp` is also valid for northwind-policy (amends 0023 item 2),** for agent `shopper-assistant` only there. It stays
   invalid for every other server, and a token verifies only for the server it was minted for.
3. **The policy resources are for `shopper-portal` (D14),** the gateway's agent, never a model; only tests read them so far.
   The assistant keeps no resource. A server that decides returns checks its facts against the documents at start and
   refuses to start if a sentence is missing. Only public documents of the token's region are served.
4. **The date is fixed at 2026-11-15 (D5),** never the wall clock or an argument, so recordings replay after the credit ends.
5. **The "pillows and duvet inserts" rule applies to subcategory `duvet` (D11).** All 12 are filled duvets or comforters.
6. **Where the policy text does not settle it, the outcome is `ambiguous`,** never a guess: damage on arrival (at any age);
   a line held twice inside the window (after it both lines are out, so ineligible: the user's RC-061); and, in the UK and
   EU inside the 14-day no-reason period, worn items, wear and tear and opened inserts.

## Consequences
- mcp-policy and mcp-orders each seed their own in-memory store, so a scan added in one is not seen by the other.
  `request_return` decides from mcp-orders' own store.
- Return requests are in memory, like the cart, and are lost on a restart.
- No warranty document exists in data v3, so there is no warranty resource. No product is marked clearance, so that rule
  is reached only by tests.
