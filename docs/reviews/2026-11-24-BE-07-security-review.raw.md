# BE-07 security review, pass 1 (verbatim)

`security-reviewer`, read-only, on the staged diff against `origin/main` `7296c04` (3,106 lines), 2026-11-24.

---

## Security review: row #26 (BE-07, the Care agent), branch req/BE-07-care, base 7296c04

I read `/tmp/claude-502/northwind-security-BE-07.diff` in full (staged, nothing committed). For context I also read `CLAUDE.md` "Hard rules", decision 0001, the plan, `agents/runtime.py`, `audit.py`, `views.py`, `fakes/_base.py`, `fakes/orders.py`, `northwind_common/cards.py` and `tests/backend_audit/test_be08_views.py`. I ran nothing; I have no Bash.

**Summary: one medium, and it is unsafe (hard rule 1). Everything else is low. Nothing is high or critical.** The injected test transcripts ("SYSTEM: ignore your instructions…") are test data. Nothing in the diff, fixtures or plan tried to steer this review.

---

## Findings

### M1: medium, **unsafe** (hard rule 1). A card number or CVV split across turns with any other turn between them reaches the model
`services/backend/src/northwind_backend/agents/care.py:225-243` (`screen_transcript`), and `prompt()` at `:268` re-screens the same way.

The screen checks each turn, then each run of adjacent turns joined with a space. Joining adjacent turns brings the intervening turn's text along too. Once that text is longer than about 12 characters, the digit groups end up further apart than `cards.py` will join them (`MAX_GROUP_GAP = 12`, `_DIGIT_GROUP` allows one separator). `_LONG_DIGITS` and the CVV gap of 40 characters don't catch it either.

How it fails, which is an ordinary chat pattern:
- turn 0 (shopper): `my card is 4111 1111 1111` (12 digits, so no PAN, no Luhn window, no `_LONG_DIGITS` hit)
- turn 1 (assistant): `I'm an AI assistant. Please don't share card numbers in chat.`
- turn 2 (shopper): `1111, please charge it`

Each turn alone passes. Windows (0,1) and (1,2) add no digits. In window (0,1,2) the last two groups are about 60 characters apart, so nothing triggers. The model gets all 16 digits in one prompt. The same works for a CVV: `my cvv is` / long assistant turn / `737`.

This is the test-writer's F2 again, with one assistant turn added. F2 was treated as a hard-rule-1 failure and fixed. There is a second effect: the planned `backend-care` recording would put such a transcript in a cassette, and the cassette scanner's patterns are narrower than `card_data`, so they would not catch it either.

Suggested fix, which tightens and does not loosen:
- Also screen each role's turns as their own sequence (shopper turns only, in order, then assistant turns only), withholding every turn that contributes.
- Or fail closed on the transcript as a whole: concatenate every standalone digit group in order, ignoring the text between them. If any 13 to 19 digit window passes Luhn, withhold every contributing turn. Treat a security-code name in one turn and a 3 to 4 digit run in another the same way.
- Add the interleaved case to `test_be07_writer.py`.

### L1: low, not unsafe today (hard rule 1, defence in depth). The case queue fake's card check runs on a JSON encoding, which hides split and non-ASCII digits
`services/backend/src/northwind_backend/fakes/cases.py:40`: `self._admit("create_case", idempotency_key, json.dumps(asdict(case)))`

`json.dumps` uses `ensure_ascii=True` by default and escapes newlines. So:
- `4111 1111\n1111 1111` becomes `4111 1111\\n1111 1111`, where the `n` glues to the digits and breaks both group detectors;
- Arabic-Indic or fullwidth digits become `٤…` and never reach `contains_pan`.

`northwind_common/cards.py:100-104` (`argument_texts`) records exactly this lesson (CC-10 re-review M1).

It is not reachable through `run_care` today. The only free text is the summary, which `check_summary` NFKC-normalizes, collapses to one line and card-checks first. Every other field is code-filled and pattern-bound. But the docstring ("Card data anywhere in a case is refused before anything is stored or hashed"), the tool register and your focus item all present this check as a control, and it is the stub for `mcp-cases`, which other callers will reach.

Fix: check the raw leaves, for example `holds_card_data(asdict(case))` from `northwind_common.cards`, or `card_data_in(*argument_texts(asdict(case)))`.

### L2: low, not unsafe (hard rule 2, the summary's money check). The check is narrower than the docs say
`care.py:130-147` (`_MONEY`).

Probes I traced by hand against the regexes all pass the check and land in the case:
- `charged USD: 999`
- `999 in USD`
- `a 999-dollar charge`
- `charged nine hundred dollars`
- `999 MXN` (and any code not in the 8-code list)
- `a 999 charge, twice` (a bare number)

The case's amount fields are still code-only from tools, and the summary is labelled, so hard rule 2 holds for every field staff act on. The implementation matches the plan's definition ("a currency sign or code next to digits, or a decimal amount"). But `tool-register.md` and `data-flow.md` say plainly that "a money figure" is refused, which overstates it. Either widen the check (any ISO-4217-shaped code within a few tokens of digits, a hyphenated currency word, spelled-out numbers next to a currency word) or narrow the governance wording to what is enforced.

### L3: low, not unsafe (hard rule 6 residual, which you're accepting). The link check only knows 26 TLDs, so a bare domain can still point staff somewhere
`care.py:149-156` (`_LINK`).

`process it at northwind-refunds.top` or `pay.ru/claim` passes: no scheme, no `www.`, and a TLD outside the list. An injected transcript can therefore put a phishing address into the staff-facing summary. That falls under the accepted "summary wording" residual, but change 3 of the approved plan ("also refuses URLs") and the tool register say a link or web address is refused. Suggested fix: refuse any `label(.label)+.alpha{2,24}` token, with explicit exceptions for things like `e.g.`/`i.e.`, plus IPv4 dotted quads. That fails closed and costs at most a retry.

### L4: low, not unsafe (hard rule 9 semantics). The fingerprint isn't purely code-filled, and the `not_escalated` key pins the first conversation for good
`services/backend/src/northwind_backend/cases.py:56-69` (`code_fields`), `:121` (`case_key`); `care.py:446-448` (flags).

1. `flags` is in the fingerprint and carries `summary_missing`, which depends on the model. So a first run with no usable summary, which injection can cause by making every submission refused, followed by a clean retry is an `idempotency_conflict`. The result is FAILED and the case can't be completed. `test_be07_units.py` even asserts this (`flagged = replace(draft(), flags=("summary_missing",))`). That contradicts "a different summary under the same key returns the first case", and the docs' "code-filled fields only". The same applies to `total_unavailable` after a transient price outage.
2. For `not_escalated` (and any root cause), a later, different complaint about the same order gets the first case back with the first summary and status COMPLETE. The new conversation never reaches staff, and a hostile first summary is pinned for that key. This follows from your change 1. I'm raising it only so the gate report names it, not to reopen the decision.

Possible fix for (1), with your agreement since it touches your change: drop `summary_missing` from the fingerprinted flags, or let a model summary replace a code placeholder under the same key.

### L5: low, not unsafe (robustness). Screening runs before any length bound, and is quadratic in the number of turns
`care.py:254-265`. `Turn.text` is unbounded, and `screen_transcript` runs on the uncut text across about 190 joined windows for 20 turns, each running `contains_pan`, the CVV regex and NFKC. A few multi-megabyte turns (from the demo file today, or Window B's staff caller later) would tie up the run for a long time. Suggested fix: refuse turns over a hard ceiling (for example 10× `MAX_TURN_CHARS`) at `CareTask` construction. Keep screening before the 2,000-character cut.

### L6: low, **unconfirmed** (hard rule 2 accuracy). Two currencies are folded into one field
`care.py:460`: `currency=order.currency or total_currency`. The catalog total's currency is discarded whenever the order has a payment currency. `fakes/orders.py:268-275` (`_report`) stores whatever currency the payment reported, with no region check. If those two ever differ, the case shows the live-price total under the wrong currency. In the current flows both come from the region, so I could not show it happening. Suggested fix: add a `total_currency` field, or flag `currency_mismatch`.

---

## On the proposed pin change (plan, last bullet)

It is sound in direction:
- Adding `AuditedCaseQueue` to `view_names` tightens the pin. Today any module under `src` could build that view with any allow-list unchecked.
- Admitting the three names in `agents/care.py` only mirrors the approved placement exception.

For it to keep its meaning, three additions:
1. **Pin the values, not just the names.** `test_be08_views.py` pins the values of the placement and read-only sets (`test_the_placement_sets_are_exactly_these`, `test_the_read_only_sets_are_reads`). Nothing there pins `CARE_PRICES == {"price"}` or `CASE_CREATE == {"create_case"}`. `CARE_READ == {"get"}` is only asserted in `tests/care/`. Without value pins, admission by name lets a set grow silently, for example `CASE_CREATE` gaining `assign_case`, which PRD §7 keeps away from shopper-side agents.
2. Optionally pair each constant with its view class (`CARE_READ` with `AuditedOrderStore`, `CARE_PRICES` with `AuditedCatalogPort`, `CASE_CREATE` with `AuditedCaseQueue`).
3. `CASE_CREATE` is a write set, while the test name and the `views.py` docstring (lines 12-16) say agents get read-only sets plus the placement exception. Update the docstring. Also have the user approve `CARE_PRICES` explicitly: PRD §7's Care row lists `create_case`, `add_note` and `get_order`, not a catalog read. It is code-side, not a model tool, but it is still a scope beyond the row, under Care's credential.

None of this weakens the pin.

---

## Checked, no findings

Hard rules:
- **Hard rule 1:** clean apart from M1 and L1. The `CareTask` order ID is checked by pattern and card check. The audit takes the transcript and summary as type and length only (`_ref`/`_shape`; `_audit_tool_use` writes `summary` by reference). `check_summary` refusals never echo the text. The demo prints only the checked summary and code fields, and its errors name no file text. `runtime.py` logs only exception type names.
- **Hard rule 2:** clean apart from L2 and L6. Amounts come only from `seen.order` (the handler's capture) and catalog prices read by code. The model's order view (`_model_view`) holds no amounts, token or address.
- **Hard rule 3:** synthetic data only (sandbox 4111 PAN, `example.test`, fixture IDs). No finding.
- **Hard rule 4:** no secrets, no `.env` access. The demo goes through `run_subagent`, then `agent_options()`, then the proxy. No finding.
- **Hard rule 5:** not applicable to this change (no product claims).
- **Hard rule 6:** clean apart from the L3 and L4 residuals. `get_order` is bound to the task's order by exact compare, and foreign IDs are refused, audited and never reach the store. The root cause comes from the store's status and code, with unknown codes mapped to `agent_requested`. The action comes from the table. The key comes from order and root cause. The transcript is JSON-escaped and labelled. An unknown tool name is not granted (`tools=[]`, `allowed_tools` is exactly two, `dontAsk`).
- **Hard rule 7:** not applicable to this change (staff-facing case, no shopper caller, no internal documents).
- **Hard rule 8:** two model tools. Views `CARE_READ={"get"}`, `CARE_PRICES={"price"}`, `CASE_CREATE={"create_case"}`. No payment, fraud or escalate reachable from `care.py`. `CarePorts` holds orders, catalog and cases only. See the pin comment about `CARE_PRICES` versus PRD §7.
- **Hard rule 9:** the key `care:<order_id>:<root_cause>` uses no model text. `dry_run` is supported. The write retries only when nothing ran (`NOTHING_RAN`). A same-key replay returns the first case. The conflict is `validation`, not retryable. Semantics are in L4.
- **Hard rule 10:** all refusals, read failures, queue outages and conflicts are structured (`isError`, `errorCategory`, `isRetryable`, `message`). An outage (transient, retryable) and an unknown order are told apart (tested).
- **Hard rule 11:** the system prompt says the agent is an AI, and the case labels the summary as written by an AI model. No finding.

Other focus items:
- **BE-08 audit allow-list:** `case_id` (`case_\d{6}`), `root_cause` (`_CODE`), and `flags` accepted only as a list of codes, otherwise shaped. The `Case` outcome has the summary as `_shape` only. `idempotency_key` matches its existing pattern. No transcript or summary text is written. No finding.
- **Determinism:** the prompt has only the order ID and the screened transcript. Tool results carry no correlation or run ID. The correlation ID is only in the audit and the case. No finding.
- **The `RECOMMENDED` move:** `orders_place.py` uses only a keyed lookup (`RECOMMENDED[p.reason or "not_finished"]`, line 790), and `Reason` can't produce `not_escalated`, so the Orders agent's model input and the `backend-replay` cassettes are unchanged. The 12 entries are byte-identical in the diff and pinned by sha256. No finding.

Decision 0001:
- **Child processes get a placeholder key only; only the proxy holds the real key:** unchanged. Care uses `run_subagent`, which calls `agent_options()`. No finding.
- **The Makefile does not export the key:** the Makefile is not in the diff. No finding.
- **No key in CI secrets; CI runs in replay:** `.github/workflows` is not in the diff. No finding.
- **The key file is never copied into a worktree:** nothing in the diff touches key files. No finding.
- **Budget caps enforced and not raised:** no cap touched. `RUN_BUDGET=1` is a per-window value inside the cap. No finding.
- **Cassettes still covered by the secret/PAN scanner:** no cassette added yet, scanner unchanged, `data-flow.md` names cassettes and `.llm-proxy-log` as destinations, and both are scanned. Fix M1 before the `backend-care` recording, because the scanner would not catch an interleaved split PAN.

Relevant files:
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/agents/care.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/fakes/cases.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/cases.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/views.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/audit.py
- /Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/tests/backend_audit/test_be08_views.py
