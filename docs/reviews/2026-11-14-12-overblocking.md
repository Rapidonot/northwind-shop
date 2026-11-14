# Row #12 §5.1: over-blocking of the new output-check patterns on the recorded answers

2026-11-14, no LLM, no spend. Reviewed by: `model` (Claude). This is the measurement plan §5.1 and the user's change 2
ask for before the output-check commit.

## Method
- **Text:** the model's own text from every assistant cassette at `origin/main` `c54f991`. That is the text deltas
  of every SSE response in `cassettes/eval-answers` (answerer only, not the judge), `cassettes/assistant-smoke` and
  `cassettes/assistant-redteam`: **143 assistant calls**. The prompt forbade figures when these were recorded, so
  they show what the new patterns catch in ordinary answers.
- **Comparison:** `hits(text)` from `origin/main`'s `output_check.py` against this branch's, with the same empty
  sources and no live values. The difference is what the new patterns add. A sentence the new check replaces and
  the old one did not is listed.
- **Script:** the session scratchpad `overblock.py`, which loads `git show origin/main:…/output_check.py` beside
  the branch's module.

## First pass (patterns as planned)

New hits: **stock 5, money 2**. Member wordings: **0**.

| Item | Class | Sentence the new check replaced | Read |
|---|---|---|---|
| EV-041 | stock | "Check the product page to see whether that size is available right now." | False positive: it asks, it does not claim stock |
| EV-042 | stock | "The live product page will show whether Glacier Blue is available in 4XL right now." | False positive: it asks |
| EV-049 | stock | "I can't see stock levels, so check the live product page to see whether the M is available now." | False positive: it asks |
| EV-045 | stock | "The live product page shows which size and colour combinations are available, and it also has current stock." | False positive: "which … are available" is the range offered |
| RT-028 | stock | "…so I can't say whether any is available." (compensation, not stock) | False positive |
| EV-030 | money | "I can't confirm prices, so check each live product page to see which is under $80." | The shopper's own figure (price filter). **D5 stop** |
| EV-029 | money | "I can't confirm prices, so please check each live product page for the \"under $150\" limit." | The shopper's own figure (price filter). **D5 stop** |

## Narrowing (done, not waiting on the D5 answer)
- The four "available" patterns get the exemption `availability`: a match passes when its clause, within the
  same sentence and at most 60 characters back, opens with "whether", "if" or "which". Otherwise it passes only as
  `check_stock` says.
- "Available in sizes/colours/fits/lengths/widths" was already left out, as the range offered.
- #15 L9's sentence ("The option you described is available in the sources") is still replaced when no stock was
  checked.

## Second pass (after narrowing)

New hits: **money 2** (EV-029, EV-030, as above). **Stock 0. Member wordings 0**, so all three member-price
patterns are kept: `members-only rate/price/pricing/deal/offer`, `member rate/deal/offer/savings/exclusive`,
`loyalty price/pricing/rate/discount/reward/points`.

## D5, decided by the user (2026-11-14): option 1
- In both money hits, the assistant repeats the shopper's own price filter ("under $80", "under $150") while saying
  it cannot confirm prices.
- D5 stays fail-closed: any figure that no `get_price` result returned this turn is blocked, the shopper's own
  included.
- The prompt gains one line: "If the shopper gives a price limit, give each live price instead of restating the
  limit."
- At W3 I report how many price-filter answers got the money fixed sentence, and how many hit the 6-turn cap.

## Third pass: `_asks()` tightened (the user, 2026-11-14)
The first narrowing was wider than "a clause opened by whether/if/which": `_BOUNDARY` only splits sentences, so real
stock claims passed with no `check_stock`. Now:
- The asking word must govern the clause that holds "available". A comma, semicolon, colon or spaced dash between
  them breaks the clause, and so does "but", "and" or "so" followed by a subject word (the, it, this, they, you…).
  A bare "and" inside a noun phrase does not break it, which keeps EV-045 ("which size and colour combinations are
  available") a question. The user named only "but, and, so"; the subject-word condition is mine. It is named as a
  focus for `security-reviewer`.
- "which" counts only as a question: never after a comma, and never directly before a verb ("which is available").
- Blocked when no stock was checked:
  - "If you need it soon, the Sage M is available now."
  - "Not sure if you saw, but the M is available."
  - "The Glacier Blue 4XL, which is available today, …"
  - "Check if you like it and the M is available."
  - "The size which is available is M."
  - #15 L9's sentence.
- The five measured false positives still pass.
- **Re-measured:** 143 calls, new hits **money 2** (EV-029, EV-030, as above), **stock 0**, member wordings 0.

EV-029 and EV-030 are also on the user's W3 watch list: their reference facts say price is "on the product page".

## Fourth pass: after security review pass 1 (2026-11-14)
- The check now has more money formats (code before, sign after, pounds, bare price, size words, markdown), and
  "available" needs an embedded-question frame. It also handles negation and reads quantities.
- Re-measured, 143 calls: new hits **money 2** (EV-029, EV-030), **stock 0**, member wordings 0.
- The five measured questions still pass.
