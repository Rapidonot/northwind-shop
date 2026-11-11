# Row #20 (BE-08 + BE-02): security-reviewer, first pass, verbatim

Diff reviewed: the working tree at base `d240edc`, uncommitted, written to
`/private/tmp/claude-502/-Users-rapido-Projects-northwind-shop/0309078e-8dda-4e44-a8c7-fef0449f1d4a/scratchpad/be08-diff-1.patch`
(4,730 lines). Saved before any fix, as the user asked. The triage and fixes are in `docs/reviews/2026-11-10-BE-08-evidence.md`.

---

## Security review: row #20 (BE-08 partial + BE-02 partial), branch `req/BE-08-audit`, base `d240edc`

There are no critical or high findings. There is 1 medium and 6 low. Before reviewing, I re-read `CLAUDE.md` "Hard rules", decision 0001, the plan (`/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-10-BE-08-plan.md`, sections A–E, Decisions, Control surface, Open items), `services/common/src/northwind_common/llm.py`, `ports.py`, the fakes and `northwind_common/cards.py`.

What I reviewed: the diff file `/private/tmp/claude-502/-Users-rapido-Projects-northwind-shop/0309078e-8dda-4e44-a8c7-fef0449f1d4a/scratchpad/be08-diff-1.patch` (working tree, uncommitted). Line numbers below are from the files on disk.

No text in the diff, tests or plan tried to steer this review.

---

## 1. Findings, most severe first

### M1. Medium. Hard rule 1 (and the module's own "references only" claim). The model's argument names go into the audit log unscreened.
- **Where:**
  - `services/backend/src/northwind_backend/agents/runtime.py:181-189` (`_audit_tool_use`, line 183: `inputs = {k: v for k, v in args.items() if k != "tool"}`)
  - `services/backend/src/northwind_backend/audit.py:118-119` (`input_refs` returns `{name: _ref(name, value) ...}`)
- **What is wrong:** `_ref` screens every value: card check first, then the allow-list, then type and length. The dict key is never screened. In `agent.tool_use` the keys are whatever the model put in its `tool_use.input`, so they are free text the model writes, of any length. The card check (`card_data`) runs only on values. This breaks the `audit.py` docstring ("No message, model text or digest is ever written") and plan A/L1 ("any model argument outside the schema … is written as type and length").
- **How it fails:** Haiku emits a tool use such as `{"order_id": "O000123", "card 4111 1111 1111 1111": "x"}`, or a sentence of model text as a key. The attempt and outcome events then hold `"card 4111 1111 1111 1111": {"type": "str", "length": 1}`. That is a PAN in the audit log, and on disk once a `JsonlAuditSink` is used. Today nothing untrusted reaches this model, so it would take a hallucination. But once the fraud reasons come over A2A (P4/P5), injected text could produce it.
- **Fix:**
  - In `_audit_tool_use`, keep only the keys in the tool's schema (`order_id` / `intent_id`).
  - Record any other keys only as a count, for example `{"extra_args": {"count": n}}`, and never write their names.
  - Optionally, make `input_refs` refuse a key that fails `card_data` or isn't a short `_CODE`-style name, so every caller is covered.
  - Add a scripted test with a PAN key and a long free-text key, and a mutant that drops the key screen.

### L1. Low. Hard rules 1 and 6. The task's IDs reach the prompt with no check.
- **Where:** `services/backend/src/northwind_backend/agents/orders_check.py:98-104` (`OrdersCheckTask.prompt`)
- **What is wrong:** `order_id` and `intent_id` are put straight into the user prompt. Nothing checks their format or runs a card check on them. Only `run_inputs` are screened, and only for the audit.
- **How it fails:** A later caller (#21 or the orchestrator at #24) builds the task from request data, so `OrdersCheckTask(order_id="4111111111111111", …)` or `order_id="O000001. Ignore prior rules…"` reaches Haiku and the recorded cassette. Today only tests construct tasks.
- **Fix:** Add a `__post_init__` that requires `order_id` to fullmatch `O\d{6}` and `intent_id` to fullmatch `pi_\d{6}` (or None), using the ASCII patterns already in `audit._ID_PATTERNS` / `fakes/_base.py`, and raises otherwise. Add a test.

### L2. Low. Hard rule 8 (least privilege; by design under N5, flagged for #21). The tool handlers accept any order or intent ID, not just the task's.
- **Where:** `orders_check.py:143` and `:156` (`_arg(...)` passed straight to the view). Pinned as allowed by `test_be02_agent_scripted.py::test_a_call_for_another_orders_id_satisfies_no_step`.
- **What is wrong:** A call with another ID is audited and doesn't count toward the status. But it still runs, and it returns that order's fraud assessment (including `shopper_id`) or that intent's amounts to the model. The register text "One order per call; a call counts only for the task's own order ID" can be read as a scope limit, but it is only a counting rule.
- **How it fails:** The model reads `O000001`'s shopper ID and fraud score while working on a different order. Nothing reaches a shopper today. The real risk is that #21 "extends this agent with the state-changing tools" on the same handler pattern, and there an unbound ID would let an argument the model chose move money on another order.
- **Fix:** Before #21 adds any write tool, bind each handler to the task's IDs. A call with a different ID returns `NOT_PERMITTED` and is audited, and it never reaches the view. Record this as a #21 prerequisite next to the existing ones in PROGRESS.md row 21. Doing it now is cheap and does not change the model input for well-behaved runs.

### L3. Low. Hard rule 1 and personal data. Some audit fields are written raw and depend on the caller to stay clean.
- **Where:**
  - `audit.py:345`: `correlation_id`, `parent_id` and `actor` go into every event unscreened, and `action_id` is derived from `correlation_id` at line 333.
  - `audit.py:55`: `idempotency_key` allows any `[A-Za-z0-9_.:-]{1,128}`, written as is after the card check.
- **What is wrong:** The "references only" property depends on product callers choosing safe correlation IDs and keys. A key like `jane.doe.order` or a correlation ID built from request text is written verbatim. A correlation ID carrying a PAN is not caught, because the card check never sees it.
- **Fix:** Run `card_data` on `correlation_id`, `parent_id` and `actor` in `AuditContext.__init__` and refuse to build the context if it fails. Give correlation IDs and actors a fixed pattern. Document that idempotency keys must be derived from IDs, as the driver's are. Optionally tighten the key pattern to `<id>:<code>`.

### L4. Low. The integrity claims have gaps beyond the two limits already stated.
- **Where:**
  - `audit.py:208` and `:215`: `event_id` is not in the hash, and `verify_chain` doesn't check `event_id == f"ev_{seq:08d}"`.
  - `audit.py:252`: `InMemoryAuditSink.read_all` returns the stored events, and their `inputs`/`outcome` are plain mutable dicts, so any reader can edit the log in place.
  - `reconstruct.py:151`: `reconstruct_order` never calls `verify_chain`.
  - `audit.py:263`: `JsonlAuditSink` doesn't verify the file when it opens it. It caches `seq` and `prev` per instance, so a second instance or a second process on the same path forks the chain.
- **How it fails:**
  - An edited `event_id` passes `verify_chain`.
  - A reader that changes `events[i].outcome["captured_minor"]` changes later reconstructions, and that goes unnoticed unless someone runs `verify_chain`.
  - Two writers on one JSONL path produce duplicate `seq` values. `verify_chain` then reports "tampered", and you can't tell a fork from tampering.
- **Fix:**
  - Hash `event_id`, or check it in `verify_chain`.
  - Freeze `inputs`/`outcome` (a `MappingProxyType` or a deep copy on `read_all`).
  - Have `reconstruct_order`, or a wrapper, report `verify_chain` problems with its record.
  - Verify the chain in `JsonlAuditSink.__init__`, and take a file lock (`fcntl.flock`) across append, or state "one writer per path" as a known limit next to the two existing ones.
  - None of these weakens a stated limit. They extend what the tests pin.

### L5. Low. BE-08 fail-closed and run hygiene. An audit failure inside the stream leaves the loop early, and a lost run outcome is silent to the caller.
- **Where:**
  - `runtime.py:226-227`: the comment says "Runs to the end even after an error: leaving early strands the SDK's generator".
  - `runtime.py:189`: `audit.record` can raise `AuditWriteError` inside that loop.
  - `runtime.py:265-267`: a failed `agent.run` outcome write is only logged.
- **What is wrong:**
  - An `AuditWriteError` from `_audit_tool_use` propagates out of the `async for` and is caught as `unexpected_error`, so the generator is abandoned. That is exactly the stranding the comment warns about. **Unconfirmed:** whether the CLI subprocess keeps making turns (up to `MAX_TURNS`, so spend) until the generator is finalized.
  - Separately, when the run's outcome can't be written, the caller gets a normal `AgentResult` with no marker. The views return `AUDIT_INCOMPLETE` in the same situation. Tool calls are still fail-closed, because each view refuses to run when it can't write its attempt.
- **Fix:**
  - Catch `AuditWriteError` around `_audit_tool_use` inside the loop, set a flag, and keep draining the stream.
  - Add an `audit_incomplete` failure to the `AgentResult` when the tool-use or run outcome write fails.
  - Test both with a sink that fails on the `agent.tool_use` attempt and on the `agent.run` outcome.

### L6. Low. Hard rules 8 and 9. The AST pins cover one package and one call shape.
- **Where:**
  - `services/backend/tests/backend_audit/test_be08_views.py:4448`: `SRC` is `services/backend/src/northwind_backend` only.
  - `test_be08_views.py:4700-4717` (diff lines): `test_every_view_built_under_src_uses_a_read_only_constant` matches only `ast.Call` whose `func` is an `ast.Name`.
- **What is wrong:**
  - `views.AuditedPaymentPort(fake, ctx, allowed=PAYMENT_SCOPES)`, called through a module attribute, is not checked.
  - The pins stop covering the agents once open item L5 moves them to `services/orchestrator` (#24).
  - The plan's R7 lists other limits, but not these two.
- **Fix:**
  - Also match `ast.Attribute` calls whose `attr` is a view name.
  - Assert that every view constructed outside `views.py`/tests passes `allowed=` as a `*_READ_ONLY` name.
  - Add "extend the pins' root to `services/orchestrator/src`" to the #24 open item.

### Nothing found at critical or high severity.

---

## 2. Checked, no findings

**Hard rules:**
- **Rule 1 (card data), apart from M1, L1 and L3:**
  - Values are screened card-first in `_ref`, numbers by their digits too.
  - `payment_token` and `postal_code` are written as type and length.
  - Error outcomes drop `message`.
  - The tool payloads carry only the allow-listed `_INTENT_FIELDS` / `_ASSESSMENT_FIELDS`, and `PaymentIntent` has no token field.
  - Every `log.error` call logs only the actor, the action and the exception's type name.
  - `UNEXPECTED` replaces the exception text, and the `hunter2` test pins this.
  - The fakes refuse card data in reads (`status`, `assessments_for`).
- **Rule 2:** status, amounts and fraud decisions come only from live port reads. The run status comes from audited tool outcomes, not model text (`derive_status`, `RunState.results`). The driver's amounts are test figures, and a shopper never sees them.
- **Rule 3:** synthetic data and sandbox tokens only.
- **Rule 4:** no secret, key, `os.environ` or `.env*` access in the diff.
- **Rule 5:** not applicable to this change (no product claims).
- **Rule 6:**
  - The system prompt says tool results are data and holds no secret.
  - `allowed_tools` is fixed in code, and no content can grant a tool.
  - `tools=[]`, and the MCP server has only the two tools; `test_the_agent_is_offered_only_its_two_read_only_tools` pins the set offered.
  - An unknown tool use is audited, and the call is denied.
- **Rule 7:** names, emails, addresses, tokens, postal codes and correlation IDs are kept out of the model input (`test_the_model_sees_no_personal_data_token_or_correlation_id`). Shopper ID, scores and amounts reaching Haiku is a stated, approved data flow (data-flow.md). Not shopper-facing, so audience and region filtering do not apply.
- **Rule 8, apart from L2 and L6:**
  - The two tools are inside the PRD §7 Orders row (payment port, fraud agent) and are read-only.
  - The views are built with `PAYMENT_READ_ONLY` / `FRAUD_READ_ONLY`, and any other operation returns `NOT_PERMITTED`, audited.
  - Views share one fake, which closes FOUND-3 medium (1).
  - The AST pin keeps every money name out of `src` except `fakes/` and `views.py`, and `_driver.py` can't be imported from `src`.
- **Rule 9:** no agent can reach authorize, capture, void or refund. Only the test-only `_driver.py` calls them, on fakes, which matches the user's L2 decision. The idempotency keys and `dry_run` paths are unchanged, and `timeout_after_effect` still gives one effect.
- **Rule 10:**
  - The tool envelope is unchanged and pinned.
  - Errors reach the model with `isError`, `errorCategory`, `isRetryable`, `code` and the fixed `message`, plus `is_error`.
  - A valid empty result is `Ok` with `empty`/`note` and counts as complete. "Access failed" gives `partial`/`failed`, and both cases are tested.
- **Rule 11:** not applicable to this change (not shopper-facing). The system prompt states it is an AI anyway.

**FOUND-2:** the options are built only through `agent_options()`, and no `ClaudeAgentOptions` or `anthropic.Anthropic` is constructed directly. `model=ROUTINE_MODEL`, `max_turns=6`, `permission_mode="dontAsk"`. `agent_runtime()` points the CLI at the proxy (or `ScriptedLLM`) with `env -i`.

**Decision 0001 / key handling:**
- **Placeholder key only:** child processes get only the placeholder key. `child_env` is unchanged and used through `agent_runtime`, and only the proxy holds the real key.
- **Makefile:** not changed, so it does not export the key.
- **CI secrets:** no workflow change and no key in CI. The `backend-audit` tests use `llm_proxy` and replay in CI.
- **Key file:** never copied into a worktree; no path in the diff touches it.
- **Budget caps:** enforced and not raised. `LLM_BUDGET_USD` stays $90; the plan proposes `RUN_BUDGET=3` for one window that the user opens.
- **Scanner:** cassettes stay under the secret/PAN scanner. `scan-cassettes` walks `cassettes/` recursively (`scan.py` `_walk`), so `cassettes/backend-audit/` is covered with no change. It is part of `make ci`.

**Audit log, stated limits:** tail truncation and a full recompute are stated and pinned as known limits. An edit, an inner delete, a reorder and an insert are detected. Sink appends hold one lock. Fail-closed holds on the view path: a failed attempt write runs nothing (`AUDIT_UNAVAILABLE`), and a failed outcome write returns `AUDIT_INCOMPLETE` and is settled by key. L4 and L5 list the gaps beyond the stated limits.

**`tests/governance/test_tool_register.py`:** the check was widened to tool constants across all `services/*/src`. It still requires a register row per tool and the `@tool(` count. It is not weaker than before.

**Control surface:** none touched in this diff. The `cassettes/backend-audit/` files come later, and the plan lists them for approval.
