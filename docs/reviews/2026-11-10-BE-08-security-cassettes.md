# Row #20 (BE-08 + BE-02): security-reviewer, scoped read of the cassettes and the post-re-review fixes, verbatim

What was read: the cassette commit `86817e0`
(`/private/tmp/claude-502/-Users-rapido-Projects-northwind-shop/0309078e-8dda-4e44-a8c7-fef0449f1d4a/scratchpad/be08-diff-cassettes.patch`)
and the N1–N3 fixes in `b3c4f80`
(`/private/tmp/claude-502/-Users-rapido-Projects-northwind-shop/0309078e-8dda-4e44-a8c7-fef0449f1d4a/scratchpad/be08-diff-src-at-b3c4f80.patch`).
Saved before anything else was changed. What was done with C1 to C3 is in `docs/reviews/2026-11-10-BE-08-evidence.md`.

---

## Scoped security read: row #20 (BE-08 + BE-02), branch `req/BE-08-audit`, the cassettes in 86817e0 and the N1–N3 fixes in b3c4f80

**Verdict:** nothing at critical, high or medium. The 12 cassettes hold no card data, secret, credential, personal data or correlation ID. Their system prompt and tool schemas match `agents/orders_check.py` exactly. N1, N2 and N3 each resolve their note and add no new problem. I have three low notes, all about text the model saw or wrote, plus one stale doc line.

Before reviewing I re-read `CLAUDE.md` "Hard rules" and decision 0001. What I read:
- the cassette patch `/private/tmp/claude-502/-Users-rapido-Projects-northwind-shop/0309078e-8dda-4e44-a8c7-fef0449f1d4a/scratchpad/be08-diff-cassettes.patch`
- the source patch `/private/tmp/claude-502/-Users-rapido-Projects-northwind-shop/0309078e-8dda-4e44-a8c7-fef0449f1d4a/scratchpad/be08-diff-src-at-b3c4f80.patch`
- the earlier re-review `/Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-10-BE-08-security-rereview.md`
- these files on disk: `orders_check.py`, `audit.py`, `errors.py`, `runtime.py`, `reconstruct.py`, `test_be02_agent_recorded.py`, `test_be08_security_fixes.py`, `test_be08_views.py`, `docs/governance/data-flow.md` and the PROGRESS.md row #20

I did not run `make scan-cassettes` or any tests. "scan clean: 0 finding(s)" is the caller's result, and the key-fragment scan is still for the user to run. No text in the cassettes or tests tried to steer this review.

---

## 1. Cassettes (`cassettes/backend-audit/`, 12 files)

**How the 12 files map to the 12 runs:** there are 3 first-turn requests (seq 0: run1 `601306376ae0…`, run5 `8e59fe1d2ef2…`, run8 `1719ac8bb41c…`) and 9 second-turn requests (seq 1: runs 1, 3, 4, 5, 7, 8, 10, 11, 12).
- Every run on the same order and task sends the same first request, so they share one seq 0 file.
- Runs 2, 6 and 9 (`fraud_once`) have no files of their own. Local recovery hides the first timeout, so the model sees the same request as in runs 1, 5 and 8, and the hash match replays those files.
- This is what hash matching should do. It is not a finding.

**Spend:** the 12 `cost_usd` values add up to $0.00120729, which matches the $0.001207 in the commit message.

### Findings

**C1. Low. Hard rule 5 in spirit (not shopper-facing). The answers give decimal amounts the tool never returned.**
- **Where:** body (line 2) of `6ee9742d6d00…` (run1), `cf79d9093b65…` (run11), `978cbbf9880d…` (run3) and `b2857e42104f…` (run12).
- **What is wrong:** the tool returned `"currency": "USD", "authorized_minor": 20000, "captured_minor": 20000`. The answers say "20000 minor units (USD 200.00)" or "$200.00 captured of $200.00 authorized". The decimal amount is the model's own arithmetic. The system prompt says "never guess … an amount". The conversion happens to be right for USD.
- **How it fails:** on a zero-decimal currency (JPY) or a three-decimal one, the same habit gives a wrong amount. A later consumer of `AgentResult.summary` that shows it as fact would then repeat that wrong figure. Today nothing reads `summary` except the envelope, and the audit log stores only its length (`runtime.py:287`). Status comes only from `RunState`, never from this text.
- **Fix:** log it for #21/#24. Any later display of a run's amounts should be built from `StepResult.value`, never from `summary`. If the prompt is changed to "report minor units as returned", all 12 cassettes become misses and need a planned re-record in a window (stop 2). Do not hand-edit them.

**C2. Low. Hard rule 6 / hard rule 9, looking ahead to #21. The timeout envelope tells the model a retry is safe, but the system prompt tells it not to retry.**
- **Where:** `errors.py:79-83` (`TIMEOUT`: "It may or may not have taken effect; a retry with the same key is safe."). The model received this text in `24a17edd8dec…` (run7), `945f7393c28c…` (run10) and `978cbbf9880d…` (run3).
- **What is wrong:** the text was written for write calls. Here it reaches the model on a read tool, and the model holds no key. In all three recordings the model followed the prompt and did not retry.
- **How it fails:** a model that gives the tool text more weight than the prompt calls the tool again. Today that is bounded: `MAX_TURNS=6`, the handler binds the call to the task's own ID, the call goes through recovery and the audit log, and the call is read-only. Once #21 adds write tools, the same sentence tells the model that a re-issued write is safe, while the model does not control the key.
- **Fix:** before #21, make the envelope the model receives tool-specific, or remove the key sentence from the model-facing message (the audit and caller copy can keep it). Make this a #21 prerequisite in PROGRESS row #20. Changing it now changes the model input and needs a re-record.

**C3. Low. Documentation (hard rules 3 and 7 accounting). The approved data-flow list leaves out two fields the model actually received.**
- **Where:** `docs/governance/data-flow.md:37` and `orders_check.py:10-12`.
- **What is wrong:** both list order, intent and shopper IDs, status, minor amounts, and fraud decision, score and reasons. The tool results in the cassettes also include `assessment_id` (`fa_000001`) and `currency` (`USD`), from `_ASSESSMENT_FIELDS` and `_INTENT_FIELDS` (`orders_check.py:85-94`). Both are synthetic codes, not personal data.
- **How it fails:** the governance record doesn't match the recorded flow, so a later reviewer can't check model input against it field by field.
- **Fix:** add `assessment_id` and `currency` to the data-flow row and to the module docstring. No code or cassette change is needed.

### Checked, no findings (cassettes)
- **Hard rule 1:** no PAN, CVV or long digit run in any prompt, tool argument, tool result or answer. Amounts are minor-unit integers. `pi_000001` is a payment intent, which agents are allowed to see.
- **Hard rule 3:** the data is synthetic only: Northwind, `O0000xx`, `S0065`/`S0103`/`S0140`, and fake-port results. A grep for `/Users`, `rapido`, the git user name, `@`, `tok_`, `card` and `cvv` found nothing.
- **Hard rule 4:** `match.headers` holds only `anthropic-beta` and `anthropic-version` in all 12 files. There is no `x-api-key`, `Authorization`, `Bearer`, placeholder or `sk-ant` anywhere. `metadata.user_id` is scrubbed. The `x-anthropic-billing-header: cc_version=…` system block is the CLI's version marker; it appears in all 63 earlier cassettes and is not a credential. `test_id` paths are relative to the repo.
- **No correlation ID in model input:** `run-\d` does not appear in any cassette. The tool envelopes carry only `isError`, `value`/`note`, or `errorCategory`/`isRetryable`/`code`/`message`.
- **System prompt and tools match `orders_check.py`:** the `SYSTEM_PROMPT` text (`orders_check.py:50-64`) appears verbatim, once, in all 12 files. `FRAUD_DESCRIPTION`/`PAYMENT_DESCRIPTION` and both schemas match. There are exactly two tools, `mcp__northwind_orders__get_fraud_assessments` and `mcp__northwind_orders__get_payment_status`, and no built-in tools.
- **Hard rule 5:** apart from C1, every answer matches its tool results: scores 4/13/92, decisions allow/allow/review, reasons `flagged_order`, an empty list reported as "no fraud assessment yet", and timeout and outage reported as "lookup failed" with no retry. Run12's "needs a fresh check" is advice, not a claim about data.
- **Hard rule 6:** tool output arrives as `tool_result` blocks, and the prompt labels it as data. No retrieved or tool text grants anything.
- **Hard rule 7:** not applicable. This agent retrieves no documents, and the cassettes hold none.
- **Hard rule 8:** two read tools, the same as `allowed_tools` and PRD §7's Orders row slice.
- **Hard rule 9:** not applicable. No state-changing tool.
- **Hard rule 10:** errors reach the model structured. "Access failed" (`is_error: true` with a code) and "valid empty" (`"value": [], "note": …`) are distinguishable.
- **Hard rule 11:** the system prompt says "You are an AI system". The agent is not shopper-facing.

---

## 2. Fixes made after the re-review (b3c4f80)

**N1: resolved, nothing new.**
- `runtime.py:159`: `"audit_incomplete_run": (FailureType.TRANSIENT, True)`. It is used at `:278` (lost tool-use write) and `:294` (lost run outcome).
- `errors.py:126` maps it to `("rerun_the_run", "escalate_to_care")`. The views' `AUDIT_INCOMPLETE` and its `retry_same_key_to_settle` are unchanged, as the re-review asked.
- `reconstruct.py:42`'s `UNCERTAIN_CODES` checks only a tool outcome's `code`, never the run outcome's `failures` list, so the rename doesn't change reconstruction.
- `test_be08_security_fixes.py:216` and `:227-230` pin the code, that it is retryable, and the exact alternatives.
- The #21 prerequisite (build write keys from task IDs alone) is in PROGRESS.md row #20.
- **Cosmetic, no finding:** if both writes fail, the result carries two `audit_incomplete_run` entries.
- **Stale doc, low:** `docs/reviews/2026-11-10-BE-08-evidence.md:79` still lists the run-level code as `audit_incomplete`; line 160 has the change. Fix that line.

**N2: resolved, nothing new.**
- `audit.py:273` returns `copy.deepcopy(event)` while holding the lock.
- `_seal`'s JSON round trip already means the caller's draft dicts are not shared with the stored event. So neither the input nor the return value can reach the log in place.
- `JsonlAuditSink.append` returns a freshly sealed object that is not kept in memory, so editing it changes nothing on disk.
- `test_be08_security_fixes.py:258-274` pins this.

**N3: resolved for what it asked, nothing new.**
- `audit.py:373-383`: `correlation_id`, `actor` and `parent_id` are now properties with no setter, so `setattr` raises `AttributeError` (pinned at `test_be08_security_fixes.py:250-255`).
- A grep under `services/` finds no code that assigns these attributes, so nothing breaks.
- `child()` and `_write` read the properties.
- **Remaining, informational, not regressions:**
  - The private `_correlation_id` and others can still be written by code that means to. Python can't stop that, and the class has no `__slots__`.
  - `self.sink` (`:363`) is a public attribute.
  - The clock's output goes into `at` without a check. The clock is chosen by code when the context is built.
- The "fixed format" half of N3 (`run-<id>`) is still for #24. It is not visible in the PROGRESS row #20 open items I could match. Unconfirmed: it may be recorded elsewhere.

**N4: not fixed, recorded as a known limit.** It is in the docstring at `test_be08_views.py:299-301` and in PROGRESS.md row #20 (renamed import, `partial`, re-bound class; #24). That matches the re-review's second option.

---

## 3. Decision 0001 checklist
- **Placeholder key only, and only the proxy holds the real key:** the cassettes store no credential header. `agent_options()`/`agent_runtime` are not in either diff.
- **The Makefile does not export the key:** the Makefile is not in either diff.
- **No key in CI secrets, and CI runs in replay:** no workflow change. The recorded tests replay through `llm_proxy`.
- **The key file is never copied into a worktree:** nothing in either diff touches it.
- **Budget caps enforced and not raised:** unchanged. The recorded spend is $0.00120729.
- **Cassettes still covered by the secret/PAN scanner:** the files are under `cassettes/`, which `make scan-cassettes` covers. The regex scan was clean (caller's result). The key-fragment scan is still for the user, from the runner.

**Control surface:** `cassettes/backend-audit/**` is added, as the plan expects for this window. No cassette was modified or deleted. The source changes are in `services/backend/**`, a hard-rule-1/9 path, so the row stays not routine, as planned.

**Summary by severity:** critical none; high none; medium none; low C1, C2, C3 and the stale evidence line under N1.
