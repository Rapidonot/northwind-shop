# BE-05 security review, first pass (verbatim)

Saved verbatim from `security-reviewer`'s report on the diff at head `2e2e22f` (base `201c97a`).

## Security review: row #21 (BE-05 partial + CHK-05), branch `req/BE-05-orders`

- **Diff:** `/tmp/claude-502/northwind-security-BE-05.diff`, base `201c97a275e1068494034d96787e37c1de913f05`, head `2e2e22fca63543c0490a37349884b3bbeed0ae19`.
- **Read first:** `CLAUDE.md` "Hard rules", decision 0001, decision 0014, and the plan's "Approval" section and §3.
- **Read in full from the working tree (clean, at head):** `agents/orders_place.py`, `agents/messages.py`, `confirmations.py`, `fakes/payment.py`, `fakes/_base.py`, `fakes/orders.py`, `fakes/catalog.py`, `fakes/fraud.py`, `views.py`, `audit.py`, `recovery.py`. Also read: the `ports.py`, `errors.py`, `reconstruct.py` and `generate.py` hunks, and the placement and CHK-05 tests and harness.
- **Limits:** I have no Bash, so no test was run. Every finding comes from tracing the code.
- **Steering:** nothing in the diff tries to steer the review. `"ignore previous instructions"` in `test_be05_criteria.py:506` is adversarial test data, used correctly.

**Totals: critical 0, high 1, medium 0, low 5.**

---

## 1. Findings

### F1. HIGH: an order escalated by an earlier run can still be authorized and captured by a later run (hard rule 9; CHK-05; decision 0014)

**Where:**
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/agents/orders_place.py:413-457`: `validate_order` reads the order (line 422) but never looks at `order.status`.
- `orders_place.py:527-532` and `:569-576`: the "escalated, so stop" gates check only `p.escalated` and `p.doubtful`, which are state of the current run.
- `orders_place.py:223`: `RECOMMENDED["not_finished"]` says "rerun the placement or finish it by hand".
- `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/fakes/orders.py:136-139`: `confirm` refuses a non-pending order, but the capture has already happened by then.
- `fakes/orders.py:181-187`: the stored escalation context is never updated.

**What is wrong:** "Once the order is escalated, every later write is refused: an escalated order moves no money" (module docstring, lines 21-22) holds only inside one run. The order store's `ESCALATED` status is never checked before a money call. The idempotency keys are fixed per order, so an `:1` key that run 1 never used is a fresh key for run 2.

**Concrete failures (all traced through the code):**

1. **The model stops early, then the order is rerun.**
   - Run 1: the model calls `validate_order` and `assess_fraud`, then ends its turn or hits `MAX_TURNS`. `finish()` (lines 837-838) escalates with `not_finished`, and the recommended action is "rerun the placement".
   - Run 2: the same task, or a duplicate message with a fresh confirmation for the same amount.
     - `validate_order` passes, because status is ignored.
     - `assess_fraud` replays the stored `allow` under `O…:fraud.assess`.
     - `authorize :1` is a fresh key, the reference is unused, and an intent is authorized.
     - `capture` captures it.
     - `confirm` returns `order_not_pending`, `escalate` returns `already_escalated`, and the outcome is `escalated`.
   - **Result:** the shopper is charged, no order is confirmed, and the context the person reads (`state.escalations`, from run 1) says `payment: None`.
2. **A doubtful fraud write, then a rerun.** Run 1's `assess_fraud` gets a `timeout_after_effect` and escalates as `uncertain_write`. Run 2 runs as in scenario 1 and money moves.
3. **A doubtful authorize, then a rerun.** Run 1's `authorize` gets `timeout_after_effect`, escalates as `uncertain_write`, and a person is told to "settle the uncertain action". Run 2's `authorize :1` replays `Ok(intent)`, and `capture` then runs while the person is settling it.
4. **Concurrent duplicates** (CHK-05's "10 at once"). Run A escalates while run B is between authorize and capture. Run B captures.

**Why the tests miss it:**
- `test_after_an_escalation_no_money_moves` (`test_be05_criteria.py:528`) covers only a single run.
- `test_ten_duplicates_of_an_escalated_order…` (`test_chk05_criteria.py:67`) uses a `stock_out` order, where re-validation fails anyway.
- `_replay.py:103-115` (`assert_money`, `placed=False`) and `test_be05_replay_recorded.py:66-68` both pass when every run is `escalated` and one of them captured. So the recorded 5×10 check would not catch it either.

**Suggested fix:**
- Before `assess_fraud`, `authorize_payment` and `capture_payment`, re-read the order through `ORDERS_PLACE.get` and refuse with `ESCALATED` unless the status is `PENDING`. A `CONFIRMED` order would still need the idempotent replay path.
- Close the race in scenario 4 at the store: either a placement state that `escalate` must respect, or `escalate` recording a live intent so the context shows it.
- Change `RECOMMENDED["not_finished"]` so it does not say "rerun" until the fix is in.
- Add tests:
  - run 1 `["escalate_order"]`, then run 2 `HAPPY`, gives no payment effect;
  - `not_finished`, then `HAPPY`;
  - doubtful `assess_fraud`, then `HAPPY`.
- Make `assert_money` fail when a non-placed order has a captured intent that its escalation context does not report.

### F2. LOW: after a doubtful `escalate`, the escalate write is called again (#20 U2; hard rule 9's "never repeat after doubt")

**Where:** `orders_place.py:637-648` and `:650-670`, and `finish()` at `:837-838`.

**What is wrong:** `escalate_order` and `escalate()` do not check `p.doubtful`. After a `timeout_after_effect` on `order.escalate`, the model (against the tool text) or `finish()` calls it again under the same key. The reason has now become `uncertain_write`, so the request fingerprint differs and the call gets `idempotency_conflict`.

**How it fails:** the store keeps the first reason and context, but `PlacementResult.reason` and `PlacementResult.context` report the second, unsaved ones. No money moves.

**Fix:** treat a doubtful escalate like any other doubtful write. Do not call it again; report the outcome from the store (status and `escalation`), and take the context from `state.escalations`.

### F3. LOW (unconfirmed on the fakes): capture and confirm ignore a re-validation that found an issue (hard rule 2)

**Where:** `orders_place.py:573-576` and `:608-616`. Neither re-checks `p.issues == ()`.

**How it fails:** validate (clean) → fraud → authorize → `validate_order` again, which now reports `out_of_stock` from live stock → `capture` → `confirm` → `placed`. The fresh live stock reading is ignored.

**Why unconfirmed:** the fake's stock table is static, so this cannot happen today. It can once `mcp-catalog` serves live stock.

**Fix:** gate capture and confirm on `p.issues == ()`, or refuse `validate_order` once `p.intent` is set.

### F4. LOW: the dataclass `repr` exposes the confirmation reference (hard rule 9 reference handling; NFR-02 hygiene)

**Where:** `orders_place.py:232-238` (`OrderPlacementTask.confirmation_ref`) and `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/confirmations.py:45-49` (`Confirmation.ref`).

**How it fails:** both are frozen dataclasses with the default `repr`. Any later `log.info(task)`, an orchestrator trace, or pytest failure output would print the reference in clear. The audit is careful to write it only as type and length.

**Current state:** I found no such log line today.

**Fix:** `field(repr=False)` on both.

### F5. LOW: model-input and cassette checks are narrower than the plan and the data-flow row claim (hard rule 1 evidence; plan §F item 6)

**Where:**
- `/Users/rapido/Projects/northwind-shop/services/backend/tests/orders_place/_replay.py:45-51`: `FORBIDDEN` has no pattern for intent IDs (`pi_\d{6}`), assessment IDs (`fa_\d{6}`), correlation IDs (`run-O…`), shopper names or street lines. `data-flow.md` and the plan state that none of these reach the model. `shoppers.jsonl` holds names and emails.
- `test_be05_place_scripted.py:259-280`: `test_the_model_sees_no_amount_id_token_address_or_reference` runs on the happy path only. Escalation, decline and error tool results are not checked.

**How it fails:** a later change that put an intent ID or an amount into an escalation or error tool result would pass both checks and be recorded into roughly 8,000 cassettes.

**Fix (this strengthens the checks, it does not loosen them):**
- Add those patterns to `FORBIDDEN`, plus the real shopper names from `shoppers.jsonl`.
- Run the hidden-values test on an escalation path, a soft-decline path and a write-timeout path too.

### F6. LOW: the placement sets grant two reads the agent never uses (hard rule 8)

**Where:** `/Users/rapido/Projects/northwind-shop/services/backend/src/northwind_backend/views.py:52-53`.

**What is wrong:** `PAYMENT_PLACE` includes `status` and `FRAUD_PLACE` includes `read`. `orders_place.py` calls neither.

**Risk:** both are read-only, and the plan approved these sets explicitly. Least privilege would still drop them until a handler needs them.

**Fix:** narrow the sets and update the exact-contents pin in `test_be08_views.py:4744-4751`. Narrowing is a pin change, so it needs the user's approval like any other.

---

## 2. Checked, no findings

**Hard rules**

- **Rule 1:**
  - The model gets only `order_id` in, and issue codes, line count, fraud decision, score and reasons, and statuses out.
  - The token, amount and reference come from the order or the task (`orders_place.py:546-552`, `:583-587`).
  - The audit writes `payment_token` and `confirmation_ref` as type and length (`audit.py:123-148`; neither is in `_ID_PATTERNS`), and writes an `Order` by allow-listed fields only (`audit.py:90-101`, `:168-172`).
  - The escalation context holds no token, address or reference, and is card-screened before it is stored (`fakes/orders.py:173`).
  - The idempotency store keeps only a SHA-256 fingerprint of each request.
  - Exception text goes nowhere (`views.py:90-93`, `recovery.py:73-79`).
  - Only F4 and F5 apply, both low.
- **Rule 2:**
  - The total comes from `CatalogTableFake` over `prices.jsonl` and `stock.jsonl` (`fakes/catalog.py`), never from the index or the model.
  - A price change after the confirmation gives `confirmation_mismatch`, and no money moves.
  - Only F3 applies (low, unconfirmed).
- **Rule 3:** sandbox tokens only, a synthetic generator, and `replay_labels.jsonl` is rule-derived. No finding.
- **Rule 4:** no secrets, no `.env`, no key handling in this diff. Only F4 (hygiene) applies.
- **Rule 5:** not applicable to this change.
- **Rule 6:**
  - Every tool schema is `order_id` only.
  - The reason and context are built by code (`reason_for`, `context()`); model-supplied `reason`, `context` and `recommended_action` are ignored, and `test_be05_criteria.py:493-515` pins this.
  - Tool text is fixed (`messages.py`).
  - No tool result grants a capability.
  - No finding.
- **Rule 7:** not applicable to this change.
- **Rule 8:**
  - Seven tools, `allowed_tools` equal to `TOOL_NAMES`, no void, refund or carrier.
  - The AST pin admits `orders_place.py` for `authorize` and `capture` attributes only, with void, refund and `getattr` negative cases (`test_be08_views.py:4812-4850`).
  - Views are built only from the named constants.
  - Only F6 applies (low).
- **Rule 9:**
  - Within one run:
    - authorize needs a clean validation, a fraud `allow`, an unused reference bound to the order, currency and exact amount, and an amount under the per-currency limit;
    - a currency with no limit is refused;
    - there is one live intent per order across instances, and at most two attempts (`:1`, `:2`), the second only after a soft decline;
    - capture accepts only the authorizing reference and never more than the confirmed amount;
    - after a void, a new authorization needs a new reference;
    - a dry run uses nothing up;
    - `WRITE_POLICY` never repeats a `timeout`, `unexpected_error` or `audit_incomplete` write.
  - Every write key comes from the order ID alone.
  - Only issuing a reference is AST-pinned out of `src` (`test_be05_prerequisites.py:203-205`).
  - Gaps: F1 (across runs, high) and F2 (low).
- **Rule 10:** handler refusals are `business` errors with codes. A failed catalog read is returned as an error, not a validation issue (`orders_place.py:430-434`). An empty result and an access failure stay distinguishable. No finding.
- **Rule 11:** the system prompt states "You are an AI system". This is an internal agent, not shopper-facing. No finding.

**Decision 0001 key handling**

- **Placeholder key in child processes:** unchanged. `run_subagent` builds options only through `agent_options()` with `tools=[]` (`agents/runtime.py:232-235`), and no direct `anthropic` or `ClaudeAgentOptions` construction was added.
- **Makefile does not export the key:** not applicable to this change; the Makefile is not in the diff.
- **No key in CI secrets, CI runs in replay:** no workflow change. The new recorded test is `llm_suite("backend-replay")` plus `integration`, so CI replays it.
- **Key file never copied into a worktree:** not applicable to this change.
- **Budget caps enforced and not raised:** `LLM_BUDGET_USD` is untouched. The plan's `RUN_BUDGET=15` is a window budget the user opens, under the fixed $90 cap.
- **Cassettes still covered by the scanner:** `make scan-cassettes` scans the whole cassettes tree and is unchanged. The future `cassettes/backend-replay/` is covered. None were added in this diff.
