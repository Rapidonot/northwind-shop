# Scoped security re-review: row #24, BE-01 (orchestrator), after the fixes

The fixes for M1, M2 and L3 hold. L1 and L2 hold, with small gaps. M3 is only partly fixed: the coverage check can be bypassed by IDs written in fullwidth digits or with zero-width characters (N1, medium). The fixes introduced nothing critical, high or unsafe. M1 was the only unsafe finding before, and it is now closed.

**Scope.**
- I compared `/tmp/claude-502/northwind-review-BE-01-security-2.diff` with the first diff. Base is `85aae52`, head is the uncommitted working tree.
- I read these on disk: `orders_check.py`, `runtime.py`, `plan.py`, `run.py`, `aggregate.py`, `errors.py`, `messages.py`, the relevant parts of `audit.py`, `views.py`, `reconstruct.py` and `fakes/payment.py`, `test_be01_review_fixes.py`, and the SDK's `claude_agent_sdk/_internal/client.py:34-71` and `_internal/query.py:1178-1234`.
- I re-read CLAUDE.md "Hard rules" and decision 0001.
- I am read-only, and I ran no tests. Everything below comes from reading the code.
- Nothing I read tried to steer the review.

## Status of each fix

| Item | Verdict | Notes |
|---|---|---|
| M1 (unsafe) | **Confirmed fixed** | `orders_check.py:204-207`: a foreign intent's values never reach `value`, the tool result or `state`. The model gets the fixed text of `INTENT_NOT_FOR_ORDER`, and `isRetryable` is false. The answer shows `payment.status not done (intent_not_for_order)`. The defence in depth at `aggregate.py:162-167` is correct. A matching intent takes the same path as before, so #20's cassettes are unaffected. Residuals: N2, N3, N6 |
| M2 | **Confirmed fixed** | `run.py:225` always carries `plan.failures`. `aggregate.py:199-200` turns complete into partial, and `run.py:243` puts the codes into the `orchestrator.run` outcome. A good plan produces no step failure, so only run-level codes travel: `max_turns`, `replay_miss`, `model_unavailable`, `audit_incomplete_run`. Their `partial_results` carry only the plan's IDs and kinds |
| M3 | **Partly fixed** | Plain ASCII IDs are covered (`plan.py:232-235`), and refusals are fixed text. Bypass: N1. Residual not documented: N5. Side effect, which fails closed and visibly: a request that names more IDs than 4 concerns can cover, for example 5 order IDs, can never be planned and always ends `invalid_plan`. Pasted text can force that. It is a visible denial of service, not a silent one |
| L1 | **Confirmed, with notes** | See "Cancellation path" below and N4 |
| L2 | **Confirmed for NFKC** | `plan.py:159-161`. HTML escaping still expands the text after the check: N7 |
| L3 | **Confirmed** | `threat-model.md:168` now says "invented IDs" and names the residual. It still lists "drops a question" as mitigated: N5 |
| Records | **Accurate, with one caveat** | The tool-register rows for `get_payment_status`, `submit_plan` and `track_shipment` match the code. The `submit_plan` row's "none dropped" is subject to N1 |
| Tests | **Nothing removed** | Every `def test_` in the first diff is still in the second. Five new tests were added. The M1 test's name says "its values reach no one", but it asserts only on the model input and the answer, not the audit (N3). No test covers fullwidth or zero-width IDs (N1) |

### Cancellation path (L1), checked against the SDK
- The only `await` inside the `try` is the `async for` over `query()`. A cancellation can therefore only arrive inside the SDK generator, and it unwinds through `process_query`'s `finally: await inner.aclose()` (`client.py:58-71`) before it reaches the new handler. The generator is closed, not stranded.
- `Query.close()` runs inside `anyio.CancelScope(shield=True)` (`query.py:1210`). It waits for in-process tool calls up to a grace period of "a few seconds per server", and for transport close up to about 20 s (docstring, `query.py:1185-1208`).
- In Python 3.12, `wait_for` awaits the runner inside the same task, so the deadline is not a hard bound: the time to close is added to it. This confirms the item I left unconfirmed in my first review. It is not new, and it is not unsafe.
- The handler writes the outcome synchronously, with no `await`, and re-raises. A second cancellation therefore cannot interrupt it, and no outcome is written twice, because no `await` follows the `try`.
- A port read that is still blocked in `to_thread` writes its view outcome after the `agent.run` "cancelled" outcome. This is the late-event residual from my first review, and it is unchanged.

## Findings

### N1: Medium, not unsafe (BE-01 "annotated, never dropped", hard rule 6 residual). M3's coverage check runs on raw text, but the model sees the NFKC text
- **Where:**
  - `services/backend/src/northwind_backend/orchestrator/plan.py:233-234` (`ids_in(request)`) and `:211` (`_in_request`) run on the raw `request` that `_tools` captures (`:254`, passed in at `:320`).
  - The model reads `unicodedata.normalize("NFKC", request)` (`:247`).
- **What is wrong:**
  - Characters that NFKC folds to ASCII make an ID that the model sees but code does not count: fullwidth `Ｏ０００１２４`, `pi＿000042` with a fullwidth low line, mathematical or superscript digits.
  - The same happens with format characters that NFKC keeps, such as U+200B or U+200D inside `O000​124`, which the model may read as `O000124`.
  - Because `ids_in(raw)` misses the ID, coverage does not require it. Because `_in_request(raw)` misses it too, the model cannot cover it either (`id_not_in_request`).
- **How it fails:** Staff paste a customer note that says "Where is my parcel? Order Ｏ０００１２４", typed with a Japanese IME, beside their own "Was O000123 charged?".
  - The model sees O000124, which its system prompt tells it to cover. Covering it is refused with "An ID is not written in the request".
  - The model either gives up after `MAX_TURNS` (no plan, so the run fails, visibly) or drops it. If it drops it, the plan `[payment_fraud O000123]` passes every check, and the answer reads "Status: complete (1 of 1 concerns done)".
  - That is exactly M3's silent drop, for a realistic class of input. Whether the model drops or loops is model behaviour: **unconfirmed**.
- **Suggested fix:**
  - Build one text once: NFKC, then remove category-`Cf` characters. Use that same text for the model prompt, `ids_in`, `_in_request` and the length check. Then the IDs that code counts are the IDs the model sees.
  - Optionally, `request_problem` can also refuse a request whose ID set changes under that normalisation, with a fixed reason.
  - Add unit tests with fullwidth digits, a fullwidth `Ｏ`, a fullwidth low line and a ZWJ inside an ID.

### N2: Low, not unsafe. The refusal tells the model and the answer that a foreign intent exists
- **Where:** `services/backend/src/northwind_backend/agents/orders_check.py:48-50` and `:204-207`. Compare `fakes/payment.py:69`, `:312` (`unknown_intent`).
- **What is wrong:**
  - An intent that does not exist gives `unknown_intent` ("There is no such payment intent.").
  - An intent of another order gives `intent_not_for_order` ("…belongs to another order…").
  - The model, its summary (`AgentResult.summary`, in the envelope) and the staff answer can therefore tell the two apart.
  - No values or owning order ID leak: only that the intent exists. The caller is staff and the lookup is read-only, but staff are not authenticated yet (threat-model residual).
- **How it fails:** A caller tries `pi_000001…pi_999999` against one of their own orders. Each try costs a planner run and a subagent run. The code returned tells them which intents exist.
- **Suggested fix:** Either show a single "not available for this order" code to the model and the answer, and keep the distinction only in the audit, or record this oracle as an accepted residual in the threat-model row.

### N3: Low, not unsafe (records accuracy). The audit stores the foreign intent's values, marked `ok: true`, under the task's run
- **Where:** `services/backend/src/northwind_backend/views.py:203-209` → `_run` → `outcome_ref` → `audit.py:173-174`. These write every `PaymentIntent` field by reference: `order_id`, `status`, `currency` and the minor amounts.
- **What is wrong:**
  - The `payment.status` outcome for pi_000042, with O000124's amounts, sits in the O000123 task's context `<run>.tN` as `ok: true`.
  - The refusal shows only in that run's `agent.run` outcome `failures: ["intent_not_for_order"]` and in `orchestrator.task`.
  - This is BE-08's design (the port read is audited as it happened), and the audit is internal.
  - `reconstruct_order("O000123")` does not take the values into state, because pi_000042 is not among O000123's intents (`reconstruct.py:200-206`). Its timeline does list the read as an ok `payment.status`.
  - So the M1 test name and code comment "its values reach no one" overstate what happens. The tool-register's "neither the model nor the answer" is accurate.
- **Suggested fix:** Rename the test or the comment to match. Optionally record a `payment.status_refused` event that carries the code, so the audit itself shows the refusal next to the read.

### N4: Low (BE-08 accuracy; residual of L1)
- **Where:**
  - `services/backend/src/northwind_backend/agents/runtime.py:263-269`;
  - `services/backend/src/northwind_backend/orchestrator/run.py:127-138` and `:219-237` (unchanged code).
- **What is wrong:**
  - The "cancelled" outcome hard-codes `steps_ok: 0` and `status: failed`, even when some steps had already finished (for example, a fraud read completed before the deadline). `state.results()` is available and would give the true count.
  - A cancellation from outside the orchestrator, such as Ctrl-C in the CLI or a future gateway disconnect, now closes the subagent's `agent.run`. `_run_one` and `orchestrate` catch only `TimeoutError` and `Exception`, so their `orchestrator.task` and `orchestrator.run` attempts stay open. L1's goal ("no attempt left open") holds only one level down.
- **Suggested fix:** Compute `steps_ok` from `state.results()` in the handler. Give `_run_one` and `orchestrate` the same catch, write the outcome, and re-raise. Do this before BE-06 (#28) puts refunds under the same `wait_for`.

### N5: Low (documentation, hard rule 6). The coverage check works by ID, not by question
- **Where:**
  - `docs/governance/threat-model.md:168`;
  - `docs/governance/tool-register.md` `submit_plan` row ("none dropped").
- **What is wrong:** For "Where is O000123's parcel, and was it charged?", the plan `[tracking O000123]` covers every ID and passes. The payment question is silently dropped. The row lists "drops a question" as a mitigated threat, and its residual does not mention this.
- **Suggested fix:** Add to the residual: "a further question about an ID that is already covered can still be dropped." Code cannot detect this.

### N6: Low (defence in depth only)
- **Where:** `services/backend/src/northwind_backend/orchestrator/aggregate.py:162-167`.
- **What is wrong:** The second check hides the payment line from the answer only. If the tool's check were ever bypassed, for example by a future runner that does not use `orders_check`:
  - `OrchestratorResult.to_envelope()` would still carry the foreign values in `sections[].results[].value`;
  - the section would stay `complete`.
- **Suggested fix:** When the order IDs differ, drop the step from the section (or replace its value with `None`) and turn the section's status into partial, as well as hiding the line.

### N7: Low (decision 0001 spend; bounded)
- **Where:** `services/backend/src/northwind_backend/orchestrator/plan.py:159-161` against `:247`.
- **What is wrong:** The limit is checked after NFKC but before `html.escape`. 2,000 `&` characters become 10,000 characters in Sonnet's prompt, sent up to 3 times. The proxy's budget cap still holds, so no cap is breached.
- **Suggested fix:** Check the length of the final prompt text, after normalisation and escaping (this can reuse N1's single text).

## Checked, no findings
- **Hard rule 1: no finding.** The card check is unchanged. The new error, audit and answer text carry only fixed text and codes.
- **Hard rule 2: no finding now; M1 is fixed.** Payment state is shown only for its own order, both in the tool and in the answer. See N6 for the envelope.
- **Hard rule 3: no finding.** The new tests use fixture orders and the fakes only.
- **Hard rule 4: not applicable to this change.**
- **Hard rule 5: not applicable to this change.**
- **Hard rule 6: findings N1 and N5.** The escaping, the allow-lists and the fixed-text refusals, including `uncovered_id`, are unchanged and sound.
- **Hard rule 7: not applicable to this change.**
- **Hard rule 8: no finding.** The fixes add no tool and widen no scope. `INTENT_NOT_FOR_ORDER` is not a money name.
- **Hard rule 9: not applicable to this change.**
- **Hard rule 10: no finding.** `intent_not_for_order` is `permission`, not retryable, and gets the escalation alternative. "Refused" stays distinct from "empty".
- **Hard rule 11: not applicable to this change.** The prompts still say "You are an AI system".
- **Child processes get a placeholder key only, and only the proxy holds the real key: no finding.** `llm.py` is unchanged, and every run still goes through `agent_options()`.
- **Makefile does not export the key: not applicable to this change.** The Makefile is not in the diff.
- **No key in CI secrets, and CI runs in replay: not applicable to this change.** `.github/` is not in the diff.
- **Key file never copied into a worktree: not applicable to this change.**
- **Budget caps enforced and not raised: no finding.** No cap is changed. N7 is bounded by the cap.
- **Cassettes still covered by the secret/PAN scanner: not applicable to this change.** No cassette is changed and the scanner is untouched.
