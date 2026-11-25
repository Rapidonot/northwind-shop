# Rows #30 and #22 (SA-04, SA-03 partial): label review sheet

**For:** the user. **What to do:** for each row write `ok`, or the label you want instead. Only the rows you accept become
`reviewed_by: human`; the other items stay `model`. Then answer the three rule questions.

- **Labels:** `evals/fixtures/sa04_prompts.jsonl` (20) and `evals/fixtures/sa03_conversations.jsonl` (50), drafted by a fresh
  subagent, with no implementation in existence.
- **Rules:** `docs/reviews/2026-11-25-30-labelling-guide.md`, with 7 clarifications at its end.
- **Checked twice:** the subagent's validator (0 errors), and this session's own script (0 errors): the ten recommendation
  sets recomputed, variants, stock signs, regions, documents, repeats, and every fact wording in its cited record.
- **After your marks:** labels and guide are frozen by a sha256 pin. A later change is stop 5.

**The user's answer, verbatim (2026-11-25):** "Marks (the user's): rows 1–16, 19 and 20: ok → reviewed_by human. Row 17,
IC-040: ok with a change, and the same change on IC-039: add to `absent` the out-of-stock wordings "out of stock", "sold
out", "back in stock", "restock", "currently unavailable", "not in stock", so an answer that treats a non-existent
combination as merely out of stock fails. Both then human. Row 18, IC-045: ok with a change: replace `absent: final sale`
with "is final sale", "are final sale", "final sale and", "final-sale items can't", so "clearance isn't final sale"
doesn't trip it. Then human. Rule questions: A yes, B yes, C yes."

21 items are `human` (the 20 rows and IC-039), written at the user's instruction. Labels below are as drafted; the
changes are in the guide.

**After the freeze, the user's decision, verbatim (2026-11-25):** "drop "final sale and" from IC-045's `absent` (it fails
the correct "isn't final sale and can go back"; "is final sale"/"are final sale" already catch the wrong claim). Re-pin
the sha256 in its own commit, before any scorer runs on it".

## Rule questions

| # | Question | Recommended | Your answer |
|---|---|---|---|
| A | Add the slot words of clarification 3? Without them CL-014, 016, 019 and 020 fail on natural questions. | Yes | yes (the user) |
| B | Clarification 4: does a correct live count count as "in stock"? | Yes | yes (the user) |
| C | IC-009: add "not machine-washable", "do not machine wash" and "don't machine wash" to `absent`? | Yes | yes (the user) |

**Known limits:** IC-041 to IC-048 and IC-050 reword policy topics the eval set already covers; no query repeats word for
word. IC-002 is close to EV-104 (same question, another product). That the named attribute "decides" an SA-04 pick is the
subagent's judgement.

## Items

| # | Id | Region | Message | Label | Why it is hard | Your mark |
|---|---|---|---|---|---|---|
| 1 | CL-002 | UK | I walk to work and need an insulated jacket for the cold mornings. Which one should I get? | asks: budget / size | "Walk to work" counts as use given, so a question only about weather or temperature fails. | ok (the user) |
| 2 | CL-003 | CA | I'm a men's medium. Which fleece should I get? | asks: budget / use | Size given; the answer may reasonably list options by fleece weight instead of asking. | ok (the user) |
| 3 | CL-005 | US | I need a base layer for skiing. Which one would you pick? | asks: budget / size | "Skiing" may feel like enough to recommend; only a size or budget question counts. | ok (the user) |
| 4 | CL-006 | UK | What size base layer should I order for myself? | asks: size | A size chart with no question fails. | ok (the user) |
| 5 | CL-007 | EU | I'm looking for a pair of travel pants. Which should I get? | asks: budget / size | Travel is given, so "what will you use them for" fails. | ok (the user) |
| 6 | CL-011 | EU | I'd like a cardigan for my mum's birthday. Which one should I choose? | asks: budget / size | Only 3 cardigans fit. A question only about her taste or colour fails. | ok (the user) |
| 7 | CL-014 | UK | Our queen duvet is worn out. Which new one should I get? | asks: budget / use | "How warm do you sleep?" passes only with clarification 3. | ok (the user) |
| 8 | CL-016 | EU | What size towel should I order? | asks: size / use | "Bath towel or bath sheet?" passes only with clarification 3. | ok (the user) |
| 9 | CL-019 | UK | Which tablecloth should I get for Christmas dinner? | asks: budget / size | "How many guests?" passes only with clarification 3. | ok (the user) |
| 10 | CL-020 | EU | What size tablecloth should I get for our dining table? | asks: size | "How long is your table, or how many does it seat?" passes only with clarification 3. | ok (the user) |
| 11 | IC-004 | CA | Can the Corvane Cairn Cardigan go in the washing machine? | cites CG-DRYCLEAN or P0106; facts dry clean or dry-clean or dry cleaned … (13 wordings) | Dry-clean care is on the linked guide, not in the product description; either may be cited. | ok (the user) |
| 12 | IC-006 | UK | My chest measures 48 inches. Which size of the Brindlemoor Squall Rain Jacket should I order? | cites SG-OUTERWEAR; facts 2xl or xxl or 2x-large … (4 wordings) | Needs the size guide cited; 2XL exists only in the relaxed fit. | ok (the user) |
| 13 | IC-009 | EU | Can I put the Larchpell Drift Rug in the washer? | cites P0188; facts machine washable or machine-washable or machine wash … (13 wordings); absent is not machine washable, isn't machine washable, isn’t machine washable | A "no" holding "machine wash" passes unless `absent` catches it. Proposed addition below. | ok (the user), with answer C |
| 14 | IC-021 | US | I'm a man who wears a 3XL and I want a waterproof rain jacket in a relaxed fit for rainy hikes. Which should I get? | cites P0001 or P0002 or P0003 or P0004 or P0008 | P0008 stays although its relaxed fit has no stock; unisex shells count. | ok (the user) |
| 15 | IC-024 | UK | My budget is £100 and I want a quick-dry bath sheet for after swimming. Which one would you recommend? | cites P0156 or P0159 or P0160 | The one query with a budget. I checked `prices.jsonl`: every UK bath-sheet price of the three is under it. | ok (the user) |
| 16 | IC-027 | CA | My dining table seats eight and I'd like a machine-washable tablecloth in linen rather than a blend. Which do you recommend? | cites P0189 or P0191 or P0192 or P0193 or P0194 or P0195 | "Linen rather than a blend" excludes linen-cotton; seats eight maps to 60 x 102 in through the guide. | ok (the user) |
| 17 | IC-040 | US | Do you have the Corvane Moor Fleece Jacket in Glacier Blue, size XL, regular fit, in stock? | cites P0037; facts does not come or doesn't come or not available in … (57 wordings) | "Does not come" is matched from a long wording list ("don't have" is loose). IC-039 is the same. | ok with a change (the user) |
| 18 | IC-045 | UK | I bought a fleece in the clearance section. Can I still send it back? | cites POL-RETURNS-UK; facts 14 days or 14-day or 14 day … (5 wordings); absent final sale | UK clearance is 14 days. An answer saying "not final sale" trips `absent`. | ok with a change (the user) |
| 19 | IC-046 | CA | Will sending an item back cost me anything? | cites POL-RETURNS-CA; facts deducted or deduct or deducts … (8 wordings); absent returns are free, prepaid return label, drop-off point, paid by you | The fact is only "deducted"; "you pay a fee" fails. | ok (the user) |
| 20 | IC-047 | EU | Will I be charged customs duties when my parcel arrives? | cites FAQ-030; facts no import duties or no import duty or no duties … (15 wordings); absent uk warehouse | Many phrasings; a bare "No" fails. | ok (the user) |
