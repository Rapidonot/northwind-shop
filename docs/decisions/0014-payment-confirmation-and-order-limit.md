# 0014: The payment port requires a confirmation reference and has a per-order limit

Date: 2026-11-11 · Row: #21 (BE-05 + CHK-05) · Status: decided by the user (plan approval, 2026-11-11)

Plan and the user's answers: `docs/reviews/2026-11-11-BE-05-plan.md`. This settles FOUND-3's open medium 2 and
the question its port docstring left open: "on the port, or on a named layer above it".

## Decision

Hard rule 9 is enforced **on the `PaymentPort` contract**, so the PAY-01 contract suite checks it on every
adapter (the fakes now; the MCP and A2A routes in P4/P5).

- `authorize` and `capture` take a required `confirmation_ref`. The portal control issues it when the shopper
  confirms the order summary (CHK-02). Until CHK-02, a test fixture issues it, and nothing under `src` may call
  `issue` (AST-pinned).
- The rule (the user's words):
  - `authorize` uses the reference up;
  - `capture` must present the same reference that authorized this intent, and it is accepted although it is used;
  - any other reference is refused, a fresh unused one for the same order included;
  - after a void, a new authorization needs a new confirmation.
- `authorize` must be for exactly the confirmed order, currency and amount. `capture` is never more than the
  confirmed amount.
- `authorize` has a deterministic per-order limit per currency. A currency with no limit is refused. The
  placeholders, accepted by the user like the refund caps: USD 2,000, CAD 2,700, GBP 1,600, EUR 2,100. They are
  above every generated order's live-price total.
- The fakes' state (intents, assessments, shipments, the idempotency store and its lock, effects, caps, limits,
  the confirmation book) lives on a shared `*State`. So the one-payment rule, the refund cap, idempotency and
  confirmation use hold across instances. Scopes stay per instance: they belong to a credential.

## Why

A limit and a confirmation on a layer above the port would be skipped by any caller that reaches the port
directly, and the contract suite could not prove them per adapter. Agents get only an audited view, and their
handlers take the reference from the task, never from the model, so the model cannot supply, change or see it.

## What does not change yet

- `void`, `refund` and `status` take no reference. Void releases money; refund keeps its cap and gets PAY-06's
  approval hook later.
- The real portal control (CHK-02) and per-session and per-day limits (CHK-03) are later rows.

## Addendum: the payment claim (the user's Option 1, 2026-11-11, stop 6)

Security review F1 found that a concurrent duplicate of one order could authorize, or capture, while another run
escalated the order, and the stored escalation would not mention it. The user chose a claim on the order store:

- **The claim.** `OrderStorePort.claim_payment` is a compare-and-set from `pending` to `paying`, under the store's
  lock. It records the owning run's code-only identity (`run_` and 20 letters, never in model input; the audit writes
  it as type and length). It is never resolved through the idempotency store: duplicates share the order-ID keys, so
  a replay would hand the claim to both.
- **Who may pay.** The run claims inside `authorize_payment`'s code before the payment call; there is no new model
  tool. A run that doesn't own the claim never authorizes and escalates as `duplicate_in_progress`. So does a rerun
  that finds a crashed owner's claim, fail closed.
- **What an escalation shows.** The owner reports its intent on the claim after authorize and after capture. An
  escalation of a claimed order stores whichever view of the payment shows more money moved: the escalating run's,
  or the claim's as the owner last reported it, compared under the store's lock. Escalation is allowed from `paying`.
- **Known limits.** A concurrent duplicate escalates the order, with the money held and reported (owner #24: per-order
  serialization or a claim lease). If both views are stale (a failed report and a crashed owner), the stored view can
  understate the money, so the recommended action sends the reader to the payment port or the reconstruction.

