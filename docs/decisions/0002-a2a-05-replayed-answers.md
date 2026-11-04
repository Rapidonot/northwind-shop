# 0002: A2A-05 runs real protocol checks on replayed answers

- Status: accepted
- Date: 2026-11-03
- Relates to: A2A-05 (PRD §5 Epic 6), decision 0001

## Context

A2A-05 exposes the Shopper Assistant as an A2A server. A partner test agent discovers it, asks
product questions and places a scoped order, and out-of-scope requests are rejected. The partner
journey makes the assistant call Claude. Per decision 0001, A2A (P5) is built after the API credits
expire (~2026-11-24), so the answer text cannot be generated live at that point.

## Decision

1. **During the credit window (PROGRESS #15):** the partner test client's script is fixed now in
   `evals/fixtures/a2a-05-partner/`: 5–10 product questions, one out-of-scope request and the
   scoped-order turn. The assistant's answers to exactly those messages are recorded as
   **named fixtures**, one per scripted turn. RECORD-ALL re-records them.
2. **When A2A-05 is built (Window B):** everything on the protocol side runs for real and must
   pass:
   - agent-card discovery and validation
   - authentication and scopes
   - out-of-scope rejection, enforced by deterministic scope checks rather than by the model
   - the scoped order, through the cart/order tools and port fakes

   Only the assistant's answer text comes from the named fixtures. They are matched by fixture
   name, not request hash, so metadata the A2A wrapper adds does not cause misses.
3. **Definition of done:** A2A-05 is marked **done** when those protocol checks pass. It is not
   left as permanently partial because the answer text is replayed.

## Consequences

- If the partner script changes after the window, its new turns have no fixture and need API
  access to record. The script is therefore treated as frozen once recorded.
- If the wrapper changes the assistant's prompt in a way that matters for answer quality, the
  replayed text no longer reflects it. Answer quality for partner traffic is covered by the main
  answer evals, not by A2A-05.
