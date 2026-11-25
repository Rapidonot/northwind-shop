# Scoped security read: row #30, M-4 and `_negated` (hard rule 2)

The SHA lines match (`base 567753e0…`, `head 552d5a9d…`). The diff holds only `output_check.py` and two new test files. This is a static read: I ran no test, script or `make` target.

**Summary**
- No critical or high finding.
- Nothing new in the diff is medium or above.
- Both named phrasings are closed.
- Two mediums are on the base. They are residuals of the same two classes.
- All line numbers are in `/Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py` unless another path is given.

## Findings

**M-A. Medium. On the base. Hard rule 2. Other natural "none left" wordings match no stock pattern (`:433-466`).**
- These reach the shopper with `live.stock` empty, or at any stock level:
  - "There aren't any left in size M."
  - "We don't have any left."
  - "Zero left." ("zero" is not in `_UNIT_WORDS`)
  - "No units left."
  - "No stock remaining."
  - "None of them left."
- "No more stock left" also passes: the pattern needs "no stock" adjacent.
- M-4's two named phrases are closed. The class is not.
- The same list is absent from `evals/src/northwind_evals/intents.py:121-125` (so the scorer does not see them either). TRAP-INJ-002 (`evals/src/northwind_evals/harness.py:57`) still sees only "out of stock" and "sold out", so M-4's remark about it stands.
- Any further `PATTERNS` change after recordings is stop 7, so this is the user's call.

**M-B. Medium. On the base, same verdict at head. Hard rule 2. A negation whose scope crosses a cut is still read as positive (`:780`, `:785-789`, `:797-804`).**
The fix makes one cut correct, but discarding the text before it loses a negation that governs across it. All rows below get `_negated` = False at base and head:

| Input | Stock | Result |
|---|---|---|
| "None of the Slate towels, Bath or Hand, is in stock." | all checked > 0 | shown; says the opposite of the check |
| "None of the 2.5 m rugs is in stock." ("." in a decimal is a cut) | all > 0 | shown |
| "The towel is not, in Slate, Bath size, in stock." (two asides; `_NEGATED_ASIDE` reads one) | 5 | shown |
| "The towel is not (in Slate) (Bath) in stock." | 5 | shown |
| "The towel is not, as far as I can tell today, currently in stock." ("not" is exactly outside the 40-character window) | 5 | shown |
| "The towel is not\nin stock." (a newline is a cut and a sentence start) | 5 | shown |
| "Neither colour, Sage or Black, is available in sizes M and L." (H3, `:1109`) | **nothing checked** | shown as an offered range |

- The last row is the worst, because it needs no `check_stock` at all.
- Earlier L-3 rated related H3 misses low. I rate this medium because H3 fails with nothing checked and the wording is natural; the individual odd-punctuation rows alone would be low.
- The answer to "does polarity hold for every cut character" is therefore: yes for a negation after the last cut, no for one before it.

**L-A. Low. New for two or more cuts (one cut or none already behaved so). Hard rule 2. Over-claims with "never" now pass.**
- "The towel in Slate, Bath size, is never out of stock." at 5 units: the base lost "never", read it as negative and blocked. Head: governing negation, so positive, so shown.
- Likewise "…, …, is never in stock." at 0. One check vouches a statement about all time.

**L-B. Low. New, documentation. The release list is wider than "a true negated claim at zero".**
`docs/decisions/0015-output-control.md:448-450` and your question both name only that case. Head also releases the items under "What now reaches the shopper" below.

**L-C. Low. New patterns, no loosening (base passed them unchecked). Hard rule 2. Two "none left" spans can be vouched wrongly.**
- "There is almost none left." or "next to none left" with every variant at 0: shown, though it implies some remain.
- "There aren't none left." with every variant > 0: shown (double negation flips; decision 0015 already logs double negation as a residual).
- A multi-variant turn is strict: a=0, b=3 and "None left in M" is blocked even if true. It fails closed.
- Logged finding 8 applies: an unchecked variant is vouched if every checked one is at 0.

**L-D. Low. New. Test gaps in `services/assistant/tests/test_30_negation_cut.py`.**
- Every parametrised case uses commas only. Nothing with two or more cuts uses `;`, `:`, brackets, an en/em dash, " - ", `.` or a newline.
- Nothing tests the 40-character edge, a negative phrase under a negation with two cuts ("…, …, is not out of stock" at 0 and at 5), a negated count with two cuts, or the mid-word case in (c) below.
- The mixed-cut claim rests on reading the code.

**L-E. Low. On the base. Whitespace and hyphen forms.** "none    left" (four spaces; `_S` is at most three, and HTML may collapse them) and "none-left" / "no-stock left" (no `_J`) match nothing. "out-of-stock" is handled by `_J`.

## Answers to your questions

**Does each close its finding?** M-4: yes for "none left" and "no stock left" (case, markdown, double space). `_negated`: yes. `finditer` scans the original string, and head slices once at the last end.

**What now reaches the shopper that the base blocked?** The old clause started at the sum of the cut ends and the new one starts at the last end, so the new clause always contains the old one. With no cut or one cut the result is identical. With two or more:
- (a) A true negated claim at zero (IC-032).
- (b) A negated negative phrase at all > 0: "…, …, is not out of stock / not sold out / not unavailable". Correct, except "never" (L-A).
- (c) Text where the old offset fell inside a word and made a false negation: "A, B, knot rope is in stock." at 5 units. The old clause began at "not rope is", so None, so blocked. Head: False, so shown, which is correct. The H3 range has the same effect ("A, B, knot rugs are available in three colours.", no check needed), also correct.
- A real negation the base saw is never lost. A negated count becomes stricter. M-4 releases nothing.

**One, two, three or more cuts, each cut character.** Same arithmetic for every one. Positions are in `before`, matches do not overlap, and the last one wins. Negation after the last cut: correct. Before it: M-B.

**40-character edge.** Every governing form fits (about 26 characters at most). "knot" cut to "not" at the edge gives None and fails closed. A cut straddling the edge (" - " without its leading space) lengthens the clause and fails closed. "isn't" cut inside, or a negation beyond 40, gives False and fails open (base, L-3). The aside pattern can be 60 or more characters wide, hence the M-B window row.

**H3 `offered_range`.** It needs `negated is False`, so a longer clause only tightens it, apart from the artefact in (c). M-B's last row remains.

**Streaming.** The negation precedes the match, so it is in the view before the match is decided. `origin` keeps 2L (128) characters and needs at most 64 + 40. A match at a delta's end is deferred by the `end >= len` and `_OPEN_TAIL` rules. "There is no" before "stock" can be released and then Reset, but no claim is shown. Unconfirmed, base: a run of about 130 `*` between "not" and "in stock" might push "not" out of the view when streamed, because the window is counted in marked characters but polarity is read in the plain view.

**Vouching.**
- "none" is not read as "one".
- "no one left" and "not one left" are counts under a negation and always blocked.
- "none in stock" and "none available" give None and are blocked.
- "5 in stock, none left" is blocked at 5.
- "No, none left" is cut at the comma and needs zero.
- "No doubt none left" gives None.
- `_NEGATIVE` is searched only on the matched span.

**TRIGGER.** It is searched for the earliest match, so one added alternative can only hold more. It holds from "none" for at most 63 view characters and is released by later text or at finish. Nothing is released earlier. "None of these is waterproof." is briefly held, then shown. "no stock left" is held from "stock" (already a trigger).

**`_COUNT_IN`.** `\b` binds only to the grouped `_SPELLED_COUNT`. Every count in a `live_stock` span is already behind `\b` in its pattern, plain view drops `_`, and the digits branch is unchanged. The only span whose reading changes is "none left". `offered_range` passes only the word, so no count.

**Regex cost.** `_NEGATION_CUT` runs on at most 40 characters, so the list is at most 40 long. The new pattern is at most 17 wide, linear, with no nested repetition. The `TRIGGER` addition is a literal. No backtracking risk.

**Over-blocking (fails closed, new with two or more cuts).** A real but non-governing negation after the last cut, formerly chopped off, now gives None: "The towel, in Slate, has no hood and is in stock." is replaced at 5 units.

## Checked, no findings

Hard rules:
- Rule 1, card data: not applicable to this change.
- Rule 2, live values: findings above.
- Rule 3, synthetic data: tests use catalog-style names only; no finding.
- Rule 4, secrets: none in the diff; no `.env*` read.
- Rule 5, invented attributes: not applicable to this change (marker code untouched).
- Rule 6, data not instructions: no text in the diff tries to steer the review; no finding.
- Rule 7, internal and region: not applicable to this change.
- Rule 8, least privilege: not applicable to this change.
- Rule 9, idempotency and confirmation: not applicable to this change.
- Rule 10, structured errors: not applicable to this change.
- Rule 11, AI disclosure: not applicable to this change.

Decision 0001 and key handling:
- Placeholder key in child processes, real key only in the proxy: not touched by this diff.
- Makefile does not export the key: not in the diff; still `unexport ANTHROPIC_API_KEY` (`Makefile:37-38`).
- No key in CI, CI in replay: not in the diff; `LLM_MODE: replay` at `.github/workflows/ci.yml:40,81,115,154` and `guard.yml:75`, and no `secrets.` use in the workflows.
- Key file never copied into a worktree: not touched.
- Budget caps: not touched, not raised.
- Cassettes covered by the scanner: not touched; no cassette in this diff.

## Not checked
- I ran nothing, so the 522-call comparison, the 31 passing tests, the mutant results, the check identity `05379e63`, the W6 promote and candidate `6e9f4ae9` are not re-verified. The IC-032 cassette text was not read.
- A search of `cassettes/` for "none left", "no stock left", "any left", "not out of stock" and "never out of stock" found nothing, but a phrase split across recorded deltas would not match it.
- How the portal renders a soft newline or four spaces (it decides how the newline row of M-B and L-E look to a shopper).
- Worked examples were traced by hand against the code, not executed. Please run them before acting on them.
- Nothing outside the two changes: the rest of row #30 is not in this diff.
