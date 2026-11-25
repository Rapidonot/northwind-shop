# Rows #30 and #22 (SA-04, SA-03 partial): labelling guide and scoring rules

Fixed on 2026-11-25, before any implementation. The labels in `evals/fixtures/sa04_prompts.jsonl` (20) and
`evals/fixtures/sa03_conversations.jsonl` (50) follow this guide, and the scorer is built to it afterwards. Once
frozen, a change to a label or a rule needs the user (plan, stop 5).

## Criteria
- **SA-04:** "On 20 deliberately ambiguous prompts, the assistant asks before recommending in at least 90%": 18 of 20.
- **SA-03 (partial):** "Each intent has at least 10 labelled test conversations and a pass rate reported." Five intents,
  10 each. The rate is reported per intent, with no threshold.
- **A conversation is one shopper message and its answer:** each message stands alone (SA-05 owns memory). SA-04 is
  scored on the first answer.

## Both files
- One JSON object per line, keys sorted, UTF-8. `region`: `US`, `CA`, `UK` or `EU`, mixed across the file.
- `reviewed_by` is always `model` in the draft. Only the user writes `human`. `why` is one line.
- Queries read like a real shopper's. They name only real, public products from `data/fixtures/products.jsonl`
  (`visibility` public, sold in the item's region), no real-world brand or person, and no catalog price or stock count.
- No query repeats one in `evals/fixtures/starter_set.jsonl`, `evals/fixtures/rag04_queries.jsonl` or the other file.
- Every fact is read from `data/fixtures/`, never from memory. `data/fixtures/_answer_key.json` is not read.

## SA-04: `CL-001` to `CL-020`
```json
{"accepts": ["size"], "department": "Bedding", "id": "CL-001", "prompt": "…", "region": "US",
 "reviewed_by": "model", "why": "…"}
```
- The prompt asks for a pick ("which … should I get", "recommend …", "I need a …") and leaves out what decides it.
- `accepts`: the slots whose question counts, from `size`, `budget`, `use`. Any one is enough.
- **Deliberately ambiguous** means at least two public catalog products, or sizes, fit the words, and the right one
  turns on an `accepts` slot. Name the deciding attribute in `why`.
- Balance: at least 6 prompts with each slot in `accepts`, at least 6 departments, no prompt naming a catalog product.
- A prompt that gives the missing slot, or says "any" or "no preference", does not belong here.

**Scoring an answer** (the text left on screen). It passes when both hold:
1. **It asks about an accepted slot.** Asking text is a sentence that ends in "?", or one that begins "tell me", "let me
   know", "please tell me", "could you" or "can you"; and the list lines directly under such a sentence. It must hold a
   word of an accepted slot (whole words, case-insensitive, plurals included):
   - `size`: size, fit, dimension, measurement, measure, twin, full, queen, king, chest, waist, inseam, mattress, how
     big, how large, how tall, wide, inches, feet
   - `budget`: budget, price range, spend, how much … pay, price limit
   - `use`: use, using, used, activity, activities, occasion, weather, conditions, climate, season, temperature, where,
     what for, indoor, outdoor, hiking, travel, everyday, layering, room
2. **It recommends nothing.** No catalog product is named (its full name, or brand plus model word) and no product or
   variant ID is cited, anywhere in the answer, the question included. Guide, FAQ and policy citations are fine.

An error, an empty answer or an output-check fixed sentence alone fails.

## SA-03: `IC-001` to `IC-050`
```json
{"absent": [], "cites": [["P0001"]], "facts": [["20,000 mm", "20000 mm"]], "id": "IC-001",
 "intent": "product_question", "query": "…", "region": "US", "reviewed_by": "model", "variants": [], "why": "…"}
```
- `intent`: `product_question`, `comparison`, `recommendation`, `availability`, `policy`; 10 each, in that order.
- `cites`: a list of groups. Every group needs at least one of its IDs cited in the answer.
- `facts`: a list of groups of alternative wordings. Every group needs at least one present. Give each group every
  ordinary wording (units, hyphens, "machine washable" / "machine wash"), and keep each wording short.
- `absent`: wordings that must not appear, such as another region's return window. Often empty.
- `variants`: availability only, else `[]`.

| Intent | What the message asks | Required labels |
|---|---|---|
| `product_question` | One named product's attribute, care or size guidance | `cites` (product, guide or both), `facts` |
| `comparison` | Two or more named products on an attribute that differs | one `cites` group and one `facts` group per product |
| `recommendation` | A pick, with size, budget or use already given, so asking would be wrong | one `cites` group with every product that truly fits; `facts` optional |
| `availability` | Whether a named product is in stock in a given colour and size (and fit, when it has fits) | `variants` with the one variant ID; `cites` and `facts` empty. Two items name a colour and size that do not exist together: `variants` empty, `cites` the product, `facts` wordings for "does not come" |
| `policy` | Returns, exchanges, shipping, care policy or payment, for the region | `cites` the region's or `ALL` document, `facts`; other regions' figures in `absent` |

- `recommendation` picks rest on catalog attributes, colours, sizes and fits only, never on price or stock. A budget
  may appear in the message only where every fitting product stays in `cites`.
- `availability`: the label never holds the answer (hard rule 2). Choose variants whose regional stock is zero for at
  least 3 items and positive for at least 4, without writing the figure anywhere.

**Scoring an answer.** It passes when every rule that applies holds:
1. Every `cites` group, every `facts` group, and no `absent` wording. Text is compared lower-case, with dashes folded to
   hyphens, spaces collapsed, markdown removed and commas inside numbers dropped, on both sides.
2. **Availability with a variant:** `check_stock` returned that variant in this turn, and the answer says what the live
   store says at scoring time. Above zero: "in stock" or "available", un-negated. Zero: "out of stock", "sold out",
   "unavailable", "not in stock", "not available".
3. **Recommendation:** the answer is not only a question.
4. An error, an empty answer or an output-check fixed sentence alone fails.

## Review sheet
About 20 of the hardest items across both files go on one sheet for the user. The rest stay `model`.

## Clarifications from drafting (2026-11-25, before any scorer, prompt change or measurement)

The drafting subagent found these gaps. The user decided 3 and 4 on the review sheet. The session
writing them had read recorded answers made with the old prompt, on eval items, so these are not blind.

1. **Matching is by whole words:** a wording that starts or ends with a letter or digit must not touch another one
   ("XL" is not in "2XL", "500" is not in "2500"). Curly and straight apostrophes and inch marks fold together.
2. **`accepts`** holds every slot that splits the fitting set and is not already given. A use that is given (walking to
   work, skiing, travel, a birthday) is left out.
3. **SA-04 slot words added** (the user's answer A, yes, 2026-11-25):
   - `size`: how long, length, seat, guest, people, bath towel, bath sheet, hand towel
   - `use`: warm, warmth, cold, sleep
4. **A live count says "in stock".** An answer that gives the variant's live count ("7 units", "7 left") counts as above
   zero (the user's answer B, yes, 2026-11-25).
5. **Fit** is named in availability messages for every product with a fit, single-fit ones included.
6. **"Men's" and "women's"** include unisex. "Duvet" in IC-030 covers comforters, which share its subcategory.
7. **Recommendation sets ignore stock**, so P0008 stays in IC-021 though its relaxed fit is out of stock.

## Changes at the user's review (2026-11-25)
- **IC-009** (answer C): `absent` gains "not machine-washable", "do not machine wash", "don't machine wash".
- **IC-039, IC-040:** `absent` gains "out of stock", "sold out", "back in stock", "restock", "currently unavailable", "not
  in stock": calling a combination that does not exist out of stock fails.
- **IC-045:** `absent` is "is final sale", "are final sale", "final sale and", "final-sale items can't", so "clearance
  isn't final sale" does not trip it. Two are the user's phrasings, not literal text in another region's document.
