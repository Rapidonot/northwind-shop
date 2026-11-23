# Scoped security re-review 3: row #24, BE-01 (orchestrator), fixes for N1–N7

All seven fixes hold for the cases they target: N1, N3, N4, N5, N6, N7 and the recording of N2. The fixes introduced nothing critical, high or unsafe.

- **Q1:** IDs can still escape the coverage check. N1 closed only the NFKC and category-Cf forms (R1, medium).
- **Fix side effect:** removing Cf characters can glue a written ID to the text next to it, so code no longer sees it (R2, low).
- **CI break:** the N3 rename broke a governance citation, so `make ci` will fail (R3, low).

**Scope.**
- I read `/tmp/claude-502/northwind-review-BE-01-security-3.diff`. Base is `85aae52`, head is the uncommitted working tree.
- I read these on disk: `orchestrator/plan.py`, `orchestrator/run.py`, `orchestrator/aggregate.py`, `agents/runtime.py`, `agents/orders_check.py:89-95` and `:199-211`, `audit.py:104-112` and `:429-487`, `errors.py:141-229`, the three orchestrator test files and `tests/governance/test_citations.py`.
- I re-read CLAUDE.md "Hard rules" and decision 0001.
- I am read-only. No tests have run, and everything below comes from reading the code.
- Nothing I read tried to steer the review.

## Status of each fix

| Item | Verdict | Notes |
|---|---|---|
| N1 | **Fixed for NFKC and Cf; other forms remain (R1, R2)** | `canonical()` is at `plan.py:154-159`. `check_plan` uses it at `:232`, `prompt_for` and `_model_text` at `:162-165` and `:259-262`, and `request_problem` at `:170` and `:176`. Code and the model now read the same string. Escaping cannot create or hide an ID: entities start with `&` and end with `;`, and neither character is in the boundary class at `:181-182`. The card check runs on both the raw and the canonical text |
| N7 | **Confirmed** | `plan.py:174` checks `len(_model_text(request))`. The test at `test_be01_review_fixes.py:281-284` covers it. Nit: `canonical()` runs on input of any length at `:170`, before the length check on the raw text (R4) |
| N4 | **Confirmed** | `runtime.py:263` takes `steps_ok` from `state.results()`. That call only reads state and cannot raise, because `alternatives_for` has a default (`errors.py:141-143`). `run.py:148-152` and `:285-287` close the open attempt and re-raise. See Q3 |
| N6 | **Confirmed** | `aggregate.py:190-219` removes the foreign step together with its values, adds `intent_not_for_order` and makes the section partial or failed. `_section_lines` (`:160-175`) no longer checks on its own. `aggregate()` applies the check to every section (`:227`). See Q4 |
| N3 | **Renamed, but the rename broke a citation (R3)** | The test is now `…_reach_neither_model_nor_answer` (`test_be01_review_fixes.py:70`). The comment at `orders_check.py:206` still says "reach no one" |
| N2, N5 | **Recorded** | `threat-model.md:168` lists both as residuals, and both are accurate. The `submit_plan` row in the tool register now says "by ID, not by question" |

## Answers

### Q1. Can an ID the model sees still escape `ids_in` or `_in_request`?
Yes. Both functions use the same ASCII pattern and boundaries on the canonical text (`plan.py:181-196`), so they agree with each other. Any form the model reads as an ID but the pattern does not match fails the way N1 did: code does not require the ID, and the model cannot cover it. See R1, and R2 for a case the fix itself introduced.

### Q2. Can the canonical text make an ID appear that the staff did not write? Does it matter?
Yes, but it does not matter. NFKC turns compatibility forms into ASCII IDs:
- `O⁰⁰⁰¹²⁴` (superscript digits) becomes `O000124`.
- `O00㉑24` (circled 21) becomes `O002124`.
- `pi﹍000042` (dashed low line) becomes `pi_000042`.

It does not matter for these reasons:
- The model reads exactly that string. "No invented ID" means "no ID that is not in the text the model reads", and that still holds.
- Anyone who controls the request text can already write any ID in plain ASCII. This is the documented residual "steered among IDs written in the request".
- Each section's label names the order it looked up (`aggregate.py` `_label`).
- Every lookup is read-only and bound to that one ID.

The fail-closed side effect is that a request can now require more IDs to be covered, which can end in `uncovered_id` or `too_many`. That failure is visible, not silent.

No finding. The reverse case does matter: removing a Cf character can make a written ID disappear (R2).

### Q3. Is the `_run_one` `CancelledError` path correct inside a TaskGroup?
Yes. I found no double write and no case where a deadline is reported as a cancel.

**Deadline versus cancel.**
- In Python 3.12, `wait_for` with a timeout runs `async with timeouts.timeout(...)` in the same task. When its own deadline expires, it turns the `CancelledError` into a `TimeoutError` before `run.py:148` can see it, because the uncancel count matches its own cancel.
- A `CancelledError` reaches `:148` only when someone else also cancelled the task: the TaskGroup, Ctrl-C or a caller. If a cancel and the deadline arrive together, "cancelled" is the correct report.

**No double write.**
- `audit.outcome` is synchronous (`audit.py:466-475`), and sink errors are wrapped as `AuditWriteError` (`:448-451`).
- After each `try`, `_run_one` and `orchestrate` contain no `await` before the outcome write. The only cancellation points are `wait_for` and `_plan_and_run`, so each attempt gets exactly one outcome.
- If the TaskGroup cancels a child task before it starts, its coroutine never runs. No attempt is opened, so none is left open.

**Different labels by design.** On a deadline, `agent.run` records `cancelled` (`runtime.py:269`) and `orchestrator.task` records `deadline`. This is the L1 design, and it is consistent: the task level names the reason.

**Unconfirmed, no finding.** If the SDK ever turned a cancellation into a `ClaudeSDKError`, `run_subagent` would return normally, and a deadline would show up as `model_unavailable`. My earlier reading of the SDK says the `CancelledError` propagates.

### Q4. Does N6 change any legitimate same-order payment?
No.
- `_INTENT_FIELDS` includes `order_id` (`orders_check.py:89-95`).
- The tool sets a value only when `intent.order_id == task.order_id` (`:204-209`).
- The task's order is the concern's order (`run.py:78`).
- `test_be01_units.py:253` and `:295` still assert the same-order line "payment pi_000042 is captured (…)" after `aggregate`.

The one edge case fails closed: a payment value with no `order_id` key would count as foreign. Only test runners do that (`test_be01_criteria.py:378`), and they call `run_concerns`, not `aggregate`.

The status can never move from failed to partial in practice. A foreign step is an ok step, so `derive_status` had already set the section to at least partial.

## Findings

### R1: Medium, not unsafe (BE-01 "never dropped", hard rule 6 residual). Some forms of an ID still escape the coverage check
- **Where:** `services/backend/src/northwind_backend/orchestrator/plan.py:181-196` (`_ANY_ID`, `ids_in`) and `:188-190` (`_in_request`), applied to `canonical()` at `:154-159`.
- **What is wrong:** the model is likely to read these forms as IDs, but code does not count them:
  - **Lowercase:** `o000124`, `PI_000042`.
  - **Homoglyphs NFKC does not fold:** Cyrillic `О` (U+041E) or Greek `Ο` (U+039F) in place of the Latin `O`.
  - **Invisible characters that are not Cf:** U+034F COMBINING GRAPHEME JOINER and the variation selectors U+FE00–FE0F (both Mn), and the Hangul fillers U+115F, U+1160 and U+3164 (Lo). Any combining mark inside the digits also breaks the match.
  - **ASCII glued to a letter or underscore:** `orderO000124`, `intent_pi_000042`. The boundary deliberately rejects these (`test_be01_review_fixes.py:178`). My previous review said "plain ASCII IDs are covered", which was too broad.
  - **Encodings the model may decode:** `&#79;000124` is escaped to `&amp;#79;000124`, and percent-encoding works the same way.
- **How it fails:** this is the same path as N1.
  - Staff type "Was O000123 charged? Where is o000124's parcel?".
  - The model covers `O000124`, and code refuses it as `id_not_in_request`. Or the model covers `o000124`, and code refuses it as `bad_order_id`.
  - The model then drops the ID or runs out of turns. If it drops it, `[payment_fraud O000123]` passes, and the answer reads "complete (1 of 1)".
  - Whether the model drops the ID or loops is model behaviour: **unconfirmed**. Lowercase is the realistic case, because it needs no adversary.
- **Suggested fix (stricter, fail-closed):** in `request_problem`, run a second, looser scan for ID-shaped tokens:
  - fold case;
  - remove every Default_Ignorable_Code_Point and every Mn character;
  - map the confusables of `O`, `p`, `i` and `_` to ASCII;
  - drop the boundary to digit-only;
  - refuse the request with a fixed reason such as `ambiguous_id` if the loose scan finds an ID that the strict scan of `canonical(request)` does not.

  Add unit tests for each form above. The threat-model row should say that coverage applies only to IDs written literally, and that encoded forms are a residual.

### R2: Low, not unsafe. Introduced by the N1 fix: removing a Cf character can glue an ID to the next character, so it disappears from both checks
- **Where:** `plan.py:159` together with the boundaries at `:181-182`.
- **What is wrong:**
  - `O000123​O000124` (a zero-width space used as a separator) becomes `O000123O000124`. Neither ID matches.
  - `O000123­is` (a soft hyphen) becomes `O000123is`. No ID matches.
  - Before the fix, the raw text matched both IDs. The model still reads them, and `_in_request` refuses them, so this is R1's silent-drop path, now reached from text that used to be covered.
- **How it fails:** staff paste text from a web page that uses U+200B between table cells. Neither order is required, so a plan with only `unsupported/other`, or one that covers neither ID, passes.
- **Suggested fix:** compute `ids_in` both with each Cf character removed and with each Cf character replaced by a space. Refuse the request with a fixed reason if the two sets differ. Add a test with U+200B between two IDs.

### R3: Low, not unsafe (records; breaks `make ci`). The threat-model row cites the old N3 test name
- **Where:** `docs/governance/threat-model.md:168` cites `services/backend/tests/orchestrator/test_be01_review_fixes.py::test_an_intent_of_another_order_is_refused_and_its_values_reach_no_one`. That test was renamed at `test_be01_review_fixes.py:70`.
- **How it fails:** `tests/governance/test_citations.py::test_every_cited_path_exists[threat-model.md]` reports "no such test" (see that file's `:72-81`, `:171-175`), and `make ci` fails. Every other citation in the new rows resolves; I checked each one.
- The comment at `services/backend/src/northwind_backend/agents/orders_check.py:206` still says "its values reach no one", which N3 said overstates what happens.
- **Fix:** update the citation to the new name and reword the comment.

### R4: Low, not unsafe (local CPU only). `request_problem` normalises input of any length before it checks the raw length
- **Where:** `plan.py:170` runs before `:174`.
- **What is wrong:** a request of many megabytes is NFKC-expanded, up to about 18 times for characters such as U+FDFA, before `too_long` refuses it. This happens on the staff CLI only. There is no model call and no spend.
- **Fix (optional):** check `len(request) > MAX_REQUEST_CHARS` first.

### R5: Low, not unsafe (test gap). No test shows N4's `steps_ok` above 0
- **Where:** `test_be01_review_fixes.py:334` asserts only `steps_ok == 0`.
- **What is missing:** nothing shows that a step finished before the cancel is counted.
- Separately, the cancelled outcome at `runtime.py:264-271` still records `status: failed` and `ok: false` even when `steps_ok` is above 0. That is acceptable for a cancelled run, but the audit should say so explicitly.
- **Fix:** add a case where the fraud read finishes before the cancel, and assert `steps_ok == 1`.

## Checked, no findings
- **Hard rule 1: no finding.** The card check runs on both the raw and the canonical text (`plan.py:176`). Only fixed text and codes are new. The digit patterns in `cards.py` are Unicode `\d`.
- **Hard rule 2: no finding.** N6 keeps payment state bound to its own order in both the envelope and the answer.
- **Hard rule 3: no finding.** The tests use fixture IDs and fakes only.
- **Hard rule 4: not applicable to this change.**
- **Hard rule 5: not applicable to this change.**
- **Hard rule 6: R1 and R2.** The escaping, the allow-lists and the fixed-text refusals are sound. N2 and N5 are recorded as residuals.
- **Hard rule 7: not applicable to this change.**
- **Hard rule 8: no finding.** No tool was added and no scope widened.
- **Hard rule 9: not applicable to this change.** Everything stays read-only.
- **Hard rule 10: no finding.** `intent_not_for_order` is `permission` and not retryable, and "cancelled" is distinct from "deadline".
- **Hard rule 11: not applicable to this change.**
- **Child processes get a placeholder key only, and only the proxy holds the real key: not applicable to this change.** `llm.py` and `agent_options()` are untouched.
- **Makefile does not export the key: not applicable to this change.**
- **No key in CI secrets, and CI runs in replay: not applicable to this change.**
- **Key file never copied into a worktree: not applicable to this change.**
- **Budget caps enforced and not raised: no finding.** N7 bounds the prompt after escaping, and no cap changed.
- **Cassettes still covered by the secret/PAN scanner: not applicable to this change.**

**Unsafe findings: none.** R1 is the only medium finding. Under the routine-row triage it is inside this row's files, so it should be fixed, followed by a scoped re-review. R3 must be fixed before `make ci`.
