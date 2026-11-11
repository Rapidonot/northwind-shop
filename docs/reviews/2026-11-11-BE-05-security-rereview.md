# BE-05 security re-review, scoped to `2e2e22f..f11f5e3` (verbatim)

## Scoped security re-review: row #21 (BE-05 + CHK-05), fix commit `2e2e22f..f11f5e3`

**Inputs**
- **Diff:** `/tmp/claude-502/northwind-security-BE-05-rereview.diff`. Its first two lines are base `2e2e22fca63543c0490a37349884b3bbeed0ae19` and head `f11f5e3550e69923d665998d3fe856fcf3a70051`, and they match the branch head.
- **Read first:** `CLAUDE.md` "Hard rules" and decision 0001.
- **Read in full at head:**
  - `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/agents/orders_place.py`
  - `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/fakes/orders.py`
  - `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/agents/messages.py`
  - `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/_replay.py`
  - `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_chk05_idempotent_placement.py`
- **Also read:** the relevant parts of `ports.py`, `recovery.py`, `fakes/_base.py` and `test_be05_place_scripted.py`.

**Limits and checks**
- I have no Bash, so no test was run. Everything below comes from reading the code.
- Steering: nothing in the diff tries to steer the review. The first-pass report committed under `docs/reviews/` is data.
- The fix commit touches no control-surface path: no `conftest.py`, cassette, Makefile, workflow or `evals/` file.

**Summary:** F1 is partly fixed: the sequential cases are closed and the concurrent residual is still open, which I rate **medium**. F2 to F5 are fixed. F6 was not changed, by design. The fix commit adds 4 lows. **Nothing is at medium or above except the F1 residual.**

---

## 1. Verdict per first-pass finding

### F1: PARTLY FIXED. Scenarios 1 to 3 are closed. The scenario-4 residual is open, now MEDIUM.

**What I checked:**
- `_store_gate` (`orders_place.py:390-405`) reads the order through the audited `ORDERS_PLACE.get`.
- It runs before every write:
  - address `:489`, fraud `:519`, authorize `:558`, capture `:603`, confirm `:642`, escalate `:689`
- It fails closed: a failed read returns the error, and no write follows.
- It refuses `ESCALATED` or `CONFIRMED` that this run did not produce.
- `OrderStatus` has only `PENDING`, `CONFIRMED` and `ESCALATED` (`ports.py:169-172`), so "not one of those two" means `PENDING`.

**Traced again on the new code:**
- **Scenario 1** (`not_finished`, then a rerun): run 2's `assess_fraud` is refused at the gate, and no money moves.
- **Scenario 2** (doubtful fraud write, then a rerun): the same result.
- **Scenario 3** (doubtful authorize, then a rerun): run 2's authorize is refused, so the earlier hold is never captured.
- **A placed order run again:** every write is refused with `order_already_placed`, and the outcome is `placed`.
- `finish()` escalates only when the store shows `PENDING` (`:893-901`).
- `RECOMMENDED["not_finished"]` no longer says rerun (`:227`).

**Tests:** `test_be05_security_fixes.py` covers every scenario above.

**Residual (scenario 4): MEDIUM.**
- **Where:**
  - `orders_place.py:390-405` together with `:603-624`: the gate read and the capture are separate calls.
  - `fakes/orders.py:181-188`: the first escalation's context is kept and is never added to.
- **The window is real today, not only once networked ports arrive.** `call_with_recovery` runs every port call through `asyncio.to_thread` (`recovery.py:88-92`). So between a gate read and the following write there is an event-loop yield and a worker-thread hop, even on the in-process fakes.
- **Hold variant, reachable in-process:**
  1. Runs A and B are duplicates of one order.
  2. A validates and assesses, then its model ends the turn.
  3. B validates, assesses (the result is replayed) and authorizes `:1`. The order is still `PENDING`, so B gets a fresh intent.
  4. A escalates (`agent_requested`, or `not_finished` from `finish()`). Its stored context says `payment: None`.
  5. B's capture is refused with `order_escalated`.
  6. The result is a live authorization hold that the stored escalation does not mention, and the agent has no void.
- **Capture variant:** the same race, with A's escalation landing between B's gate read and B's capture. The shopper is charged the confirmed amount, the order is `ESCALATED`, and the stored context says `payment: None`.

**Why medium and not high:**
- It needs concurrent duplicates of one order whose paths diverge: one escalates while the other carries on.
- For the capture variant, the escalation must also land inside one gate-to-write window.
- The money that moves is bounded:
  - there is one live intent per order;
  - the capture equals the amount the shopper confirmed;
  - no second charge and no over-limit charge is possible.
- The movement is still recorded in two places:
  - the audit chain, which `reconstruct_order` can rebuild;
  - the payment port's intent for the order.
- B's `PlacementResult.context` also reports it. However, nothing in `src` consumes `PlacementResult` yet, so the only durable record a person reads is the store's escalation context.

**What would raise it to high:**
- The real payment adapters (P4) land before the store transition does.
- Any caller runs placements of one order concurrently, for example a gateway retry on timeout while the first run is still live.

**Why the current tests cannot see it:**
- `test_ten_replays_at_once_yield_one_order` uses identical scripts, so the runs never diverge.
- The recorded 5×10 harness runs each order's runs in sequence (`_replay.py:98-103`).
- `assert_money` (`_replay.py:128-130`) checks only `captured_minor == 0`. It does not check for an authorized intent that the stored escalation context leaves out.

**Fix:** a design and pin change, so it goes to the user, as you said.
- Option 1: a conditional store transition, for example a claim from pending to paying under the run's key, which `escalate` respects or reports.
- Option 2: `escalate` appends a live intent to the stored context.

**Additions that need no design change:**
- Add a deterministic interleaving test of the hold variant, marked `xfail(strict=True)`, so the residual stays visible.
- Extend `assert_money` so that a non-placed order with a live intent fails unless `state.escalations[oid]["payment"]` reports it. Add it as xfail until the transition lands.
- Bring the module docstring at `orders_place.py:21-22` ("an escalated order moves no money") in line with the honest `_store_gate` docstring until the transition lands.

### F2: FIXED. Now: closed.

- The guard is `_escalate_doubtful()` (`:697-699`).
- `escalate_order` refuses with `STOPPED` before the gate (`:687-688`), and `finish()` skips the write (`:899`).
- The reason comes from the store (`:911`).
- The context is rebuilt by code whenever this run did not complete the escalation (`:913-917`). `context["reason"] == result.reason` holds, so `assert_context` is satisfied.
- `test_a_doubtful_escalation_is_never_written_again` pins one attempt and the store's reason.
- See N1 below for a side effect.

### F3: FIXED as reported. Now: closed. One adjacent variant is low and unconfirmed.

**The fix:**
- Capture (`:606-608`) and confirm (`:645-647`) now refuse unless `p.issues == ()`.
- `test_capture_follows_a_later_validation_that_found_an_issue` uses a private `CatalogState`, so the shared table is not edited.

**The adjacent variant:**
- A re-validation that changes only the price moves `p.total`. Capture still takes the authorized amount (`:613-624`), and confirm then refuses the mismatch (`:651-658`).
- The order ends escalated with money captured. The context does report the capture.
- This is unconfirmed because the fake prices are static.
- Fix: gate capture on `p.total` still equal to the authorized amount and currency, or refuse `validate_order` once `p.intent` is set.

### F4: FIXED for the reference. Now: closed.

`field(repr=False)` is on `OrderPlacementTask.confirmation_ref` (`orders_place.py:242`), `Confirmation.ref` (`confirmations.py:47`) and `Placement.task` (`:268`). The test pins both reprs. See N2 for the same class of leak through `Order`.

### F5: FIXED. Now: closed.

- `FORBIDDEN` gained `pi_\d{6}`, `fa_\d{6}`, `run-O\d{6}` and `checkout-\d`.
- `check_cassettes` also rejects every shopper `name` and `address.line1` (`_replay.py:51-66`, `:206-214`).
- Every change strengthens a check; none loosens one.
- The hidden-values test is parametrized over six paths: clean, stock-out, fraud flag, soft decline, address, and doubtful capture (`test_be05_place_scripted.py:260-303`).
- The `"pi_0"` and `"fa_0"` prefixes match the fakes' zero-padded IDs (`fakes/payment.py:166`, `fakes/fraud.py:75`).
- See N4 for one weakness in how the test checks its paths.

### F6: NOT CHANGED. Now: low, unchanged.

It stays with the user, because narrowing is a pin change.

### The two rewritten CHK-05 tests: not weakened

- **`test_two_interleaved_duplicates_use_the_same_keys_and_write_once`** (`test_chk05_idempotent_placement.py:131-162`):
  - It still pins exact equality of the key sequences.
  - The only key it leaves out is `order.confirm`, which run 2 is now refused by design.
  - It adds `assert_one_order`.
  - Interleaving is needed because a sequential rerun now writes nothing, and `test_a_rerun_of_a_placed_order_writes_nothing_and_reports_placed` covers that case.
- **`test_a_duplicate_message_with_a_fresh_confirmation_charges_nothing_more`** (`:165-182`):
  - It is stronger at the agent layer: no `payment.*` event at all from run 2, plus `order_already_placed`, plus `assert_one_order`.
  - It no longer exercises the payment port's `idempotency_conflict` on a fresh reference.
  - That defence is still tested in `/Users/rapido/Projects/northwind-shop/services/backend/tests/test_be05_prerequisites.py`. I confirmed this by grep only; I did not re-read that test's body.

---

## 2. New findings from the fix commit (all low)

### N1. LOW: a run can now end `failed` with no escalation and no context (hard rule 9 "no silent end"; BE-05 escalate-with-context)

**Where:** `orders_place.py:893-901` and `:911-917`.

**What is wrong:**
- `finish()` escalates only when the store read positively shows `PENDING`.
- It attaches context only when the outcome is `escalated`.

**Two ways to get there:**
- **(a)** The run's `escalate` write gets a `timeout` before the effect (the fake's `timeout_before_effect`, or `hang`). The order stays `PENDING`, `finish()` correctly does not write again, and the result is `failed` with `reason=None` and `context=None`.
- **(b)** The store read in `finish()` fails after retries. `before` is `None`, so no code escalation is even attempted, although the port's own `NOT_PENDING` / `ALREADY_ESCALATED` checks would make the attempt safe.

**Scenario:** capture succeeds, then confirm returns a non-doubtful error, then escalate times out before the effect. The result is `failed` with no context, while the money is captured on a `PENDING` order. Only `uncertain` lists the keys.

**Fix:**
- Attach `handlers.context()` to a `failed` outcome too.
- In `finish()`, skip the code escalation only when the store positively shows non-`PENDING` or the escalate was doubtful, not when the read failed.

### N2. LOW: `repr(Placement)` still prints the payment token and the postal code (NFR-02 hygiene; same class as F4)

**Where:**
- `orders_place.py:264-279`: `Placement.order`, `.confirmed` and `.escalated` hold an `Order`, and only `task` got `repr=False`.
- `ports.py:183-192`: `Order` keeps the default `repr`, which includes `payment_token` and `shipping_postal_code`, although the `Order` docstring says they never reach the audit.

**Scenario:** a pytest assertion failure that shows `handlers.placement`, or any later `log.debug(placement)`, prints a `tok_sandbox…` value and a postal code.

**Fix:** `field(repr=False)` on `Order.payment_token` and `Order.shipping_postal_code`. `ports.py` is outside this diff, so this is a follow-up, not a blocker.

### N3. LOW: the escalate tool's refusal tells the model to escalate (bounded turn waste)

**Where:** `orders_place.py:687-688`, which returns `STOPPED` (`:190-193`): "…Escalate the order."

**Scenario:**
- After a doubtful escalate, the model is first told "Do not call it again" (`messages.py:49-50`).
- If it calls `escalate_order` anyway, the `STOPPED` text tells it to escalate.
- That can loop until `MAX_TURNS`, which is 16.
- No write happens and every refusal is audited, but in a recording window each turn costs money and adds cassette size.

**Fix:** a dedicated refusal for `escalate_order` after doubt, such as "The escalation may already have taken effect. Stop here."

### N4. LOW: the six-path hidden-values test does not pin that each path reached the tool result it targets (F5 test strength)

**Where:** `test_be05_place_scripted.py:303`, which asserts only `result.outcome in ("placed", "escalated")`.

**Scenario:**
- A later gate change could make, say, the soft-decline or doubtful-capture path stop at an early refusal.
- The model would then never see the decline or doubtful tool result that the path exists to check, and the test would still pass.
- Shopper names and emails are also checked only in cassettes, not in this scripted model text.

**Fix:**
- Assert the expected outcome per path, and that the targeted tool's result was returned: for example the `soft_decline` code, or the doubtful-capture message.
- Add `SHOPPER_TEXT` and `@example.com` to the scripted check.

---

## 3. Checked, no findings (scoped to the fix commit)

**Hard rules**
- **Rule 1:** the new refusal codes `order_escalated` and `order_already_placed` carry fixed text, and the store's reason and context reach no tool result. The F5 checks are stricter. N2 is hygiene only. No finding.
- **Rule 2:** the F3 gate now follows the latest live validation. The price variant is folded into F3 (low, unconfirmed).
- **Rule 3:** test data only, sandbox values. No finding.
- **Rule 4:** no secret, no `.env`, no key handling. No finding.
- **Rule 5:** not applicable to this change.
- **Rule 6:** the gate's decisions come from store status, never from model or retrieved text. The escalation reason comes from the store or from `reason_for`, and model-supplied fields are still ignored. No finding.
- **Rule 7:** not applicable to this change.
- **Rule 8:** no new tool or view operation. The gate uses `ORDERS_PLACE.get`, which was already allowed. F6 is unchanged.
- **Rule 9:** sequential reruns and duplicates are closed. The concurrent residual is open (medium). N1 is low.
- **Rule 10:** the new refusals are `business` errors with codes. A failed gate read is returned as the port's error, not as a status. No finding.
- **Rule 11:** the system prompt is unchanged. No finding.

**Decision 0001 key handling**
- **Placeholder key in child processes, real key only in the proxy:** not applicable. No LLM client or `agent_options` change.
- **Makefile does not export the key:** not applicable. The Makefile is not in the diff.
- **No key in CI secrets, CI runs in replay:** not applicable. No workflow change.
- **Key file never copied into a worktree:** not applicable.
- **Budget caps enforced and not raised:** not applicable. `LLM_BUDGET_USD` and the runner budgets are untouched.
- **Cassettes still covered by the secret/PAN scanner:** `make scan-cassettes` is untouched. The row's own `check_cassettes` is strictly stronger (`_replay.py:206-214`). No cassette was added.
