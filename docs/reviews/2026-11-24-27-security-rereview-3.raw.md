# Row #27 security review, scoped re-review of option (B) (raw, verbatim from security-reviewer)

Diff `/tmp/claude-502/northwind-review-27-option-b.diff`, base 72aef0b, head 57635e2.

## Row #27, scoped re-review of option (B) (diff /tmp/claude-502/northwind-review-27-option-b.diff, base 72aef0b, head 57635e2)

**Result:** N1-R is closed, and so are N1, M1, L1, L2, N3 and N6. They are moot because the code they were about is gone. Nothing new is medium or above. Three lows are open; none is high or critical, so stop 10 does not apply.

One limit: I have no git, so I could not run `git diff origin/main -- services/assistant/src/northwind_assistant/output_check.py tools.py` myself. The diff's hunks undo exactly what the exemption added: the import, the `subj`/`verb` named groups, the `live_order` exempt, `order_vouched`, `_BOUND`, `VOUCHED_VERBS`, `LiveValues.orders` and `LIVE_EXEMPTIONS`. A grep finds no leftovers. Byte-identity with main is still **unconfirmed** until someone runs that command.

### Findings

**L1 (low): four lookalike cases still use S0031's order, so they no longer test anything**
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/mcp-orders/tests/test_chk06_tw_edges.py:54, 55, 60, 65`
- **Rule:** CHK-06/NFR-01 "same bytes" net (hard rule 10's not-found versus access distinction).
- **What is wrong:** These cases still name O000064: `"O００００６４"`, `"Ｏ000064"`, `"O 000064"` and `"O٠٠٠٠٦٤"`. O000064 belongs to S0031, not the new demo shopper S0188. Another shopper's order already gets the same `not_found` bytes, so these cases pass even if the parser folds the lookalike to a valid ID.
- **How it fails:** Suppose a future change makes the ID parser fold fullwidth digits (NFKC) or Arabic-Indic digits. `"O００００６４"` then becomes O000064, which still returns `not_found`, and the test stays green. The same change with `"O０００８００"` would return S0188's order. Line 53 was updated; these four were missed.
- **Fix:** use the O000800 forms: `"O０００８００"`, `"Ｏ000800"`, `"O 000800"`, `"O٠٠٠٨٠٠"`.

**L2 (low): the new option-B test never checks that `get_order` succeeded, and its failing-first run used different inputs**
- **Where:** `/Users/rapido/Projects/northwind-shop-b/services/assistant/tests/test_chk06_orders_attached.py:600-618` (`test_b_a_confirmed_or_placed_claim_stays_blocked_on_a_signed_in_turn`) and `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-24-27-failing-first.log:121-126`
- **What is wrong (test):** The test asserts only that the claim is blocked. It never asserts that the tool result was a success for O000800. If O000800 returned `not_found` (a fixture or demo-shopper change), the claim would be blocked for the wrong reason and the test would still pass. A reintroduced exemption would then go unnoticed.
- **What is wrong (evidence):** The failing-first entries are for `O000064: …` and "16 October". At 72aef0b the demo shopper was S0031, so the test as committed (O000800, "20 October") is not the one shown failing.
- **Fix:** add the same check the on-hold test has: `order_tool.unlabel(tool_results(llm.requests[1])[0])[1]["status"] == "delivered"`. Optionally record a failing-first run of the committed parametrization.

**L3 (low, already present before this diff; doc accuracy): order statuses other than "confirmed/placed", and past dates, are controlled only by the prompt**
- **Where:** `/Users/rapido/Projects/northwind-shop-b/docs/governance/threat-model.md:111`, `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py:150, 348-359`, `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/prompt.py:81-84`
- **Rule:** hard rule 2 (order state and delivery dates only from live tools).
- **What is wrong:**
  - The threat-model row's threat includes "relays a status the tool did not return". Its mitigations cover only the confirmation class, and it carries no **Open:** marker.
  - `output_check.py` has no pattern for delivered, in transit or a delivery date. Since option B, nothing reads `get_order` results at all (`turn.py:172-174`).
- **How it fails:** S0188 asks about O000928, which is in transit. The model writes "O000928 was delivered on 26 October." That passes the check unchanged, whether or not `get_order` was called.
- **Why low:** the same was true at 72aef0b, because the exemption only covered placed/confirmed. Option B's `ORDERS_RULES` now explicitly asks for this unchecked phrasing.
- **Fix:** mark the row **Open:** with a named owner in "Owners", as the row #16 cart-write row does. Any later deterministic check must not bring back an exemption to the confirmation class (D7 in the 27b draft already says so).

### N1-R and earlier findings: confirmed moot
- `order_vouched`, `_BOUND`, `_SENTENCE_BREAK`, `_ORDER_ID`, `VOUCHED_VERBS`, the `live_order` exempt and `LiveValues.orders` are all gone. A grep over `services/`, `apps/`, `tools/`, `evals/src` and `tests/` finds no reference outside docs and review files.
- The conditional branch is back to `return bool(introduced) and copula in PRESENT_COPULAS`. `"order confirmed"` has no exempt.

### Nothing still reads order status into the output check
- `turn._LIVE_TOOLS` (`turn.py:172-174`) contains only the price, stock and cart tools, so `get_order` results never reach `record_live`.
- `tools.record_live` has no order branch, and `tools.py` no longer imports `order_tool`.
- `order_tool.order_status` is deleted. `CONFIRMED_STATUSES` is now used only by `services/common/tests/test_chk06_shopper_claim.py:149` and `services/mcp-orders/tests/test_chk06_get_order.py:25,162`.

### Anonymous request is still byte-identical
- `prompt.system_prompt` returns the unchanged text when `signed_in` is false (`prompt.py:120-121`). The four region hashes are still pinned (`test_chk06_orders_attached.py:55-60, 122-125`).
- The `get_order` description and schema example changes (`services/mcp-orders/src/northwind_mcp_orders/tools.py:86, 94`) only reach signed-in turns.
- No `cassettes/assistant-orders/` exists yet, so no recorded request goes stale.

### S0188 switch is clean
- `data/backend/shoppers.jsonl:188`: region US, fictional name and city, an `example.com` email and postal code `US-3391`.
- Its eleven orders in `data/backend/orders.jsonl` (O000267 … O000928) all use `tok_sandbox_ok_0188`.
- The constant changed only in `services/gateway/src/northwind_gateway/signin.py:30` and `services/mcp-orders/src/northwind_mcp_orders/testing.py:26`. The tests checking the shopper's region on file and the bound cookie were updated.
- The S0031 tokens left in `services/common/tests/test_chk06_shopper_claim.py` are token-only tests and do not depend on the demo shopper.

### Checked, no findings
- **Hard rule 1:** no finding. The card-data refusal tests (`test_chk06_get_order.py:338-345`) only had their IDs changed.
- **Hard rule 2:** no new finding beyond L3, which was already there. N1-R is moot.
- **Hard rule 3:** no finding. S0188 and its orders are synthetic, with sandbox tokens.
- **Hard rule 4:** no finding. No secret, key or `.env` is touched.
- **Hard rule 5:** not applicable to this change.
- **Hard rule 6:** no finding. `<order_data>` labelling is unchanged, and the new prompt clause grants no tool.
- **Hard rule 7:** not applicable to this change.
- **Hard rule 8:** no finding. The scopes are unchanged and `get_order` is still the assistant's only orders tool. The 27b draft's `request_return` (D6) is docs only; it will need its decision record before any code.
- **Hard rule 9:** not applicable to this change (no state-changing tool).
- **Hard rule 10:** no finding in code. See L1 for test strength.
- **Hard rule 11:** no finding. The disclosure line in `SYSTEM_PROMPT` is unchanged.
- **0001, placeholder key only in child processes:** not applicable to this change.
- **0001, Makefile does not export the key:** not applicable (Makefile not in the diff).
- **0001, no key in CI secrets, CI runs in replay:** not applicable (no workflow change).
- **0001, key file never copied into a worktree:** not applicable.
- **0001, budget caps enforced and not raised:** not applicable. The 27b draft proposes `RUN_BUDGET=1` inside a window and does not touch the $90 cap.
- **0001, cassettes still covered by the scanner:** not applicable. No cassette was added or changed, and the scanner is untouched.

No text in the diff, docs or fixtures tried to steer the review.
