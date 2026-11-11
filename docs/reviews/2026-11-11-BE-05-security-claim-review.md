# BE-05 security review of the payment-claim fix, `d6a5d7e..8ed9890` (verbatim)

## Scoped security review: row #21 (BE-05 + CHK-05), payment-claim fix `d6a5d7e..8ed9890`

**Inputs and limits**
- **Diff:** `/tmp/claude-502/northwind-security-BE-05-claim.diff`. Its first two lines are base `d6a5d7e5d7efe794586fdda44b5c34055dab661c` and head `8ed989075a78d63b395b906a7a7c1a1e706dc39f`, which match the branch head you gave.
- **Read first:** CLAUDE.md "Hard rules", decision 0001, and my re-review `docs/reviews/2026-11-11-BE-05-security-rereview.md`.
- **Read in full at head:** `agents/orders_place.py`, `fakes/orders.py`, `fakes/_base.py`, `views.py`, `recovery.py`, `reconstruct.py`, `agents/messages.py`, and `ports.py` lines 160-296.
- **Also read:** the relevant parts of `audit.py`, `idempotency.py`, `northwind_common/cards.py`, `tests/orders_place/_replay.py` and the changed tests.
- **No test was run.** I have no Bash, so everything below comes from reading the code.
- **Steering:** nothing in the diff tries to steer the review.

**Summary:** the claim works as a compare-and-set, and two runs can never both authorize. But **two findings are at medium**:
- **C1:** the stored escalation can report the wrong amount of money. Once a claim exists, the store overwrites the run's own payment context with its own copy, and that copy can be out of date.
- **C2:** `finish()` never escalates an order that another run's claim holds in `paying`. If that owner crashed, the order stays `paying` with nobody escalating it, which breaks your rule 2.

The rest are lows. The F1 scenario-4 residual as the re-review described it (a hold never mentioned) is closed when every claim write succeeds. C1 is what is left of it.

---

## 1. Findings, most severe first

### C1. MEDIUM: the stored escalation can show a captured payment as "authorized, captured 0", and labels the owner's own claim "another_run" (hard rule 9; F1 / your rule 3)

**Where**
- `fakes/orders.py:203-214`: the store fills in its own copy of the payment (its "claim view") only when the caller's `payment["intent_id"]` differs from `order.intent_id`. When it does, it **replaces** the caller's `payment`, and the label defaults to `"another_run"`.
- `orders_place.py:786-788` with `:852-858`: when the run's own steps have values, `handoff()` builds `payment` from `_money(intent)`. `_money` has **no `intent_id`** and no claim label, so the comparison above always differs.
- `orders_place.py:413-423`: `_report_on_claim` ignores its result, so the store's view stays stale whenever a report write fails.
- `orders_place.py:763-775`: `escalate()` reads the order and then writes the escalation as two separate calls through `to_thread`.

**Failure 1 (one run, one fault):**
1. The owner authorizes and the report succeeds, so the store holds `intent_id` with status authorized and 0 captured.
2. The owner captures successfully.
3. The capture report gets `timeout_before_effect`. The order is now uncertain, and the store still shows authorized and 0 captured.
4. `confirm_order` is refused with `STOPPED`, so the model escalates. The run-built context correctly says captured N, but it has no `intent_id`.
5. The store sees `None != pi_…` and overwrites the stored `payment` with `{status: "authorized", captured_minor: 0, claim: "another_run"}`.
6. Result: the shopper is charged, and the durable escalation says nothing was captured and that another run holds the claim. Before this diff, the run's context was kept as written.

**Failure 2 (concurrent, no fault):**
1. Duplicate B gets `claimed_by_another_run` and calls `escalate("code")`.
2. B's `orders.get` (`:763`) sees A's authorize report.
3. A then captures, and A's report lands while the order is still `paying`, so `_report` does not touch the escalations.
4. B's escalate write lands. The intent IDs are equal, so nothing is filled in, and B's stale `{status: "authorized", captured_minor: 0}` is stored.
5. A's confirm is then refused, so the order ends escalated with money captured and the stored escalation says 0 captured.

`assert_at_most_one_of_everything` (`test_chk05_idempotent_placement.py:101`) asserts that `captured_minor` matches, so the ten-at-once test may hit this intermittently. That is unconfirmed: I could not run it.

**Without any fault:** every owner escalation after a successful authorize (a declined capture, or F3's price-variant refusal) is stored with `claim: "another_run"`. That points the person at a run that doesn't exist. `_report` (`fakes/orders.py:281`) also always writes `"another_run"`.

**Fix (none of these loosens a check):**
- In `handoff()`, add `intent_id` and `claim: "this_run"` to the run-built `payment` when the run holds the claim.
- In the store's `escalate` effect, under the lock, compare the whole view: `intent_id`, `status` and `captured_minor`, not the intent ID alone.
- Never replace a caller's payment that shows more money moved than the store's view. Keep both: for example, keep the caller's `payment` and store the claim view as `claim_payment`, or merge them taking the further-along state.
- Fill in the claim view whenever `order.claimed_by` is set and the caller's `payment` is `None`, even before an intent exists. Today that also needs `intent_id`, so a stored escalation can omit the claim entirely if the escalator's read failed and the owner's report later fails (a double fault).
- Add a deterministic test for each of the two failures above.

### C2. MEDIUM: `finish()` treats "paying, claimed by another run" as closed, so a crashed owner's order is never escalated by a rerun that stops short (hard rule 9 "no silent end"; your rule 2)

**Where:** `orders_place.py:961-964`.

**Failure:**
1. Run A claims, authorizes (or also captures) and crashes, so `finish()` never runs.
2. Rerun C validates, and then its model ends the turn before `authorize_payment`. That is the `not_finished` path, or C's own escalate write failed without being doubtful.
3. In `finish()`, `before.status is PAYING` and `claimed_by != C`, so `closed` is true and no escalation is written.
4. The order stays `paying` with a live hold, or a capture, and no stored escalation. C's outcome is `failed`, and its context exists only in `PlacementResult`, which nothing consumes yet.

This is a regression: before this diff, the order was `PENDING` and `finish()` escalated with `not_finished`. The module docstring (`:27-29`) still claims that no run ends silently. `test_a_rerun_after_a_crash_finds_the_claim_and_escalates` drives the rerun through authorize, so it cannot see this path.

**Fix:**
- In `finish()`, do not count "paying, claimed by another run" as closed. Escalate it, with the reason `duplicate_in_progress`. That matches the authorize path and your fail-closed rule. A live duplicate that escalates is already the accepted cost (the PROGRESS low owned by #24).
- Add a crash-then-short-rerun test.

### C3. LOW: `order.claim_payment` is not in reconstruction's `STATE_CHANGING`, so a doubtful claim or report never shows as uncertain (BE-08; your rule 2, "reconstruction settles it")

**Where:** `reconstruct.py:32-45`. Only `ORDER_ACTIONS` gained the claim (`:52`).

**Failure:** a claim or report write with a `timeout` or no outcome is never listed in `OrderRecord.uncertain`. So the audit-only record cannot show that the order may be claimed, or that the store's payment view may be stale (see C1). The claim's inputs have no `idempotency_key`, so each such call would be grouped by its action ID, as reconstruction already does for unreadable keys.

**Fix:** add `"order.claim_payment"` to `STATE_CHANGING`. That strengthens the check. If the replay's `record.uncertain == ()` then fails, that is a real doubtful write.

### C4. LOW: the `DUPLICATE` refusal tells the model "it has been escalated" whether or not the escalation was written (hard rule 9 hygiene)

**Where:** `orders_place.py:202-205` and `:615-618`.

**Failure:**
1. `escalate("code")` returns `service_unavailable` three times, and the model is still told the order has been escalated.
2. With C2 unfixed, `finish()` also skips the order, so the outcome is `failed` with no escalation.
3. Today this is benign only because the claim owner is alive.

**Fix:** use the `DUPLICATE` text only when the escalation is `Ok`. Otherwise return the escalation's own error, so the model escalates and the doubt is visible.

### C5. LOW: the rewritten interleaved test does not check that the runs used the same keys (CHK-05 test strength)

**Where:** `test_chk05_idempotent_placement.py:177-180`.

**What is wrong:**
- `keys = {cid: [...]}` keeps only the last event per correlation ID, and `assert keys` only proves the dict is not empty.
- The test's name and docstring say "the same keys". The old cross-run exact-key equality is now pinned nowhere: I grepped `tests/`.
- The claim assertions themselves are sound: exactly one `claimed`, one authorize, no `payment.authorize` from run 2, `duplicate_in_progress`, the stored intent equal to the authorize reference, and no capture. With the same task, the keys are the same by construction, so your rule 4 is met.

**Fix:** assert that run 2's attempt keys for its shared steps (`fraud.assess`, and `order.escalate` where run 1 has one) equal run 1's.

### C6. LOW: the two at-once tests now accept a `failed` ending, and stopped pinning outcomes the claim cannot change (test strength)

**Where:**
- `test_chk05_criteria.py:92-100`
- `test_chk05_idempotent_placement.py:162-163`

**What is wrong:**
- `len(outcomes) == 1` passes when every run ends `failed` with no escalation and no money.
- For `stock_out` and `fraud_flag`, which never reach authorize, the at-once test no longer asserts `OUTCOME[kind]` or `escalate == 1`. Those expectations did not need to change.
- The criteria test's escalated branch checks only `payment is not None`, not the intent or the captured amount.
- The money caps are kept (at most one of each, exactly one when placed), so the change matches your fail-closed rule.

**Fix:**
- Assert `outcomes <= {"placed", "escalated"}`.
- Keep `== {OUTCOME[kind]}` and `assert_one_order` for kinds that don't pay.
- Reuse `assert_at_most_one_of_everything` in the criteria test.

### C7. LOW: stale docs (hygiene)

**Where:**
- `orders_place.py:21-26`: the module docstring still describes the concurrent race as open, with a strict xfail.
- `orders_place.py:429-430`: the `_store_gate` docstring says it "needs a conditional store transition".

**Fix:** describe the claim instead, and the C1 and C2 limits until they are fixed.

---

## 2. Your specific questions

- **Can two runs both authorize?**
  - No. `claim_payment` runs inside `_store.exclusive` (`fakes/orders.py:250`), the same `RLock` as `run()` (`idempotency.py:45-61`).
  - `_claim` compares the owner (`:253-261`). Only the owner gets `Ok`, a second time included, which the soft-decline retry needs.
  - Every authorize sits behind the claim (`orders_place.py:606-619`).
  - `test_concurrent_claims_from_ten_runs_give_exactly_one_owner` and the interleaved test pin this.
- **Is the claim outside the idempotency store?**
  - Yes. No key is passed to the store, `exclusive` writes no entry, and the view's audit inputs have no `idempotency_key`.
  - `claim_key` is used only for this run's own `uncertain` list.
- **Can money move on an escalated order without the stored escalation reporting it?**
  - A hold, or a capture landing between a claim and a duplicate's escalation, is reported: by the store's fill-in, or by `_report` on an `ESCALATED` order (`fakes/orders.py:278-282`).
  - It can be **misreported** (C1). It can also go unreported if a report write fails inside that window (C1, double fault).
- **Does an owner's crash fail closed?**
  - Yes, if the rerun reaches authorize.
  - No, if the rerun stops before authorize (C2).
- **Hard rule 1:**
  - `run_id` is `"run_"` plus 20 ASCII letters. It has no digits, so neither the PAN, long-digit nor CVV screen can match it (`cards.py:29-40` needs digits).
  - It is in no tool result, the prompt or any error text: `CLAIMED` and `BAD_RUN` carry fixed messages.
  - The audit writes it as `{type, length}`, because it is not in `_ID_PATTERNS` (`audit.py:60-79, 130-148`).
  - `claimed_by` is in neither `_ORDER_FIELDS` nor the `Order` repr (`ports.py:203`), and `claim_payment_view` and the handoff carry only `"this_run"` or `"another_run"`.
  - So nothing new can reach a cassette.
- **Hard rule 10:**
  - Every claim result is a `ToolError` with a category: `claimed_by_another_run` (business), `invalid_run_id` and `invalid_intent`/`unknown_order` (validation), `order_not_pending`/`already_*` (business), outage and timeout (transient), card data (validation).
  - A failed claim returns that envelope to the model and is never treated as an empty result.
- **No model change (your rule 4):**
  - No new tool, schema, description or system-prompt change, and authorize and capture return the same `{"status"}`.
  - The only new model text is the fixed `duplicate_in_progress` refusal, and a claim's port error on a fault path. Neither is on a path the pre-claim recordings took when runs ran in sequence. I did not open the 3 `backend-audit` cassettes, so that part is unconfirmed.
  - The test you asked for exists (C5 notes its key assertion is vacuous).
- **Pins and F6:**
  - `ORDERS_PLACE` gained `claim_payment` (approved).
  - `PAYMENT_PLACE == {authorize, capture}` and `FRAUD_PLACE == {assess}` match the only calls in `orders_place.py` (`self.payment.authorize`/`.capture`, `self.fraud.assess`), pinned exactly in `test_be08_views.py:191-198`.
  - No backend `src` file reads `.context` (I grepped).
- **`test_a_concurrent_duplicates_hold_is_reported_in_the_escalation`:** the strict xfail is removed and its assertions are byte-identical. From the code, A's escalation fills in B's reported intent, so it should pass. I did not run it.

---

## 3. Checked, no findings

**Hard rules**
- **Rule 1:** no finding. See section 2.
- **Rule 2:** not applicable to this change. Pricing and validation are untouched, and the claim view carries authorized amounts, not catalog prices.
- **Rule 3:** synthetic IDs only. No finding.
- **Rule 4:** no secret, key or `.env` handling. The run identity comes from `secrets`, and it is a random label, not a credential. No finding.
- **Rule 5:** not applicable to this change.
- **Rule 6:** claim decisions come from store state and code identity only, never from model or retrieved text. Model-supplied fields are still ignored. No finding.
- **Rule 7:** not applicable to this change.
- **Rule 8:** no new model tool. The one new view operation is the approved pin, and F6 is narrowed as asked. No finding.
- **Rule 9:** C1 and C2 (medium), C4 (low). The claim, its owner check and its idempotency-free design are sound.
- **Rule 10:** no finding.
- **Rule 11:** the system prompt is unchanged. No finding.

**Decision 0001 key handling**
- **Child processes get only the placeholder key, and only the proxy holds the real key:** not applicable. No change to the LLM client or `agent_options`.
- **Makefile does not export the key:** not applicable. The Makefile is not in the diff.
- **No key in CI secrets, CI runs in replay:** not applicable. No workflow change.
- **Key file never copied into a worktree:** not applicable.
- **Budget caps enforced and not raised:** not applicable. No budget setting was touched.
- **Cassettes still covered by the secret/PAN scanner:** no cassette is in this diff (`821007c` is out of scope as you stated), and the scanner and `check_cassettes` are untouched. No finding.

**Files cited**
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/agents/orders_place.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/fakes/orders.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/reconstruct.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/ports.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/views.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_chk05_idempotent_placement.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_chk05_criteria.py`
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/test_be05_security_fixes.py`
