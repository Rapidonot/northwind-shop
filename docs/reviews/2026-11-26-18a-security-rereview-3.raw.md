# Row 18a: security-reviewer's third scoped re-review, verbatim (base 418614f, head 4627104)

Saved as returned.

---

## Third scoped security re-review, row 18a (base 418614f, head 4627104)

**What I read**
- The diff `/tmp/claude-502/northwind-18a-security-scoped3.diff`. Its SHA lines are `base 418614f8c972…` and `head 462710483d1d…`.
- CLAUDE.md "Hard rules" and decision 0001.
- My three earlier passes and `docs/reviews/2026-11-26-18a-decisions.md` (D5 and "The user's answers").
- The changed code in context:
  - `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py`
  - `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scan.py`
  - `/Users/rapido/Projects/northwind-shop-b/services/mcp-orders/src/northwind_mcp_orders/tools.py` and `returns.py` (`KEY`)
  - `/Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py`
  - `/Users/rapido/Projects/northwind-shop-b/evals/tests/test_a2a05_partner.py`
  - every test marked `@D5`

I ran nothing. Every outcome below is traced by hand.

### Does each fix close its finding?
- **R1 and R2: closed.** `_RESTART`, `_SUBJECT_WORD` and `_NEGATED_ADVERB` are gone, so the whole sentence is read and the restart class has no more cases.
- **R3, two spaces: closed.** R3's other half is unchanged and still open, a low in the sentence-end class: when the phrase directly follows ". ", the full stop still ends the sentence.
- **R4, the broken alternative: closed.** `\b(?:we|they)(?:…|['’]ve{_S}not){_S}(?:run|ran){_S}out\b` matches "We've not run out of the M." The two alternatives that worked before are unchanged. R4's other wordings ("It isn't gone.", "Sorry, we're out.") are still shown. They fall under the accepted "none-left wordings" residual.
- **R5: closed.** My R5 input now counts 2 key matches in the join, 1 in the carry and 0 in the head, so the split key is reported.
- **R6: closed for its three inputs.** "nothing" and "declined" are now negating words, and "to your wishlist" does not confirm a cart action.
- **D2 / row #27 L4: closed for the order number.** It is still open for the variant ID (F3).

### Findings

**Critical:** none.

**High:** none.

**Medium:** none.

**Low**

**F1. The order text: a denial in another sentence no longer blocks the narrative. This is a regression against 418614f.**
- Where: `evals/tests/test_a2a05_partner.py:168-192`. `order_denied(text)` was removed, and `confirmed_variants` reads only the sentences that confirm.
- Rule: decision 0025 clause 4 ("fails closed"), hard rule 9.
- Input, beside an artifact that names P0003-SLT-REL-M: "I added the shell to your cart [P0003-SLT-REL-M]. Sorry, that didn't go through, so your cart is still empty."
  - Now: served. The first sentence confirms and holds no negating word. The second cites no variant, so nothing reads it.
  - At 418614f: the artifact went alone, because `order_denied` saw "n't" and "cart" in the second sentence.
- Unsafe: no. The suite is stale and skips until #18.
- Class: the order-text class (L6, N8, R6). The implementation does what D5 (c) says. What was lost is the 418614f check on the whole text.
- Fix (blocks more only): keep both rules. Serve only when no sentence holds both a negating word and an order or cart word, and every variant is confirmed in its own sentence.
- Another case of the same class, which 418614f also served: "The shell [P0003-SLT-REL-M] was in your cart until it sold out." "was in your cart" confirms, and no negating word appears.

**F2. The reply-word exception takes "No-one" as the reply "No".**
- Where: `output_check.py:893` (`_REPLY_NO` accepts a bare hyphen after "no"), used at 917-918.
- Rule: hard rule 2 / RAG-09.
- Input: "No-one in any of our stores across the region has the M in stock." with a=5.
  - `_negated`'s 40-character window starts after "No-one" and returns False.
  - In the sentence, `_REPLY_NO` matches "no-" and strips it, leaving "one in any of …", which holds no negation.
  - The claim is vouched, so the sentence is shown although it says there is no stock.
- Not a regression: the same regex sat behind `started` at 418614f, with the same result.
- Unsafe: yes. It is on a protected path (`services/assistant/**`). The input needs more than 40 characters between "No-one" and the phrase.
- Class: I count it in the clause-restart class, because it is the one restart rule D5 (a) kept. If you count the reply-word exception as a class of its own, this is new.
- Fix (blocks more only): accept a hyphen only with whitespace on both sides, e.g. `(?:,|\s[-‐‑]\s|\s*[‒–—―])`. "No, the Charcoal L …" still matches.

**F3. A card split between a well-formed variant ID (or order number) and another argument still passes. D2's "never a card" holds only for the order number plus the key.**
- Where: `tools.py:58-63`, `105-116` and `148`.
- Rule: hard rule 1 / PAY-05.
- Inputs:
  - `{"order_id": "O000800", "variant_id": "P4111-111111", "condition": "unused", "idempotency_key": "k-111111-x"}`.
    - The variant's group is "4111-111111" (10 digits), and the key has 6 digits.
    - `_card_across` skips every pair that holds a well-formed variant.
    - In `holds_card_data`'s joined text, "4111" touches the "P", and the two standalone runs are more than 12 characters apart.
    - D2 allows 6 digits. Together, 4111111111111111 is Luhn-valid. The request is not refused as card data, and the desk then refuses the unknown line.
  - `order_id` "O411111" with `variant_id` "P1111-111111": both are well-formed, so no pair is checked.
- Not a regression: this was the same at 418614f.
- Unsafe: yes, as a gap in the hard-rule-1 net. Whether `services/mcp-orders` counts as protected is unconfirmed: it is not in CLAUDE.md's named list. The inputs are contrived.
- Class: the row #27 L4 / D2 split-card class. That is not one of the four classes your stop rule names, so by its wording this is a new class. Your call whether it stops the chain.

**F4. Sentence ends: a full stop before a capital letter, and a "!" inside a dashed aside.**
- Where: `output_check.py:885-887`.
- Rule: hard rule 2.
- Inputs, each with a=5, each shown:
  - "None of the U.S. Outlets has the M in stock." The full stop before the capital "O" ends the sentence.
  - "None of them — sadly! — are in stock." The "!" before a space ends the sentence.
- Not a regression: 418614f did the same.
- Unsafe: yes, on a protected path.
- Class: sentence ends (L1, N5, R3). D5 (d) grouped this class with wordings, but your stop rule does not name it.

### Is anything blocked or found at 418614f now shown or lost?
- **output_check.py: nothing lost.**
  - `negated_in_sentence` now reads the whole sentence where it read a part of it, and it no longer strips negated adverbs.
  - The early `return True` when `whole` is False with no sentence end is the old `not started`.
  - The reply strip behaves exactly as before.
  - The new `_SENTENCE_END` matches only where the old one did, so there are fewer ends and longer sentences.
  - A `None` result still makes `stock_vouched` False, and `offered_range` still needs `False`.
  - The stock-pattern change only adds a match.
- **Tests marked D5:** every one asserted "shown". Three of them also asserted a block at v=0 ("Never mind…", "No worries…", "Don't wait…"). Those three are pinned again at a=0 in `test_18a_d5_pins.py` (`WAS_SHOWN`). No safety assertion is hidden.
- **scan.py: nothing lost.**
  - The line split is the same, and `findall` is at least `search`, so every count is at least the old one.
  - Lines outside the seam are the same text in the join and in its parts.
  - When the old rule reported, the seam line held no match in the carry or the head. That stays 0, while the join's count is at least 1.
- **mcp-orders: nothing lost.** The card checks still run first, and D2 only refuses more.
- **A2A-05: one thing lost (F1).**

### Can the D2 key rule break a recorded replay?
- **No, for any key with 6 or fewer digits in a row.**
  - The tool description and schema are unchanged, so request bytes are unchanged.
  - The rule fires only on a digit group longer than 6, before the desk, and changes the tool result only then.
  - My grep of `cassettes/` finds 1,606 `idempotency_key` values and none with a 7-digit group. That fits the 1,612 measured. My grep does not reassemble keys split across `input_json_delta` events, so it is a spot check, not proof.
- Two edge cases, neither in any recording I found:
  - A long-digit key on a request the desk would refuse for another reason now gets `invalid_idempotency_key` first.
  - `\d` is Unicode, but the desk's ASCII `KEY` already refuses such keys with the same code and different wording.

### Checked, no findings
- Hard rule 1: F3 only. The card-first order is intact, and the D2 error does not echo the key.
- Hard rule 2: F2 and F4. R1, R2, R3 (two spaces) and R4 (the alternative) are closed.
- Hard rule 3: only sandbox test cards and synthetic IDs.
- Hard rule 4: R5 is closed. No secrets, no .env access, fake test keys.
- Hard rule 5: not applicable to this change.
- Hard rule 6: not applicable to this change.
- Hard rule 7: not applicable to this change.
- Hard rule 8: not applicable to this change.
- Hard rule 9: F1 (decision 0025 clause 4). `request_return` still requires its key.
- Hard rule 10: the new error has category `validation`, is not retryable, and says what to do. No finding.
- Hard rule 11: not applicable to this change.
- Placeholder key only, real key only in the proxy: `llm.py` and the factory are not in the diff. No finding.
- Makefile does not export the key: the Makefile is not in the diff. No finding.
- No key in CI secrets, CI runs in replay: no `.github` hunk. No finding.
- Key file never copied into a worktree: nothing touches `LLM_ENV_FILE`. No finding.
- Budget caps enforced and not raised: no budget or ledger change. No finding.
- Cassettes still covered by the scanner: yes, and R5 is closed.
- Prompt injection in the diff, tests or fixtures: none found.

**Against your stop rule:** no medium or above.
- F1 (order-text class) and F2 (clause-restart class, the kept reply word) are lows in classes you named, so they go to the PR body as accepted residuals.
- F4 is in the sentence-end class, which the rule does not name.
- F3 is in the D2 split-card class, which the rule does not name.
- Whether F3 and F4 count as new classes, and so stop the chain, is your decision.
