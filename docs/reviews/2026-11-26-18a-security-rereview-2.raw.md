# Row 18a: security-reviewer's second scoped re-review, verbatim (base 1209446, head 418614f)

Saved as returned. Session notes at the end.

---

## Second scoped security re-review, row 18a (base 1209446, head 418614f)

**What I read.**
- The diff `/tmp/claude-502/northwind-18a-security-scoped2.diff`. Its SHA lines match the ones you gave.
- CLAUDE.md "Hard rules" and decision 0001.
- My two earlier passes: `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-security-review.raw.md` and `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-security-rereview.raw.md`.
- The changed code in context:
  - `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py`
  - `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scan.py`
  - `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/cassette.py`
  - `/Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py`
  - `/Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner.py`

I ran nothing. Every outcome below is traced by hand.

**Stop 9: the "restart or cut hides a negation" class (M1, then N1) is still open.** Your four N1 inputs are closed. The class is not:
- "None of the sizes S, M, and L are in stock." with a=5 is shown. So is "None of the Slate, Charcoal, and Moss towels are in stock." The Oxford comma before "and" is taken as a restart.
- "None of the Bath towels in sizes S - M are in stock." with a=5 is shown. The spaced hyphen is a restart.
- "None of the jackets in UK 8 – 12 is available in three colours." with nothing checked is shown. The spaced en dash is a restart.
- This fix also widens the class: see R2, a regression.

### 1. Does each fix close its finding?
- **N1: closed for its four inputs, not for the class.** The unspaced en and figure dash, the colon before a non-subject, and a dash with `started` False are all handled, which closes the reach case. Still open: R1 and R2.
- **N2: closed.**
  - `_PAN_GROUPED_OVERLAPPING` tries every start, with the same guards.
  - It finds everything `_PAN_GROUPED_WIDE` did. At each start the alternation picks the same single candidate, so nothing found before is lost.
  - The across-request grouped case is covered by count subtraction.
- **N3 a, b, c: closed** for cards. With no trim, (a) and (b) are handled by count subtraction, and (c) goes back to the untrimmed carry. `put` and `_scan_cassettes` both pass `carried()` the same way. For key kinds there is a new hole (R5).
- **N4: closed.** "Security code 7, 3, 7 - 2, 4" and "CVV 1 - 2 - 7, 3, 7" both match the added alternative. A union only adds matches. Unconfirmed: that the alternative is byte-for-byte the pre-L5 form. I cannot diff against 28e07ef.
- **N5: closed** for "U.S. stores" and "No. 5". Two gaps remain (R3).
- **N6: closed for the six listed wordings.** One new alternative cannot match, and close variants are still shown (R4).
- **N7: closed by blocking.** "There's nothing left to add." and "Nothing left me cold." are now blocked both ways. This is over-blocking only, not unsafe.
- **N8: partly closed.** My three inputs now fail closed. A confirmation phrase beside an unlisted denial still passes (R6).
- **N9: closed.** `\\+` matches 7 backslashes, and there is no backtracking risk.
- **N10: closed.** The test now fails if the claim's word is in any piece, whether or not it is later taken back.

### 2. Is anything blocked or found at 1209446 now shown or lost?
- **output_check: one regression (R2).**
  - Every other change reads more of the sentence or blocks more:
    - The colon and dash restarts were removed or narrowed. A removed restart can only make the loop take an earlier one, or none, so it reads a superset.
    - Fewer sentence ends mean a longer sentence, or `started` False.
    - "Nothing left" lost its lookahead.
    - `_NEGATIVE` gained "out of".
  - Moving to the case-kept text is neutral: the sentence is casefolded before any pattern runs.
  - The new `\.(?=\s*$)` end matters only when a "." directly touches the phrase, where a new sentence is the right reading.
- **scan.py: none for cards.** One finding the old subtraction caught is now lost for a key kind (R5, part of question 3).
- **cards.py: none.**

### 3. Can count subtraction hide a finding the old kind subtraction caught?
- **Card numbers (`luhn-valid-pan`): no.**
  - These are counted per occurrence.
  - For the old code to report, the trimmed carry held no such finding and the cassette's own scans found none.
  - So the only things subtracted are cut artifacts:
    - at the carry's left edge, which also sits at the start of the join;
    - at the head's right edge, 230 or more characters from the seam, which also sits at the end of the join.
  - The join keeps both, plus the seam card.
- **Key-pattern kinds: yes, in one contrived alignment (R5).**
  - `_KEY_PATTERNS` count lines, not matches (`pattern.search(line)`).
  - A single-line carry that starts with a cut artifact of the same kind holds that kind on the very line where the seam secret lands. The join then counts 1, the parts count 1, and 1 - 1 = 0.
- **Canaries and key fragments: no.** They are substring matches, so a cut cannot create one, and `put` never carries a dirty cassette.

---

### Findings

**Critical:** none.

**High:** none.

**Medium**

**R1. The M1/N1 class is still open: an Oxford comma or a spaced range dash restarts the clause and hides "None of …". Pre-existing at 1209446 and 28e07ef. My last pass missed the comma case.**
- Where: `output_check.py:899-903`. The comma-conjunction restart is at 901, and `\s[-‐‑]\s|[—―]|(?<![^\W_])[‒–]|[‒–](?![^\W_])` at 900. They are used at 933-941.
- Rule: hard rule 2 / RAG-09.
- Unsafe: yes, on a protected path (`services/assistant/**`).
- Inputs and outcomes:
  - **"None of the sizes S, M, and L are in stock." with a=5: shown.**
    - `_negated` cuts at the last comma and reads " and l are ", so the result is False.
    - In the sentence, ", and " is the last restart and nothing after it spoils it, so only "l are " is read and the claim is vouched.
    - "None of the Slate, Charcoal, and Moss towels are in stock." is the same.
  - **"None of the Bath towels in sizes S - M are in stock." with a=5: shown.** The restart is `\s-\s`, and only "m are " is read.
  - **"None of the jackets in UK 8 – 12 is available in three colours." with nothing checked: shown.** A spaced en dash is a restart, and `offered_range` exempts the claim with negated False. This is the M-B worst case.
  - "None of the towels in sizes S—M are in stock." with a=5 is shown. A bare em dash always restarts.
- Fix (only blocks more):
  - Let ", and/so/yet" restart only before a subject word, as the colon now does. This blocks "…, but the M is available" style sentences, which is the safe side, but check row #30's pins.
  - Do not let a single spaced hyphen, en dash or em dash restart when the sentence opens with a negating quantifier ("none of", "neither", "no", "not one").
  - Or, more simply: when a negation opens the sentence, no restart makes the claim vouched, and the claim stays unvouched (None).

**R2. Regression: adding "he" and "she" to `_SUBJECT_WORD` adds comma restarts that hide a negation.**
- Where: `output_check.py:895`, used at 902.
- Rule: hard rule 2.
- Unsafe: yes, on a protected path.
- Input: "None of the jackets you, he or she tried on is in stock." with a=5.
  - At 1209446 it was blocked: ", he" was no restart, so the whole sentence was read, "none" was found, and the result was None.
  - Now it is shown: ", he" restarts, and " or she tried on is " holds no negation.
- The class is pre-existing with "you", "we" and "i": "None of the jackets he, you or I tried on is in stock." is shown at both heads.
- Fix: drop he/she from the comma alternative, or apply R1's rule that a sentence opened by a negation makes no restart vouch the claim.

**Low**

**R3. N5's residuals.**
- Where: `output_check.py:884`.
- Rule: hard rule 2.
- Unsafe: yes, on a protected path.
- Two spaces after the full stop:
  - `\s+` backtracks to one space, and the second space satisfies `(?![a-z0-9])`.
  - "None of the U.S.  stores has the M in stock." with a=5 is shown, as at 1209446.
- When the phrase directly follows the full stop, `before` ends at ". ", so the lower-case word after it cannot be seen and the sentence ends.
  - Example: "None of the colours sold in the U.S. available in three sizes…". This one is contrived and unconfirmed as natural.
- Fix (only blocks more):
  - `\.(?=\s+[^\sa-z0-9]|\s*$)`.
  - Pass the phrase's first exact character so the lookahead can see it.

**R4. N6: one new alternative cannot match, and close wordings are still shown with nothing checked.**
- Where: `output_check.py:525-532` and `497-500`.
- Rule: hard rule 2.
- Unsafe: yes, on a protected path.
- At 526, `(?:we|they){_S}(?:…|(?:['’]ve){_S}not)` requires whitespace before "'ve", so it can never match.
  - "We've not run out of the M." matches no pattern and is shown. Pattern 512 needs "run" right after "we've".
- "It isn't gone." and "They aren't gone." are shown. At 531, "isn't" has no `{_S}` between "is" and "n't".
- "There are hardly any of the M left." is shown. At 498, "of" takes only them/those/these.
- "Sorry, we're out." is shown. At 521, "of" is required.
- Fix:
  - `\b(?:we|they)(?:{_S}(?:have|had|did)n['’]t|{_S}(?:have|had|did){_S}not|['’]ve{_S}not)`.
  - `(?:is|are)n['’]t` in 531.
  - Allow any short noun phrase after "any of".
  - Allow an end of clause after "out".
- Every one of these blocks more.

**R5. Count subtraction counts key kinds per line, so it can hide a split key that 1209446 reported. A regression, contrived.**
- Where: `scan.py:313-328`, with the per-line `pattern.search(line)` at 129-131.
- Rule: hard rule 4, decision 0001 §3 (scanner).
- Unsafe: yes, on a protected path (the scanner, runner-sync).
- Input:
  - The earlier call's output is one line. Exactly 256 characters from its end it holds "X" + "sk-" + 22 × "A", and it ends with " sk-abcdefghij". It is clean: `(?<![A-Za-z0-9])` rejects "Xsk-", and the tail is under 20 characters.
  - The next call starts "klmnopqrstuvwxyz0123 ok".
- Outcome:
  - The untrimmed carry starts "sk-AAAA…", a cut artifact, so `generic-sk-key` counts 1 for the carry alone. The head counts 0.
  - The join is one line holding both the artifact and the seam key, so it counts 1, and 1 - 0 - 1 = 0. The split key is not reported.
  - At 1209446 the trim started the carry at "-AAAA…". `ahead` was then empty, so `rebuilt-across-requests-generic-sk-key` was reported.
- Card numbers are not affected: they are counted per occurrence.
- Fix (keeps the scan strict):
  - For the carry comparison, count key-pattern matches per occurrence (`finditer`).
  - Or report the union of the count rule and the old kind rule on a trimmed carry.

**R6. N8 is still partly a denylist: a confirmation phrase plus an unlisted denial is served.**
- Where: `evals/tests/test_a2a05_partner.py:152-173` and `184`.
- Rule: hard rule 9, decision 0025 clause 4 ("fails closed").
- Unsafe: no. The suite is stale and skipped until #18.
- Inputs served beside an artifact that names the variant:
  - "Your cart has nothing in it [P0003-SLT-REL-M]." `\bno\b` and `\bnot\b` do not match "nothing", and "your cart has" confirms.
  - "I added nothing; the shell [P0003-SLT-REL-M] was declined." "I added" confirms, and "declined" is not in `NEGATING`.
  - "I added the shell [P0003-SLT-REL-M] to your wishlist." The confirmation is not tied to the cart.
- Fix:
  - Add nothing, none, empty, declined, removed and unsuccessful to `NEGATING`.
  - Require the confirmation in the same sentence as the cited variant, and with the cart as its object.

**Not a finding (over-blocking only):** N7's fix means "There's nothing left to add." is always replaced. This is a product decision under part 4, not a security issue.

### Checked, no findings
- Hard rule 1: N2 and N4 are closed and nothing found before is lost. R5 concerns hard rule 4, not cards.
- Hard rule 2: findings R1, R2, R3, R4.
- Hard rule 3: only sandbox cards (4242…, VISA test) and synthetic products and keys. No finding.
- Hard rule 4: no secrets and no .env access, and the test keys are fake. N9 is closed. R5 is the only point.
- Hard rule 5: not applicable to this change.
- Hard rule 6: not applicable to this change. No tool access comes from content.
- Hard rule 7: not applicable to this change.
- Hard rule 8: not applicable to this change.
- Hard rule 9: R6 (decision 0025 clause 4). Nothing else.
- Hard rule 10: not applicable to this change.
- Hard rule 11: not applicable to this change.
- Placeholder key only, real key only in the proxy: `llm.py` and the factory are not in the diff. No finding.
- Makefile does not export the key: the Makefile is not in this diff. Unconfirmed outside it.
- No key in CI secrets, CI runs in replay: the diff has no `.github` hunk. No finding.
- Key file never copied into a worktree: nothing touches `LLM_ENV_FILE` or worktree setup. No finding.
- Budget caps enforced and not raised: no ledger or budget change. No finding.
- Cassettes still covered by the scanner: yes, the scan paths and both carry callers are unchanged in shape. R5 is a gap in what the scanner detects.
- Prompt injection in the diff, tests or fixtures: none found.

On severity: there is no high or critical, so stop 10 does not apply. R1 and R2 are medium and unsafe. R1 is pre-existing and R2 is a regression, and both belong to the M1/N1 class, so stop 9 applies. R5 is the only regression in the scanner.

---

## Session notes (not part of the report)
- **Stop 9.** The "restart or cut hides a negation" class (M1, then N1, now R1 and R2) survived two fix attempts. R5 (the carry's
  subtraction: L4, N3, R5), R6 (the order text's denylist: L6, N8, R6) and R4 (the "none left" list: L3, N6, R4) are each a
  third round on the same failure too. No fix was made after this report. See D5 in `2026-11-26-18a-decisions.md`.
