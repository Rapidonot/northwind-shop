# BE-05 scoped re-review of the claim-fix commit, `8ed9890..6653c32` (verbatim)

## Scoped re-review: row #21 (BE-05 + CHK-05), claim-fix commit `8ed9890..6653c32`

**Nothing is at medium or above.** All seven findings, C1 to C7, are fixed. This commit adds four new lows (N1 to N4).

**What I read**
- The diff `/tmp/claude-502/northwind-security-BE-05-claim-rereview.diff`. Its first two lines are base `8ed989075a78…` and head `6653c329a0d4…`, which match the branch head.
- First, as required: CLAUDE.md "Hard rules", decision 0001, and my earlier review `docs/reviews/2026-11-11-BE-05-security-claim-review.md`.
- In full at head: `agents/orders_place.py`, `fakes/orders.py`, `reconstruct.py`, `agents/messages.py`, `ports.py:150-259`, `test_chk05_idempotent_placement.py`.
- The relevant parts of: `idempotency.py`, `fakes/_base.py`, `fakes/payment.py`, `views.py`, `agents/runtime.py`, and the helpers in `test_be05_security_fixes.py`.
- I have no Bash, so I ran no tests. You say all 5 regression tests fail against `8ed9890`. That is unconfirmed, but reading the old code, each one should fail there.
- Nothing in the diff tries to steer the review.

---

### 1. C1 to C7

**C1: fixed. Now: none open, with one low residual (N1).**
- `_money` now carries `intent_id` (`orders_place.py:860-867`), and `handoff()` adds `claim: "this_run"` whenever the run's own authorize or capture value exists (`:793-796`).
- That label is always true. The only way to get an authorize value is a claim that returned `Ok` for this `run_id`. The claim owner can never change: `_claim` moves only from `pending`, and no code path goes back to `pending`.
- **Failure 1** (the capture report fails): the caller's view `(N, captured, intent)` outranks the store's view `(0, authorized, intent)`, so the capture is kept. This is pinned by `test_a_failed_capture_report_never_hides_the_capture_in_the_escalation`.
- **Failure 2** (the escalator read the order before the capture was reported): the store merges under its lock. `_commit` reaches `IdempotencyStore.run`, and `claim_payment` reaches `exclusive`, and both hold the same `RLock` (`idempotency.py:45-61`). So a capture reported earlier wins inside `escalate`, and a capture reported later wins inside `_report`. This is pinned at the store level by `test_a_stale_escalation_view_never_replaces_a_fresher_claim_view`.
- A claimed order with no intent yet, where the caller's view is `None`, now always stores the claim view (rank `(0,0,0)` beats `(-1,-1,-1)`). That closes the double fault in which the claim was omitted.

**C2: fixed. Now: none.**
- `finish()` (`orders_place.py:972-985`) counts only `confirmed` and `escalated` as closed. "Paying, claimed by another run" now adds `claimed_by_another_run`, so `reason_for` gives `duplicate_in_progress`, and the code escalates.
- This is pinned by `test_a_crashed_owners_order_is_escalated_by_a_rerun_that_stops_short`.
- `finish()` runs after the model loop, so this changes no model input.

**C3: fixed. Now: none.**
- `order.claim_payment` is in `STATE_CHANGING` (`reconstruct.py:44-45`). The claim's inputs have no `idempotency_key`, so each call is grouped alone by its action ID (`:259`). That is conservative: a later claim never settles an earlier doubtful one.
- This is pinned by `test_a_doubtful_claim_is_uncertain_in_the_reconstruction`.

**C4: fixed. Now: none.**
- The `DUPLICATE` text is returned only after an `Ok` escalation (`orders_place.py:619-623`). Otherwise the model gets the escalation's own envelope.
- I also checked the case where another run escalated first with a different reason under the same key. That gives `idempotency_conflict`, and it still ends correctly: the store gate refuses with `ESCALATED`, and `finish()` sees the order as closed.

**C5: fixed. Now: none.**
- Keys are now collected from `attempt` events per correlation ID. The test asserts `fraud.assess` for both runs, and that run 2's keys, minus `order.escalate`, are a subset of run 1's (`test_chk05_idempotent_placement.py:181-189`).

**C6: fixed. Now: none.**
- Both at-once tests now assert `outcomes <= {"placed", "escalated"}`.
- `stock_out` and `fraud_flag` assert `{"escalated"}` again, plus `assert_one_order`. `OUTCOME` marks only those two kinds `escalated`, and they never reach authorize, so the comment is accurate.
- The criteria test now calls `assert_at_most_one_of_everything`, which checks that the intent and the captured amount match.

**C7: fixed. Now: none.** The module and `_store_gate` docstrings describe the claim. The small stale bits left are in N4.

---

### 2. Your question: can `further_along` pick a view that shows less money moved than really did?

**Never less than the more advanced of the two views it is given.** The rank is `(captured_minor, stage, intent present)`, and the caller's view is kept on a tie. One intent goes `claimed → authorized → captured`, so the highest rank is always the latest of its inputs. Case by case:

- **Refunded or voided intents:** not reachable today. `PAYMENT_PLACE` is `{authorize, capture}`, so a claim owner only ever reports `authorized` or `captured`.
  - If they were ever reported, a refund keeps `captured_minor` and adds `refunded_minor` (`fakes/payment.py:279-292`). Stage 3 would then correctly beat `captured`. But neither view carries `refunded_minor` (N2).
  - `voided` ties with `authorized`, and the first argument wins. Where the stale view comes first, the error is in the safe direction: it shows a hold that has already been released.
- **Two different intent IDs:** not reachable today.
  - The owner is fixed for the order's life, and only the owner gets `Ok` from the claim, so only the owner authorizes.
  - Each run has at most one intent: a second authorize hits `ALREADY_DONE`, and a soft decline creates no intent.
  - `_report` requires `payment.order_id == order_id` and `claimed_by == run_id`.
  - `further_along` does not check that the IDs match, though, so a second live hold would be dropped silently (N2).
- **A caller's view that overstates the amount:** the caller is `handoff()`, built only from port results, never from the model. An overstated view would win, but that errs in the safe direction for a handoff to a person. The context is also not audited (`views.py:402-409`), and it is screened for card data before it is stored (`fakes/orders.py:187`).
- **Where it can understate:** only when both inputs are stale. That needs a double fault: a failed report plus an owner that never escalates. See N1.

---

### 3. New findings

#### N1. LOW: a double fault still stores "authorized, captured 0" for a captured order, and the recommended action points the reader at that field (hard rule 9; F1 residual)

**Where**
- `orders_place.py:797-799`: the claim view built from the store.
- `ports.py:231-248`.
- `orders_place.py:234-236`: `RECOMMENDED["duplicate_in_progress"]` says "check its authorization (in `payment`) first".
- `orders_place.py:27-28`: the docstring says "a hold or a capture is always reported".

**Scenario**
1. Owner A captures N.
2. A's capture report on the claim gets `timeout_before_effect`.
3. A crashes before it escalates.
4. Rerun C stops short. `finish()` escalates with `duplicate_in_progress`.
5. C's view and the store's view are both `{authorized, captured_minor: 0}`, so the stored escalation says nothing was captured, and the recommended action tells the person to read that field.

**Why it is low**
- It needs a fault plus a crash.
- A void on a captured intent would be refused by the payment port.
- The audit holds A's `payment.capture` `Ok` outcome, and since C3 it also lists the report as uncertain, so `reconstruct_order` shows the capture.

**Fix**
- Label a view that comes from the store as last reported by the owner, for example `"source": "claim_report"`, in both `claim_payment_view` and the store merge.
- Change `RECOMMENDED["duplicate_in_progress"]` to say: read the payment port's intent for the order, or the order's reconstruction (`payment_label`), before acting, never the `payment` field alone.
- Soften "always" in the docstring.

Both texts go to the store only, not to the model, so no cassette changes. The full fix is the PAY-08 lookup by order, which is already an open item.

#### N2. LOW: `further_along` assumes one intent per claim, and neither view carries `refunded_minor` (defence in depth before the P4 adapters)

**Where:** `ports.py:221-248`, `ports.py:209-218`, `orders_place.py:860-867`.

**Scenario** (not reachable on the current fakes):
- A real adapter, or a later agent, reports a second intent, or a refund, on a claimed order.
- The higher-ranked view replaces the other. Another intent's live hold disappears from the stored escalation, or a refund shows with no refunded amount.

**Fix**
- When both views have different non-null `intent_id`s, keep both, for example `{"payment": best, "other_payment_views": [...]}`, rather than choosing one.
- Carry `refunded_minor` in `_money` and `claim_payment_view`.
- Add a unit test for `further_along` covering mismatched IDs and refunded and voided views.

#### N3. LOW: the store defaults the claim label to `"another_run"` when the caller's view is `None`, though the store can't know who escalated (handoff accuracy)

**Where:** `fakes/orders.py:209` and `:279`.

**Scenario**
1. Owner A claims, and then authorize hard-declines, so there is no intent.
2. A escalates. Its `orders.get` in `escalate()` (`orders_place.py:768`) fails three times, so `handoff(None)` gives `payment: None`.
3. The store fills in `claim: "another_run"`, although A holds the claim.
4. A person may then look for a duplicate run that does not exist. This happens on a fault path only.

**Fix:** when the caller gave no label, store `"unreported"` or `"unknown"` instead of `"another_run"`.

#### N4. LOW: stale text (hygiene)

- **`orders_place.py:967-969`:** the comment says the code escalation is skipped when the store holds the order "as not pending". Since C2 the rule is "confirmed or escalated".
- **`orders_place.py:30-31`:** the module docstring names `not_finished` as the code escalation. A paying order claimed by another run is now escalated as `duplicate_in_progress`.
- **`PROGRESS.md:12`:** the row this commit edited contradicts itself.
  - It still says "1 strict xfail (the residual)", but the claim fix removed that xfail.
  - It says "Nothing recorded; spend $2.588401" next to "Window 1 recorded … $0.000331".
  - It says "`backend-audit` runs 3, 7, 10 miss until window 1 re-records them (planned)", although window 1 has already recorded.
  - These are evidence-accuracy problems for the gate report, not security problems.

**Fix:** update the two comments, and refresh the row's evidence after `make ci`.

Not a finding, just a note: step values now carry `intent_id` and amounts in `AgentFailure.partial_results`. Nothing reads those today; I grepped `src`. Hard rule 1 allows intent IDs, but when BE-01 (#24) puts subagent results into the orchestrator model's context, that model will see these values. The comment "never the model" at `orders_place.py:862` holds only until then.

---

### 4. Checked, no findings

**Hard rules**
- **Rule 1:** no finding. `intent_id` (`pi_` plus 6 digits) is allowed by the rule and cannot match the PAN screen. `run_id` was not added anywhere. The context is screened for card data before it is stored and is not audited. No new model-facing text: tool returns are still `{"status"}`, and the C4 path reuses the existing envelopes.
- **Rule 2:** not applicable to this change. The amounts come from port results and the store.
- **Rule 3:** no finding. The tests use synthetic IDs only.
- **Rule 4:** not applicable to this change.
- **Rule 5:** not applicable to this change.
- **Rule 6:** no finding. Labels and views are built by code from store state and port results, never from model text.
- **Rule 7:** not applicable to this change.
- **Rule 8:** no finding. No new tool, scope or view operation.
- **Rule 9:** C1 to C4 fixed. N1 and N3 are low. The claim's compare-and-set and the one-authorize rule are unchanged.
- **Rule 10:** no finding. The C4 path returns the escalation's structured error.
- **Rule 11:** not applicable to this change. The system prompt is unchanged.

**Decision 0001**
- **Child processes get only the placeholder key, and only the proxy holds the real key:** not applicable to this change.
- **The Makefile does not export the key:** not applicable. The Makefile is not in the diff.
- **No key in CI secrets, and CI runs in replay:** not applicable. No workflow changed.
- **The key file is never copied into a worktree:** not applicable to this change.
- **Budget caps enforced and not raised:** not applicable. No budget setting was touched.
- **Cassettes still covered by the secret/PAN scanner:** no cassette is in this diff, and the scanner is untouched. No model input changed (step values, `finish()` and the store merge are all after or outside the model loop), so no planned recording is invalidated.

**Files cited**
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/agents/orders_place.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/fakes/orders.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/ports.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/reconstruct.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/idempotency.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/fakes/payment.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_be05_security_fixes.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_chk05_idempotent_placement.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_chk05_criteria.py`
- `/Users/rapido/Projects/northwind-shop/PROGRESS.md`
