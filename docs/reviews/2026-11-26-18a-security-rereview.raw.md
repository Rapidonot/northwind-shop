# Row 18a: security-reviewer's scoped re-review, verbatim (base 28e07ef, head 1209446)

Saved as returned. Session notes at the end.

---

## Scoped security re-review, row 18a (diff base 28e07ef, head 1209446)

**What I read.** `/tmp/claude-502/northwind-18a-security-scoped.diff` (its SHA lines match the ones you gave), CLAUDE.md "Hard rules", decision 0001, and my first pass `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-26-18a-security-review.raw.md`. On disk I also read the changed code in context in `/Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py`, `/Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py` and `/Users/rapido/Projects/northwind-shop-b/services/llm-replay/src/northwind_llm_replay/scan.py`. I ran nothing. Every outcome below is traced by hand.

### 1. Does each fix close its finding?
- **94048d9 (test-writer's gaps): closed.**
  - With a 4L reach, a sentence whose start lies outside the window, with no end or new clause in it, returns None.
  - The "none of them are left" verb form is matched.
  - "CVV 7 / 3 / 7" is found.
- **M1: closed for my three inputs.** The second dash is skipped, and the first is spoiled by the second, so the whole sentence is read. See N1 for what is left.
- **L1: closed** for e.g., i.e., approx. and incl. Other abbreviations are still open (N5).
- **L2: closed.** Only attitude adverbs are stripped now.
- **L3: closed** for "nothing left in size M", "we've run out", "they're all gone", and hardly/barely/scarcely (low stock, never vouched). Some wordings are still open (N6).
- **L4: closed only for a contiguous digit run.** It is not closed for grouped cards, Unicode digits or a window that is one long token, and the trim adds a small regression (N2, N3).
- **L5: closed** for the four inputs, but with a small regression (N4).
- **L6: closed** for the four inputs, but the check is still a denylist (N8).
- **L7: closed for 3 backslashes only.** Escaping three times gives 7, which is not matched (N9).

### 2. Regressions against 28e07ef
- **`negated_in_sentence` (`whole`/`started`, dash skip, abbreviation filter, narrower `_NEGATED_ADVERB`, 4L reach): none.**
  - Each change only reads more of the sentence or fails closed more often.
  - `_RESTART_SPOILED` now includes dashes, so any restart before a dash is spoiled. After skipping a closing dash, the loop therefore takes no restart at all, never an earlier one.
  - Fewer sentence ends make the sentence longer, and when `whole` is False they leave `started` False.
  - The reply strip is gated on `started`.
  - The wider reach only adds text in front of what was read before.
  - `whole = origin == 0` is right: `_raw` holds the whole attempt until `reset()`.
- **`carried`: one regression (N3c).**
- **cards.py: one regression (N4).**

### 3. Can the stream cut a new pattern so that a claim is sent before it is decided?
**No.**
- Every new pattern ends in left/remaining (and starts with the triggers zero, none, nothing, any or hardly), or ends in the trigger "out" or "gone".
- The trailing word is always held, and `_first_match` decides a match before `_release_to` runs. So the complete claim is never released undecided.
- Every new pattern is shorter than L.
- The `hardly` prefix stays in view, because "hardly" is itself a trigger. Stock patterns run on the plain view, so `**hardly**` does not split it.

Two side effects, neither unsafe:
- "They're all gone." and "They ran out" are sent and then taken back. "they", "all" and "it's" are not triggers, so "They're all " is released and then reset (N10).
- The lookahead on "nothing left" over-blocks in the stream (N7).

---

### Findings

**Critical:** none.

**High:** none.

**Medium**

**N1. A one-character restart that is not a new clause still hides the negation. This is the M1 class, and it is pre-existing, not a regression.**
- Where: `output_check.py:868-874` (`_RESTART` holds `[;:]` and a bare `[‒–—―]`), used at 900-907. `_NEGATION_CUT` at 824 also cuts there.
- Rule: hard rule 2 / RAG-09.
- Unsafe: yes, on a protected path (`services/assistant/**`).
- Inputs and outcomes:
  - "None of the Bath towels in sizes S–M are in stock." with a=5 is shown. The unspaced en dash of a range is taken as a restart, so only "m are" is read.
  - "None of the jackets in UK 8–12 is available in three colours." with nothing checked is shown through the `offered_range` exemption. This is the M-B worst case again.
  - "None of these sizes: M or L is in stock." with a=5 is shown, via the colon restart.
  - Contrived: when the first dash of an aside is more than about 250 characters back, outside `NEGATION_REACH`, the closing dash alone restarts and sets `started=True`.
- Fix (only blocks more):
  - Do not treat `–`/`‒` between two alphanumerics as a restart.
  - Do not take a `:` restart when a negation stands earlier in the sentence.
  - With `whole` False, do not count a dash restart, since its partner may be out of view.

**N2. Grouped cards: the scanner's non-overlapping match hides a valid card, and the L4 fix does not cover it.** The scan_text part is pre-existing.
- Where: `scan.py:68`, `73-76`, `134-140` (`finditer` on `_PAN_GROUPED` / `_PAN_GROUPED_WIDE`), plus `ahead` at `308-310`.
- Rule: hard rule 1, decision 0001 §3 (scanner).
- Unsafe: yes, on a protected path (runner-sync).
- Inputs and outcomes:
  - "ref 1111 4242 4242 4242 4242 x" scans clean.
    - The leftmost match is "1111 4242 4242 4242", and its Luhn check fails (sum 66).
    - The overlapping "4242 4242 4242 4242" is never tried.
    - A random leading 4-digit group fails Luhn about 90% of the time, so most such prefixes hide the card.
  - The same shape defeats the L4 fix:
    - Call 0 holds "1111 4242 4242 4242 4242 …", with the 256-character cut inside "1111". The trim moves the carry's start to the space after it.
    - Alone, that carry finds the valid "4242 4242 4242 4242", so `luhn-valid-pan` lands in `ahead`.
    - Call 0 ends "…ref 42424242" and call 1 starts "42424242 ok". The real join card is dropped, in both `put` and `scan_paths`.
- Fix (keeps the scan strict):
  - Scan the grouped forms with overlapping matches, e.g. a lookahead `(?=(...))` with `finditer`.
  - Compare per-kind counts, not kinds: the join minus the carry alone minus the new head (see N3).

**Low**

**N3. More holes in the carry trim, one of them a regression.**
- Where: `scan.py:319-333`.
- Rule: hard rule 1.
- Unsafe: yes, on a protected path.
- (a) Unicode digits:
  - `_TOKEN_CHAR` is ASCII, but `_PAN_RUN`'s `\d` matches any Unicode digit, and `luhn_ok` accepts them.
  - A 17-digit fullwidth run "１４２４２…" cut after the first digit is not trimmed. That reproduces L4 exactly.
- (b) One long token: when the 256-character window has no non-alphanumeric character, `cut` is None and the window stays mid-token. L4 reproduces with "1" + "4242424242424242" + 239 letters.
- (c) Regression:
  - The trim drops the start of a card glued to a token that crosses the window's left edge.
  - Example: call 0 ends with 250 × "x" + "4242 4242 ", and call 1 is "4242 4242 ok".
  - The real join "x4242 4242 4242 4242" is found at 28e07ef and missed now, because the carry becomes " 4242 ".
  - This is contrived: it needs a 240+ character alphanumeric token.
- Fix: use the untrimmed carry for the join and the trimmed one only for `ahead`, or count-based subtraction. Use `[^\W_]` (Unicode) for token characters.

**N4. L5: the wider guards reject codes that 28e07ef caught (regression).**
- Where: `cards.py:42-43`.
- Rule: hard rule 1 / PAY-05.
- Unsafe: yes, on a protected path (gateway chat check and every MCP server).
- Inputs: "Security code 7, 3, 7 - 2, 4" and "CVV 1 - 2 - 7, 3, 7". Both are card data at the head and are not now.
- Cause: the new lookahead ` ?SEP ?\d` and the new lookbehinds reject every alignment where the old ones matched the first three digits.
- Fix: keep the old second alternative verbatim as one more alternative (a union), as scan.py does. Then re-run the over-blocking measure.

**N5. L1 left open: other abbreviations.**
- Where: `output_check.py:860`.
- Rule: hard rule 2.
- Unsafe: yes, on a protected path.
- Inputs:
  - "None of the U.S. stores has the M in stock." with a=5 is shown.
  - "None of the No. 5 towels is in stock." with a=5 is shown. "No." is left out of the list on purpose.
- Fix (only blocks more): a full stop followed by a lowercase letter or a digit does not end the sentence.

**N6. L3 left open: more wordings. Pre-existing.**
- Where: `output_check.py:489-508`.
- Rule: hard rule 2.
- Unsafe: yes, on a protected path.
- Shown with nothing checked:
  - "There's nothing left to buy in size M." The `to` lookahead exempts it.
  - "We haven't run out of the M."
  - "It's not gone."
  - "We're out of the M."
  - "Hardly any are left." and "There are hardly any of them left." These are low-stock claims.

**N7. The stream and the whole text disagree on "nothing left to add".** Over-blocking only.
- Where: `output_check.py:500`, with the decision at 1484-1508.
- Unsafe: no.
- Input: "There's nothing left to add." streamed with the space after "left" as its own delta. At "nothing left " the lookahead `(?!\s(?:to|…)\b)` passes, the match is decided and the sentence is replaced. The whole text passes. The same happens with "nothing left me cold".
- This corrects my first pass, which said I found no case where the stream and the whole text disagree.

**N8. L6 left open: the check is still a denylist, so it can fail open.**
- Where: `evals/tests/test_a2a05_partner.py:152-165`.
- Rule: hard rule 9, decision 0025 clause 4 ("fails closed").
- Unsafe: no. The suite is stale and skipped until #18.
- Inputs served beside the artifact (they cite the variant and hold no listed word):
  - "Adding the shell to your cart was unsuccessful [P0003-SLT-REL-M]."
  - "The shell [P0003-SLT-REL-M] was declined at checkout."
  - "Your cart is still empty [P0003-SLT-REL-M]."
- Fix: require a positive confirmation phrase.

**N9. L7 left open: escaping three times.** Unconfirmed, as in the first pass.
- Where: `scan.py:57`.
- Rule: hard rule 4.
- `\\{1,6}` covers one and two levels of escaping (1 and 3 backslashes). Three levels give 7 backslashes, which are not matched.
- The tests use 3, 4 and 6 backslashes. 4 and 6 are not real escape depths.
- Fix: `\\+`.

**N10. The "held until decided" test does not test that.**
- Where: `services/assistant/tests/test_18a_security_fixes.py:423-443`.
- Unsafe: no.
- It drops `None`, so a take-back passes. For "They're all gone." the stream sends "They're", " ", "all", then a reset, then the stock sentence.
- For "We've run out of the M." it only checks that "m" was not sent.

### Checked, no findings
- Hard rule 1: findings N2, N3, N4.
- Hard rule 2: findings N1, N5, N6, N7.
- Hard rule 3: only sandbox test cards (4242…, 4111…) and synthetic products, canaries and keys. No finding.
- Hard rule 4: no secrets, no .env access, test key values are fake. N9 is the only point.
- Hard rule 5: not applicable to this change.
- Hard rule 6: not applicable. No tool access comes from content.
- Hard rule 7: not applicable to this change.
- Hard rule 8: not applicable to this change.
- Hard rule 9: N8 (decision 0025 clause 4). Nothing else.
- Hard rule 10: not applicable to this change.
- Hard rule 11: not applicable to this change.
- Placeholder key only, real key only in the proxy: `llm.py` and the runtime factory are not in the diff. No finding.
- Makefile does not export the key: the Makefile is not in this diff. It was limited to services, evals and .github, so this is unconfirmed outside it.
- No key in CI secrets, CI in replay: the diff has no `.github` hunk. No finding.
- Key file never copied into a worktree: nothing touches `LLM_ENV_FILE` or worktree setup. No finding.
- Budget caps: no ledger or budget change. No finding.
- Cassettes still covered by the scanner: yes, the scan paths are unchanged. N2, N3 and N9 are gaps in what it detects.
- Prompt injection in the diff, tests or fixtures: none found.

On severity: no high or critical, so stop 10 does not apply. N1 and N2 are medium and unsafe, both are pre-existing rather than regressions, and both are in this row's files. N3c and N4 are the only regressions against the head I reviewed.
---

## Session notes (not part of the report)
- Triage (decision 0021): N1 and N2 medium, unsafe, in the row's files: fixed. N3 and N4 are regressions of the first round's fixes: fixed. N5, N6, N7, N9 low in the row's files: fixed. N8 (decision 0025 rule 4): fixed by requiring a confirmation. N10 (a test that did not test the hold): fixed. A second scoped re-review follows.
