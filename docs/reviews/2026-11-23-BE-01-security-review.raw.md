# Security review: row #24, BE-01 (orchestrator)

I found three medium and five low findings, and nothing critical or high. One medium (M1) breaks hard rule 2 by my reading, so I count it as unsafe. The prompt escaping, the kind and ID allow-lists, the card-data refusal, audit by reference and the money-name pin all hold up.

**Scope.** I reviewed `/tmp/claude-502/northwind-review-BE-01-security.diff`: base `85aae52`, head is the uncommitted working tree, `docs/reviews` excluded. I re-read CLAUDE.md "Hard rules" and decision 0001 for this review. For context I also read these files on disk:
- `/Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/agents/runtime.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/audit.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/agents/orders_check.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/recovery.py`
- `/Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py`
- `/Users/rapido/Projects/northwind-shop-b/services/backend/tests/backend_audit/test_be08_views.py`
- `/Users/rapido/Projects/northwind-shop-b/.venv/lib/python3.12/site-packages/claude_agent_sdk/_internal/client.py`
- the plan, `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-23-BE-01-plan.md`

I am read-only, and I ran no tests. Every claim below comes from reading the code.

Nothing in the diff, fixtures or tests tried to steer this review. The planted strings, such as FlakySink's `sink-secret-text 4111…`, are test data and I treated them that way.

## Findings

### M1: Medium, unsafe (hard rule 2). The model pairs a payment intent with an order, and nothing checks the pair
- **Where:**
  - `services/backend/src/northwind_backend/orchestrator/plan.py:193` (`check_plan`). It accepts any `intent_id` written anywhere in the request, paired with any `order_id`.
  - `services/backend/src/northwind_backend/agents/orders_check.py:191-203` (`get_payment_status`). It never compares `intent.order_id` with `task.order_id`.
  - `services/backend/src/northwind_backend/orchestrator/aggregate.py:116` and `:149`. They print the payment line under "payment and fraud for order <concern.order_id>" without checking `value["order_id"]`.
- **What is wrong:**
  - In #20, trusted code built `OrdersCheckTask`, and by PAY-08 the intent comes from the task, with no lookup by order.
  - In #24, Sonnet builds the task from free text. The verbatim check proves only that the ID appears somewhere in the request, not that it belongs to that order.
  - So which payment is reported as an order's state now comes from the model, not from a live tool.
- **How it fails:** Take the request "Was O000123 charged? Also check O000124, payment pi_000042", where pi_000042 belongs to O000124. Or take pasted customer text that carries an injected `pi_000777`.
  - The planner emits `{"kind":"payment_fraud","order_id":"O000123","intent_id":"pi_000042"}`, and every check passes.
  - The fake's `status()` (`fakes/payment.py:302`) returns any intent.
  - The answer reads "1. payment and fraud for order O000123 [complete] – payment pi_000042 is captured (…)". Staff see another order's charge as this order's.
- **Suggested fix:** Bind the intent to the order before the model or the answer sees it.
  - In `get_payment_status`, when `Ok(intent)` has `intent.order_id != task.order_id`, return a fixed-text `permission` or `business` ToolError, for example `intent_not_for_order`. Record that as the step's result, with no value passed to the model.
  - As defence in depth, `aggregate._payment_line` should also refuse a value whose `order_id` differs from the concern's.
  - Add a scripted test with two orders and their intents swapped.

### M2: Medium (BE-02/BE-08 propagation; no silent success). The planner's run failures are dropped whenever a plan exists
- **Where:** `services/backend/src/northwind_backend/orchestrator/run.py:222-224`. `plan.failures` is used only when `concerns_of(plan)` is empty.
- **What is wrong:** These failures disappear from the `OrchestratorResult` and from the `orchestrator.run` audit outcome (`"failures": []`):
  - `audit_incomplete_run`: the planner's `agent.tool_use` or its `agent.run` outcome was not written;
  - `max_turns`;
  - `model_unavailable` or `replay_miss` after a successful `submit_plan`.

  The run can report `complete`.
- **How it fails:** A sink refuses the planner's `agent.tool_use` event (`runtime.py:247-252` sets `audit_lost`), but `submit_plan` had already recorded an OK plan. The planner's `AgentResult` is PARTIAL with `audit_incomplete_run`. The orchestrator ignores that, runs the subagents, and returns "Status: complete (2 of 2 concerns done)". The audit trail has a gap that the result and the run outcome both deny.
- **Suggested fix:** When `planned` is non-empty, still carry `plan.failures` into `failures`, at least `audit_incomplete_run`, `max_turns`, `model_unavailable` and `replay_miss`. `aggregate()` then turns `complete` into `partial`, and the run outcome lists the codes. Add a test with a `FlakySink` that fails `agent.tool_use` for actor `orchestrator-planner`.

### M3: Medium (hard rule 6 residual; BE-01 "annotated, never dropped"). Code cannot detect a concern the planner drops
- **Where:** `services/backend/src/northwind_backend/orchestrator/plan.py:193` (`check_plan`).
- **What is wrong:** Code checks that every concern is well formed and that its IDs appear in the request. It never checks the reverse: that every ID in the request is covered by some concern. A model error, or injected text such as "ignore the second question", can silently omit a concern. `unsupported` keeps refund requests visible only when the model chooses to emit them.
- **How it fails:** The request is "Was O000123 charged? Where is O000124's parcel? Refund O000124." The planner submits only `payment_fraud O000123`. The result is "Status: complete (1 of 1 concerns done)". Nothing shows that O000124's tracking or its refund request was asked.
- **Suggested fix:** Add a code-side coverage check. Find every `O\d{6}` and `pi_\d{6}` in the request, using the same ASCII boundaries as `_in_request`. Each one must appear in at least one concern, where `unsupported` with `topic="other"` counts. Otherwise refuse with a fixed `invalid_plan` reason such as `uncovered_id`, and add a unit test. The model cannot produce this property, so code has to enforce it.

### L1: Low (BE-08 audit completeness). A deadline hit by `wait_for` leaves the subagent's `agent.run` attempt with no outcome
- **Where:**
  - `services/backend/src/northwind_backend/orchestrator/run.py:128` (`asyncio.wait_for`);
  - `services/backend/src/northwind_backend/agents/runtime.py:258-264`, which catches only `ClaudeSDKError` and `Exception`. `CancelledError` is a `BaseException`, so the outcome write at `:289-296` is skipped.
- **What is wrong:** When the orchestrator's deadline fires mid-run, the child context `<run>.tN` keeps a dangling `agent.run` attempt. `orchestrator.task` does record `failures:["deadline"]`, so the run level is not silent. But the child's attempt/outcome pairing breaks, and `reconstruct` only flags unmatched attempts for state-changing actions.
- **Subprocess:** I see no orphan. The SDK's `process_query` closes the query in a `finally` on cancellation (`claude_agent_sdk/_internal/client.py:55-71`, `:209-211`). Port calls in flight run in `to_thread` and write their own outcome (`recovery.py:88-97`), but that outcome can land after the `orchestrator.run` outcome.
- **Unconfirmed:**
  - whether `query.close()` can block past the deadline, since `wait_for` in 3.12 waits for the cancelled task to finish;
  - whether a late subagent event after the run outcome affects `reconstruct_order`.
- **Tests:** No test drives a real `run_subagent` past the orchestrator deadline. `test_a_run_past_its_deadline…` uses stub runners, and the HANG test hits the read's own deadline.
- **Suggested fix:** In `run_subagent`, catch `asyncio.CancelledError`, write an `agent.run` outcome (`ok: False`, `status: failed`, `failures: ["cancelled"]`), then re-raise. Add a scripted test with a slow reply and a short `deadline_seconds`. Do this before BE-06 (#28) puts refunds under this same `wait_for`.

### L2: Low (decision 0001 spend, bounded). The length limit is checked before NFKC, which can expand text up to 18 times
- **Where:** `services/backend/src/northwind_backend/orchestrator/plan.py:152` checks `len(request)` on the raw text, and `:220` normalises it afterwards.
- **How it fails:** 2,000 copies of U+FDFA become about 36,000 characters in Sonnet's prompt, sent up to `MAX_TURNS = 3` times. The proxy's budget cap still bounds the spend, so no cap is breached. Only the stated limit is not the real one.
- **Suggested fix:** Check the length of the normalised text too, and refuse if either is over the limit.

### L3: Low (documentation accuracy, hard rule 6). The threat-model row overstates the mitigation
- **Where:** `docs/governance/threat-model.md:168`.
- **What is wrong:** The row lists "another shopper's parcel" as mitigated. The verbatim check stops only invented IDs. An ID written anywhere in the request, including pasted customer text, can still be chosen, and staff are not authenticated. The effect is read-only, but the row should say this residual plainly. M3's coverage check narrows it.
- **Suggested fix:** Reword the row to "invented IDs", and state the residual of steering among IDs present in the request.

### L4: Low (hard rule 8 records; plan conformance)
- PRD §7 "Tool scoping by agent" has no Orchestrator or planner row. Hard rule 8 says each agent gets only the tools in that table. `submit_plan` is a pure output channel with no data access, but it is recorded only in `docs/governance/tool-register.md`. Suggested fix: add the row through a decision, which is the user's call because it changes the PRD.
- The approved plan says "Turn cap 2", but `plan.py:42` sets `MAX_TURNS = 3`. The plan names `escalate_to_staff`, but the code uses `escalate_to_care` (`errors.py:120`). Neither is a budget cap, but the gate report should list both as deviations from the approved plan.

### L5: Low, future (hard rule 6; unconfirmed until P5)
- **Where:** `services/backend/src/northwind_backend/orchestrator/aggregate.py:138`, and `_payment_line` and `_fraud_line`. Also `services/backend/src/northwind_backend/agents/returns_track.py:94-99` (`_value`).
- **What is wrong:** Port-supplied strings go verbatim into the staff answer and into the subagent's tool result: carrier, scan `status` and `on`, payment `status`, fraud `reasons` and `decision`. With today's fakes they are code constants. When P5's A2A carrier adapter replaces the fake, they become external text.
- **Suggested fix:** When the adapter lands, check them against the audit's `_CODE` and date patterns.

## Checked, no findings
- **Hard rule 1: no finding.**
  - `request_problem` runs `card_data` on the raw request before any model call (`run.py:215-218`), and `contains_pan` and `contains_cvv` apply NFKC themselves (`cards.py:85`, `:93`).
  - The `orchestrator.run` attempt, written before that check, goes through `input_refs`, which yields `{"refused":"card_data"}` or type and length.
  - The failure carries only `reason: "card_data"`.
  - Subagent prompts carry pattern-checked IDs only.
- **Hard rule 2: finding M1.** All other state, meaning fraud, tracking and payment values, comes from live port calls. The answer is built from `StepResult.value`, never from model text.
- **Hard rule 3: no finding.** Only the sandbox test card 4111… and fixture IDs from `data/backend/` are used.
- **Hard rule 4: not applicable to this change.** No secrets, `.env` or key handling are touched.
- **Hard rule 5: not applicable to this change.** There are no product claims.
- **Hard rule 6: findings M3 and L3; the escaping is sound.**
  - `html.escape(quote=False)` runs after NFKC, so `<`, `>` and `&` are escaped, and fullwidth or small-form brackets are normalised first and then escaped. A test pins that a `</request>` in the request cannot close the block.
  - Kinds and topics are enums in both the schema and the code, unknown fields are refused, and IDs must fullmatch their pattern and appear verbatim with ASCII boundaries.
  - Only `payment_fraud` and `tracking` map to runners. Refusals are fixed text with no echo.
  - No path widens tools based on request content.
- **Hard rule 7: not applicable to this change.** There is no retrieval and no shopper caller.
- **Hard rule 8: no finding beyond L4.**
  - The planner's allowed tools are exactly `{submit_plan}`, Returns gets `{track_shipment}`, and Orders is unchanged. All are built via `agent_options(tools=[], dontAsk)`.
  - The AST money-name pin covers the new code. `ROOTS[0]` is `SRC = src/northwind_backend` walked with `rglob`, so it includes `orchestrator/**` and `agents/returns_track.py`.
  - No new string, attribute or import exactly matches `authorize`, `capture`, `void` or `refund`. `refund_request`, `captured` and `refunded` are not exact matches.
  - The view-constant pin covers `AuditedCarrierPort(allowed=CARRIER_READ_ONLY)`.
  - The `glob("orchestrator/src")` root in that test matches nothing, which is harmless.
- **Hard rule 9: not applicable to this change.** There is no state-changing or monetary tool, and `submit_plan` changes nothing.
- **Hard rule 10: no finding, given M2.**
  - `track_shipment` errors go through `model_envelope`, with `errorCategory` and `isRetryable`.
  - An empty scan list is `ok`, `empty`, with a note, so it is distinct from an error.
  - A tool never called gives `not_run`, `unsupported` gives `not_supported` with status FAILED, and an empty plan gives FAILED.
- **Hard rule 11: not applicable to this change.** The output goes to staff. The agent prompts still say "You are an AI system".
- **BE-08 audit by reference: no finding beyond M2 and L1.**
  - `run_inputs={"request": …}` is written as type and length.
  - The planner's `concerns` tool argument is written as type and length.
  - Labels (`kind`, `task`) are code values, and outcomes contain only enum values and codes.
  - The `not_supported` reason is the topic enum.
- **Determinism condition: no finding.**
  - Model-visible text holds only the escaped request, IDs, tool values and fixed envelopes.
  - No correlation ID, clock or deadline enters a prompt, a tool result or a tool error.
  - This is pinned by `test_the_same_request_twice_makes_the_same_model_input` and `test_no_run_id_time_or_deadline_reaches_a_model`.
- **Control surface (`scripted.py`): no finding.** With no routes, FIFO is unchanged, and a test pins it. The placeholder credential check still runs on every request whatever the route. It is a test double, not the proxy.
- **Child processes get a placeholder key only, and only the proxy holds the real key: no finding.** `llm.py` is unchanged, and every run goes through `agent_options()` and `agent_runtime()`.
- **Makefile does not export the key: not applicable to this change.** The Makefile is not in the diff.
- **No key in CI secrets, and CI runs in replay: not applicable to this change.** `.github/` is not in the diff.
- **Key file never copied into a worktree: not applicable to this change.**
- **Budget caps enforced and not raised: no finding.** No cap is changed. The planned recording uses `RUN_BUDGET=1`. See L2 for the bounded input amplification.
- **Cassettes still covered by the secret/PAN scanner: not applicable to this change.** No cassette is added or changed and the scanner is untouched. The future `cassettes/backend-orchestrator/` falls under the existing `cassettes/**` scan.
