# Row #12 security review, second scoped re-review (raw, verbatim)

Reviewer: `security-reviewer` subagent, read-only. Diff `/tmp/claude-502/northwind-12-rereview2.diff`, base
`d4e1c4fb13bd220c21115d933b00fcd78485a120`, head `206ac64c8401f9d4d31fb85dc8378172969c80f0`. Authorised by the user as
the second and last scoped re-review. Saved unedited below.

---

Security re-review 2 (scoped, final): row #12, fixes for N1–N9. Diff base d4e1c4f, head 206ac64

Before reviewing I re-read the CLAUDE.md "Hard rules" and decision 0001 from disk. I read the whole fix diff (/tmp/claude-502/northwind-12-rereview2.diff, whose SHA lines match the ones you gave), both earlier raw reports, the 0015 #12 sections, the full current output_check.py, `serve_catalog` in answerers.py, `internal_sources` in contexts.py and the test file. I traced every regex result below by hand; I ran nothing. I am read-only and changed nothing.

## Bottom line
- **Three high findings are open (H1–H3). All three are unsafe under hard rule 2.** None is a regression from this diff, and none sits in a line this diff changed:
  - H1 is in `_asks`, a function the N1/N4/N9 fixes edited, but in its `_FRAME` list and its look-ahead, which the diff left as they were.
  - H2 and H3 are in the `availability` patterns (output_check.py:280 and :398-409), which the diff did not touch.
  - Pass 1 and the first re-review both missed all three.
- **The fix diff adds two regressions against d4e1c4f, both medium:**
  - M1 is in the N6 code (`bare_price`).
  - M2 is in the N3 code (`_NEGATION_CUT`).
- **Width and backtracking:** no pattern is wider than L. By my count the widest new ones are `bare_price` #1 at 60 and `qualified` at 57. No pattern can backtrack catastrophically: every quantifier is bounded, and `qualified`'s two word slots cost about 150 steps per start position.

## Findings, most severe first

### H1. HIGH, unsafe (hard rule 2; 0015 `availability`): an affirmative frame lets the answer ride in the same sentence, or in the next one after "?"
- **Where:** output_check.py:707-710 (`_FRAME`: confirm, tell, know, say, sure) and :755-759 (the look-ahead only refuses `:` and dashes).
- **These pass with no `check_stock`, and also with stock {v:0}:**
  - "I can confirm whether the M is available now, and it is."
  - "I can tell you which sizes are available today, the M and the L."
  - "I can check which sizes are available now (M and L)."
  - "Want to know which size is available now? The M." ("?" cuts the look-ahead.)
  - "Wondering whether the M is available? It is."
- **Why high:** this is pass-1 finding 2's "answer after the match", which was rated high. Only the colon form was closed.
- **Fix (fail closed):**
  - Keep only frames that defer: see, check, show(s), find out, and negated knowledge (`can't say`, `don't know`, `not sure`). Drop affirmative confirm, tell, know and say. All five measured questions still pass.
  - Allow no exemption when the match's sentence ends in "?".
  - Require the rest of the sentence after the match to fit a closed list of tails: empty, "right now", "in <up to 3 words>", or ", and it/the page <verb other than is/are>…", which keeps measured question 4.
- **For you to weigh:** an anaphoric answer ("Yes, it is.") matches no pattern anyway. H1 is still high because here the exemption itself passes a sentence that says "the M is available".

### H2. HIGH, unsafe (hard rule 2; #15 L9): the `_RANGE` narrowing exempts a specific size or colour
- **Where:** :280 and :400/:405, the look-ahead `(?!\s in \s _RANGE)`.
- **These pass with nothing checked, or with M at 0:**
  - "The Summit Shell is available in size M."
  - "It is available in sizes M and L right now."
  - "The M is available in colour Sage today."
- "currently available in size M" is still blocked, by :408.
- `_RANGE` has no `\b`, so "fitted" and "colourways" are excluded as well.
- **Fix:**
  - Exclude only a plural range ("sizes XS–XL", "N sizes", "a range of").
  - Never exclude a singular "size/colour/fit X", and never exclude when a time word (now, today, at the moment, still, only) follows in the sentence.
  - Add `\b` to `_RANGE`.
  - Re-measure. Where the line falls between a range and stock is a scope question for the user.

### H3. HIGH, unsafe (hard rule 2): the availability patterns see only a literal "is" or "are"
- **Where:** :398-407, lookbehinds `(?<=\bis\s)` and `(?<=\bare\s)`.
- **These pass with nothing checked:**
  - "Yes, it's available."
  - "The M's available in Sage."
  - "They're available."
  - "The M remains available."
- Only the "now", "today" and "to order" forms are caught.
- **Fix:**
  - Add the fixed-width lookbehinds `(?<=['’]s\s)`, `(?<=['’]re\s)`, `(?<=\bremains\s)` and `(?<=\bbe\s)`.
  - Let `_OWN_SUBJECT` accept `['’]s` and `['’]re`, and do not count the own subject's "it" or "they" as `_PRONOUN`.

### M1. MEDIUM, unsafe, REGRESSION from this diff (N6 code; hard rule 2)
- **Where:** `bare_price` #1, :377-382. The diff dropped `will{_S}be` and narrowed `{_MK}` (0-4 marks) to `[*_~`]{0,2}`.
- **Both of these were blocked at d4e1c4f and now pass with no `get_price`:**
  - "The price will be 279." and "The cost will be 279."
  - "The price is ***279***."
- **Residual:** a double space before a hedge ("price is  just 279") also misses.
- **Fix:**
  - Restore both. That gives 62 view characters by my count, which is within L.
  - Use `\s{1,3}` inside `_HEDGE`. That is 64 by my count, which is at the limit, so confirm it with the width test.

### M2. MEDIUM, unsafe, REGRESSION from this diff (N3 code; D3 polarity)
- **Where:** :687. `()` in `_NEGATION_CUT` cut a governing negation away from its phrase.
- **What passes:** "The M is not (yet) in stock." and "The M isn't (currently) available." pass at {v:5}, because they are read as positive. d4e1c4f blocked both.
- **The same effect from other cuts:**
  - The dash cut you accepted: "The M is not — for now — in stock."
  - The comma cut, which already existed: "The M is not, sadly, in stock."
- **Fix:** when the text between the last cut and the phrase is only whitespace, and the text before the opening cut ends in a governing-negation shape, return None (unvouched). "No worries — the M is in stock." stays a plain claim.

### M3. MEDIUM, unsafe (hard rule 2; was there before this diff): only one figure of a money range is checked
- **Where:** `live_price`, `qualified`, `bare_price` and `pounds`. Money has nothing like `_JOINED`.
- **These pass with live {(279, USD)}:**
  - "It is 199–279 dollars."
  - "between 250 and 279 dollars"
  - "The price is 279–350."
- "$199–279" passes with live 199.
- **Fix:** a number joined by -, –, —, to, or, or and, before or after the amount, must also be live in the same currency.

### M4. MEDIUM, unsafe (hard rule 2; N2 residual)
- **These pass:**
  - an adjective before a currency the catalog never returns: "about 40,000 Japanese yen", "279 Swiss francs", "2,500 Indian rupees";
  - currency words that are not on the list: kroner, krona, kronor, zł, Rs, Rp, baht, ringgit, Kč, Ft;
  - three words in between: "279 good old Canadian dollars";
  - a comma: "279, in Canadian dollars";
  - "279 (US$)", "279 (dollars)", "279 [USD]";
  - "279 Canadian-dollars" and "279 Canadian (CAD)".
- **Fix:**
  - Add `{_OTHER_CODE}\b` to `qualified`'s last alternative (:362). `qualified_value` already returns None for it, and the width stays 57.
  - Extend `_OTHER_CODE` with the words above.
  - Extend the bracket pattern to `\(\s{0,2}(?:{_CURRENCY_CODE}|[a-z]{0,3}[$£€]|dollars?|euros?)\s{0,2}\)` and to `[...]`.

### M5. MEDIUM, unsafe (hard rule 2; N4 and N1 residual): a clause with no listed pronoun, or "&" in place of "and"
- **Where:** :728-732 and :767-771.
- **These pass with nothing checked:**
  - "Not sure if anyone noticed the M is available now."
  - "Let me know if the jacket fits the M is available now."
  - "I'd say if anyone asks the M is available now."
  - "Check which fits best & the M is available now." ("plus" works the same way.)
- **Fix:** `between` must fully match a short subject (at most 3 words) followed by is/are. Add anyone, someone, everyone, nobody, people, this and that to `_PRONOUN`, and `&` and `plus` to `_AND`.

### M6. MEDIUM, unsafe (hard rule 2; pass-1 F1 residual): bare-price wording outside the closed list
- **These pass:**
  - "The price of the M is 279."
  - "The price for the Summit Shell is 279."
  - "It sells for 279." and "It retails at 279."
  - "Prices start at 249." ("starting at" is logged.)
- **Fix:** the gate pass 1 proposed. Any bare amount in a sentence that holds price, cost, priced, sells, retails or a currency word is money, held until the sentence ends, as `_policy_context` does for durations.

### M7. MEDIUM, unsafe (hard rule 2; F4/N7 residual): a size token between the count and the stock word
- **These pass with nothing checked:**
  - "Only 2 Ms left."
  - "Just one XL left."
  - "There are a few Ms left."
  - "a handful of mediums left"
  - "There's plenty of stock."
- **Fix:** allow one token of up to 8 characters before left, remaining or on hand, keeping the width under L. Add `plenty|lots?|loads|tons` + `of stock`.

### Low
- **L1 (N5 residual):** "Weighing in at 199 pounds, it's a steal." passes as a weight, because "weighs in at" is also a price idiom. Fix: drop `in at` from `_WEIGHT_GOVERNS` (:641), or log it.
- **L2:** double negation. "Nothing is not in stock." passes at {v:0}, because the governing match wins over the earlier "Nothing" (:698). A trailing negation ("in stock nowhere") is never read. Fix: return None if `_NEGATION` also matches before the governing match.
- **L3 (N9; performance, unconfirmed):** the hold has no raw-length cap. Invisible padding keeps `_wide <= 60`, and `_HARD_BOUNDARY.search(self._raw, end)` (:1224) rescans the raw text on every delta. Fix: a 16L raw cap as in `_policy_context`. Past the cap, treat the match as a claim.
- **L4:** the look-ahead stops at any "." (:757). "…available (e.g. today): it is." passes. This is not specific to streaming.
- **L5:** TRIGGER (:432-438) lacks "lot", and the other currency signs and codes start before any trigger. That text is released and then reset. The figure itself stays held by `\d`, so only a flicker shows.
- **L6:** a hedge that changes the claim passes with a live figure. "It costs under 279" and "under $279" pass with live 279.
- **L7:** "It is 1 279 €." passes with live (279, EUR): only "279 €" is read.
- **L8 (documentation):** PROGRESS.md:234 says "'twenty pounds' … passes unless it is live". That is backwards: it is blocked unless live. 0015:195-196 and :232 still list "twenty pounds passes", which is stale since pass 1.
- **Over-blocking, not a security finding:** the N4 rule now blocks "Check whether it's available now." and "…whether they're available…". `_OTHER_CODE` blocks "won 2 awards".

## Part 1: status of N1–N9
- **N1: partly fixed.** "and" now works only through the closed noun phrase, and the three inputs are blocked. `_asks` is stricter than at d4e1c4f. Still open: "&", "plus", and clauses with no conjunction (M5).
- **N2: partly fixed.** The headline forms, "(USD)", and other currencies' signs and codes are fixed. Still open: M4.
- **N3: fixed for the given inputs.** It brings regression M2 (brackets), plus the dash side effect and L2.
- **N4: partly fixed.** Only pronouns are handled; the "any finite verb" part was not done (M5).
- **N5: fixed.** L1 is the residual.
- **N6: partly fixed.** The hedges are in, but regression M1 sits in the same pattern, and M6 remains. "starting at" and "as low as" are logged.
- **N7: fixed for the listed inputs.** M7 and L5 remain.
- **N8: fixed.** The internal-ID check runs before the canary guard and fails closed when it raises. IDs are ASCII. Internal product P0009 is not the prefix of any other 4-digit ID; I did not check document IDs for overlap, which would only over-block.
- **N9: fixed, by reading.** L3 and L4 remain.
- **N10: fixed** (0015:237).

## Part 2: what each exemption or narrowing since 3043f3a lets through

Each item lists inputs that pass with no result or with a disagreeing one.
- **`_FRAME`:** H1. "Want to know which size is available now? The M."
- **`_CLAUSE_BREAK`:** M5 ("&", "plus"). An Arabic comma "،" is not folded and does not break (low). Otherwise none found.
- **`_NOUN_PHRASE`, the "which … and" rule:** in a question shape, none found. A comma or "and" tail after it passes: "I can show which sizes and colours are available now, all of them." (H1).
- **`_PRONOUN` / `_OWN_SUBJECT`:** M5.
- **Colon/dash look-ahead:** H1 (comma, "and", bracket, "?") and L4.
- **N9 hold:** none found for timing; L3 only.
- **Dash clause end in `_negated`:** "The M is not — for now — in stock." passes at {v:5}. Brackets do the same (M2, regression).
- **`pounds` weight exemption:** L1. With live (199, USD), "199 pounds" is blocked. Otherwise none found.
- **`bare_price`:** M3 ("The price is 199–279." with live 199) and L6. Pattern gaps: M1 and M6. The any-currency match was reviewed as harmless, since live values are scoped to the region.
- **`qualified`:** M3 ("199–279 Canadian dollars" with live (279, CAD)). With a disagreeing currency, none found; the tests hold that.
- **`live_price`:** M3, L6, L7. "279 dollars (Canadian)" passes with live (279, USD) under the "dollars means any dollar" rule (low).
- **`live_stock`:** with nothing checked, none found. Otherwise M2, L2, "3 to 5 left" (only 5 is read), and the logged change-6 and F8 residuals.
- **`availability`:** H1, H2, H3 and M5.
- **Not caused by any exemption:** anaphoric answers ("Is the M in stock?" / "Yes, it is.") match no pattern.

## Checked, no findings
- **Hard rule 1:** not applicable to this change. No card path changed, and the guard still uses `scan_text`.
- **Hard rule 2:** findings above.
- **Hard rule 3:** synthetic values only (P0011, b"k"*40, a fixture ID).
- **Hard rule 4:** no secret handling changed. The eval still uses `secrets.token_bytes(40)`.
- **Hard rule 5:** `qualified` in `LIVE_EXEMPTIONS` is consistent with change 3. F7's remaining part is logged.
- **Hard rule 6:** a leak is replaced by the fixed envelope.
- **Hard rule 7:** N8 is fixed.
- **Hard rule 8:** not applicable to this change.
- **Hard rule 9:** not applicable to this change.
- **Hard rule 10:** the replacement is the structured `tool_failed` envelope.
- **Hard rule 11:** not applicable to this change.
- **0001, placeholder key / only the proxy holds the real key:** `llm.py` and `agent_runtime` are not in the diff.
- **0001, Makefile does not export the key:** the Makefile is not in the diff.
- **0001, no key in CI secrets, CI runs in replay:** no workflow is in the diff.
- **0001, key file never copied into a worktree:** nothing copies `LLM_ENV_FILE` or `.env`.
- **0001, budget caps:** not touched.
- **0001, cassettes scanned:** no cassette was added, and the scanner and its paths are unchanged.
- **Prompt injection:** no text in the diff, the tests or the copied reviews tries to steer the review.

## Could not confirm without running code
- That the tests pass, and the failing-first log.
- Every regex trace above.
- My width counts (60, 57, 62 if M1 is restored, 64 with `\s{1,3}` hedges) against the real `getwidth` test.
- L3's actual cost.
- How often the model writes the H1–H3 phrasings.
- Whether internal document IDs overlap other IDs as substrings.

Files: /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/answerers.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/contexts.py, /Users/rapido/Projects/northwind-shop/services/assistant/tests/test_rag09_security_fixes.py, /Users/rapido/Projects/northwind-shop/evals/tests/test_rag09_live_sources.py, /Users/rapido/Projects/northwind-shop/docs/decisions/0015-output-control.md, /Users/rapido/Projects/northwind-shop/PROGRESS.md
