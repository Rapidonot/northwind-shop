# BE-07 security re-review, scoped, pass 2 (verbatim)

`security-reviewer`, read-only, on the fixes for pass 1 (`$TMPDIR/northwind-security-BE-07-fixes.diff`, 481 lines), 2026-11-24.

---

## Scoped security re-review: row #26 (BE-07, Care agent), branch req/BE-07-care, pass 2

**Bottom line:** M1 is not fully closed, so a medium remains open.
- **N1 (medium, unsafe, hard rule 1):** a withheld turn acts as a barrier. I hand-traced a transcript where the full PAN still reaches the model.
- **N2 (medium, not unsafe):** the new digit pass over-withholds ordinary transcripts that contain dates. I traced a 3-turn example that loses 2 of its 3 turns.
- L1, L2, L3, L5 and L6 are closed. Each leaves a small low residual, listed below. Nothing is high or critical.

**What I read:**
- CLAUDE.md "Hard rules" and decision 0001.
- The pass 1 report and `/tmp/claude-502/northwind-security-BE-07-fixes.diff` (all 481 lines).
- In full, current versions: `agents/care.py`, `fakes/cases.py`, `views.py` and `northwind_common/cards.py`.
- The relevant parts of `audit.py`, `fakes/_base.py`, `fakes/orders.py`, `care/__main__.py` and `tests/care/_driver.py`.

I ran nothing (I have no Bash). The Luhn results below are hand-computed, not executed. Nothing in the diff or the test data tried to steer this review.

---

## Findings

### N1: medium, unsafe (hard rule 1). A withheld turn between two PAN fragments blocks every window, and any number in between also defeats the digit pass
`services/backend/src/northwind_backend/agents/care.py:239-240` (`if any(i in withheld for i in window): break`) and `:261-270` (the digit pass joins only contiguous runs).

**How the screen fails:**
- `_windows` stops at a withheld turn instead of joining across it. The withheld turn's text is not shown, so the model sees the fragments on either side as neighbours.
- The digit pass joins only contiguous runs, so one standalone number anywhere between the fragments, in any turn, breaks it.
- The re-screen in `prompt()` (`:318`) does not help. The 26-character placeholder puts the fragments 28 characters apart, beyond `MAX_GROUP_GAP = 12`.

**Hand-traced failure** (Stripe sandbox PAN 4242 4242 4242 4242):
```
0 shopper:   "card 4242 4242 4242"
1 shopper:   "cvv 123"                       -> withheld on its own
2 assistant: "I'm an AI assistant and can't take card details here. Your order has 3 items."
3 shopper:   "4242"
```
- **Adjacent and shopper-role windows:** every window starting at 0 or 1 contains turn 1, so it stops.
- **Window [2,3]:** holds only "3" and "4242".
- **Digit pass, runs:** 4242, 4242, 4242 | 3 | 4242.
- **Digit pass, candidates:** `4242424242423` (Luhn sum 51), `42424242424234242` (71) and `4242424234242` (55). None passes.
- **`prompt()`:** the JSON holds "card 4242 4242 4242" and "4242", so the whole PAN is in the model context, and in the `backend-care` cassette if this is recorded.
- **Why this counts as a regression:** a single shopper turn with the same content would be withheld whole (it contains the CVV). The multi-turn screen is weaker here than the single-turn one.

**Fix (tightening only):**
- Run `_windows` over the shown turns: skip withheld indices instead of stopping at them, and repeat until nothing more is withheld.
- Also run `_split_numbers` on each role's runs alone. This closes the trace above and N3, because shopper-only runs are 4242 ×4.
- Add the trace above and N3's trace to `test_be07_units.py`.

### N2: medium, not unsafe (fail-closed; hurts what BE-07 is for). The digit pass withholds ordinary turns that contain dates
`care.py:249-271`.

**Why it happens:**
- The pass counts runs of any length, including 1 and 2 digits, and ignores the gap between them. That drops both controls `cards.py` uses to keep "10, 12, 14" from joining up (`MIN_GROUP_DIGITS`, `MAX_GROUP_GAP`, `cards.py:20-26`).
- Each candidate window has about a 1 in 10 chance of passing Luhn. A date contributes three runs.

**Hand-traced example:**
```
0 shopper:   "I ordered 2 jackets on 2026-11-02 and only 1 arrived."
1 assistant: "I'm an AI assistant. The order shows 2 items shipped on 2026-11-04."
2 shopper:   "Only 1 came, size 10."
```
- There are 12 candidate windows.
- One of them is `2026`+`11`+`02`+`1` | `2`+`2026`+`11` = `2026110212202611` (16 digits, owners {0,1}). Its Luhn sum is 40, so it is valid.
- Turns 0 and 1 are withheld, and the model sees only "Only 1 came, size 10."
- A 10–20-turn transcript with several dates will very likely lose most of its turns. The rate is my estimate, not measured.
- The case still has its code-filled fields, so staff can act. But the summary, which is the model's only job, is built on a fraction of the conversation. Nothing tells staff that turns were withheld.
- **Why the tests miss it:** `plain_transcript()` has no digits. `test_m1_ordinary_numbers_across_turns_are_kept` passes, but it is not representative.

**Fix:** I am not proposing a narrower digit pass; changing what it counts is a check change for the user (stop 5). Instead:
- Add a number-rich realistic transcript test, such as the one above, so the rate is visible.
- Add a code-filled flag such as `transcript_partial`, so staff know the summary rests on a partial transcript. Keep it out of the fingerprint because of L4.
- Take the trade-off to the user.

### N3: low, unsafe in effect but no weaker than the single-turn screen (hard rule 1). An interleaved number defeats the digit pass when the same-role gap is over 12 characters
`care.py:261-270`.
```
0 shopper:   "my card is 4242 4242 4242"
1 assistant: "I'm an AI assistant; please don't share card numbers. Your order O000123 ships 2026-11-28."
2 shopper:   "the last bit is 4242"
```
- **Shopper role window:** the gap is " the last bit is " (17 characters, over 12), so it misses.
- **Digit pass:** the date runs break the sequence. All five candidates fail Luhn: 74, 77, 57, 69 and 69. The PAN reaches the model.
- The same text written in one turn is also missed by `cards.py` (gap over 12), so this is the shared net's limit, not a regression.
- The docstring at `:250-253` ("whatever lies between") overstates what the pass does.
- The per-role digit pass in N1's fix closes this.

**The other bypasses you asked about:**
- **A fragment glued to letters** ("1111x"): it misses the digit pass. Role and adjacent windows still catch it when it is within 12 characters. Beyond that, the single-turn detector misses it too, as `cards.py` documents ("broken up by letters"). Low, not a regression.
- **A split mid-group into 1–2 digit runs:** caught. The digit pass has no minimum run length, and role windows rejoin the group.
- **A CVV split across roles:**
  - Name in one shopper turn, digits in another: caught by the role window.
  - Name in an assistant turn, digits in a shopper turn: caught by the adjacent window when the gap is 40 characters or less. Beyond 40 it is missed, which matches the single-turn screen. Low.
  - A CVV around a withheld turn ("my cvv is" / full PAN / "737") is saved by the `prompt()` re-screen only while the gap is 40 or less. With "and it is 737" in the last turn it is 41, so it is missed. This is part of N1.
- **Arabic-Indic digits:** handled. Python's `\d` matches every Unicode decimal digit and `int()` reads them in `luhn_ok`. This holds in `_DIGIT_RUN`, `_DIGIT_GROUP` and `_CVV_DIGITS`. Fullwidth, mathematical, circled and superscript digits are handled through NFKC.
- **Pre-existing, outside this diff, unconfirmed by a run:** single digits separated by two spaces ("4  2  4  2 …", 16 digits) in one turn pass `contains_pan` and `_LONG_DIGITS` (`cards.py:26,68`). The digit pass needs two or more turns, so it doesn't help. Low; it needs deliberate obfuscation. For the `cards.py` owner.

### N4: low, not unsafe (L2 side effect). The case-insensitive code `TRY` refuses ordinary English
`care.py:141`.
- With `re.I`, "The shopper wants to try 2 other sizes" and "after 2 try-ons" match as money (`summary_has_money`).
- The fixed message ("a money amount") gives the model nothing to fix. A second refusal leaves the case with `summary_missing` and `NO_SUMMARY`.
- I don't recommend narrowing the match; that is the user's call.
- A safe improvement: a fixed refusal message that names the class ("a number next to a currency word or code such as 'try'"). It echoes nothing.

### N5: low, unconfirmed (L5 residual). NFKC expansion multiplies the bounded input
`care.py:308-309` bounds raw characters only.
- U+FDFA expands 18× under NFKC. So 20 turns × 20,000 characters becomes about 7M characters per full join.
- Each of the roughly 280 windows normalizes and scans its joined text several times in `_has_card`. My estimate is minutes of CPU, not timed.
- This is still far better than pass 1, where turn length was unbounded.
- **Fix:** also refuse a turn whose NFKC form exceeds the ceiling.

### N6: low, not reachable today (L1 residual). Card data split across case fields isn't checked
`fakes/cases.py:64-69`.
- The check runs per leaf, raw and NFKC. It drops the cross-leaf join that `holds_card_data` adds (`cards.py:119`).
- Only `summary` is free text, so `run_care` can't reach this. The docstring ("anywhere in a case") and the future `mcp-cases` callers make it worth adding.
- **Fix:** add `or holds_card_data(asdict(case))`.

### N7: low (L3 residual, falls under the accepted summary-wording residual of hard rule 6)
`care.py:158`.
- NFKC leaves U+3002 (`。`) alone, so "northwind-refunds。top" passes. IDNA treats U+3002 as a dot.
- A non-ASCII top-level domain ("pay.рф") also passes, because `[a-z]` is ASCII only.
- `tool-register.md` says "any web address".

### N8: low, documentation only (L2)
`docs/governance/tool-register.md` and `data-flow.md` say "a currency code". What is enforced is a list of 32 codes; ARS, COP, NGN, UAH and others pass. Either say "one of 32 listed codes" or widen the list.

### N9: low, not a security issue
`services/backend/src/northwind_backend/care/__main__.py:76`: the error message doesn't mention the new 20,000-character turn limit. It echoes no text, so hard rule 1 holds.

---

## Fix-by-fix status
- **M1:** narrowed, not closed. The original trace and its variants without an interleaved number are now withheld. N1 (medium, unsafe) and N3 (low) remain.
- **L1:** closed. The check runs on every raw leaf, raw and NFKC, plus `card_data_in` (with `_LONG_DIGITS`), before hashing. N6 is the cross-leaf residual.
- **L2:** closed as a docs and enforcement mismatch. Every probe from pass 1 is refused, except spelled-out and bare numbers, which the docs now state. N4 and N8 are residuals.
- **L3:** closed. Scheme, `www.`, any dotted name ending in two or more letters, and dotted quads are refused. "e.g.", "a.m.", "U.S." and dotted dates still pass. N7 is the residual.
- **L5:** closed for raw length. N5 is the NFKC residual.
- **L6:** closed.
  - On a currency mismatch the flags are `currency_mismatch` and `total_unavailable`, and the total is None.
  - `authorized_minor` and `captured_minor` both come from the payment (`fakes/orders.py:271-274`), so they match `currency`.
- **L4 and the pin change:** not reviewed again; waiting on the user.

**Regression checks:**
- **Catastrophic backtracking:** none. The new `_MONEY` and `_LINK` patterns run only on the summary after the 600-character bound, so the worst case is about 10^5 steps. `_DIGIT_RUN` is linear, and `_split_numbers` stops at 19 digits for each start.
- **Ordering in the fake:** a case with card data and a bad key now returns `invalid_idempotency_key` instead of `card_data_refused`. Nothing is stored or hashed either way. No finding.

---

## Checked, no findings
- **Hard rule 1:** no regression in the audit, the demo or the refusals. Open items are N1 and N3; N6 is not reachable.
- **Hard rule 2:** no regression. Amounts are still code-only. L6 removes the wrong-currency total.
- **Hard rule 3:** no finding. The test data uses sandbox PANs 4111 and 4242 and fixture IDs only.
- **Hard rule 4:** no finding. No secrets and no `.env` access in the diff.
- **Hard rule 5:** not applicable to this change.
- **Hard rule 6:** no regression. The labelling, the order binding and the tool list are unchanged. N7 is within the accepted residual.
- **Hard rule 7:** not applicable to this change.
- **Hard rule 8:** no finding. Only the `views.py` docstring changed. The sets are unchanged (`get`, `price`, `create_case`), and they are now value-pinned in `test_be07_units.py`.
- **Hard rule 9:** no regression. The key is unchanged and `dry_run` is unchanged. `currency_mismatch` is deterministic. Fingerprint semantics are still L4, which is pending.
- **Hard rule 10:** no finding. The fake returns a structured `CARD_DATA_REFUSED` (validation). The `CareTask` length refusal is a constructor `ValueError` that echoes no text, the same as the existing turn-count bound.
- **Hard rule 11:** no finding. Nothing changed.
- **Decision 0001, child processes get a placeholder key only and only the proxy holds the real key:** unchanged. Care still goes through `run_subagent`, which calls `agent_options()`.
- **Decision 0001, the Makefile does not export the key:** the Makefile is not in the diff.
- **Decision 0001, no key in CI secrets and CI runs in replay:** `.github/workflows` is not in the diff.
- **Decision 0001, the key file is never copied into a worktree:** nothing in the diff touches key files.
- **Decision 0001, budget caps enforced and not raised:** no cap touched.
- **Decision 0001, cassettes still covered by the secret/PAN scanner:** the scanner is unchanged and no cassette was added. Fix N1 and N3 before the `backend-care` recording: the scanner's narrower patterns would not catch these split PANs either.

**Relevant files:**
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/agents/care.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/fakes/cases.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/views.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/care/__main__.py
- /Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/tests/care/test_be07_units.py
- /Users/rapido/Projects/northwind-shop-b/docs/governance/tool-register.md
- /Users/rapido/Projects/northwind-shop-b/docs/governance/data-flow.md
