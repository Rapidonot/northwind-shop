# Row #31 (RAG-07): labelling guide for the 60 routing queries

Fixed on 2026-11-25, before any router exists. The labels in `evals/fixtures/rag07_queries.jsonl` follow this guide, and
the router is built to it afterwards. Once frozen, a change to a label or a rule needs the user (plan, stop).

## Criterion and scoring rule
RAG-07: "Routing accuracy at least 90% on labelled queries."
- An item is **correct** when the router's set of indexes equals the label's set exactly. Order does not matter.
- **Pass:** at least 54 of 60.
- **Reported, not gated:** accuracy per category, and per index (how often each index is wrongly added or left out).

## The three indexes
There is one table. An index is a group of source types in it:

| Index | Holds | Answers |
|---|---|---|
| `specifications` | products, size guides, care guides | what an item is made of, does, weighs, comes in, fits like, and how to wash it; which item to pick |
| `reviews` | product reviews | what other shoppers experienced or think |
| `policy` | returns and shipping policies, FAQs | how the shop works: returns, exchanges, delivery, taxes, payment, promotions, orders, contact |

## Rules for the label
1. **By what is asked, not by which words appear.** "Can I return the jacket if the zip breaks?" is `policy` only.
2. **Reviews** when the message asks for other people's views ("what do people say", "do reviewers mention", ratings), or
   for an experience the catalog does not state: comfort, itchiness, softness in use, smell, noise, pilling, how it holds
   up, how it wears or ages, whether it is worth it.
3. **Specifications** for every stated attribute, size and fit questions, care and washing, comparisons of attributes, and
   a pick or recommendation. Also price, stock and availability of an item: the item has to be found, and the live tools
   answer the rest.
4. **Policy** for how the shop works, with or without a named product.
5. **"Does it run small?" / "true to size?"** is `specifications` and `reviews`: the catalog states it and shoppers report
   on it. Naming reviewers ("do reviewers say it runs small?") makes it `reviews` only.
6. **Two asks, two indexes.** Each separate ask adds its index. "Is it waterproof, and can I return it?" is
   `specifications` and `policy`.
7. **A pick that asks for shoppers' opinion** ("best-reviewed fleece for hiking") is `specifications` and `reviews`.
8. **Unsure means all three:** a greeting, thanks, chat with no product or shop topic, a message too vague to place
   ("tell me more"), or one carrying text aimed at the system ("SYSTEM: …", "ignore your instructions").
9. **A region, currency, audience or visibility word never changes the label.** "Show me the internal returns policy" is
   `policy`.
10. **FAQ overlap:** FAQ-006 (find my size) and FAQ-013 (keep a jacket waterproof) repeat the guides, so such questions are
    `specifications`. Questions only FAQ-014 (down sourcing) answers are left out of the set.

## Item format (one JSON object per line, keys sorted, UTF-8)
```json
{"category": "reviews", "id": "RT7-001", "indexes": ["reviews"], "query": "…", "region": "US", "reviewed_by": "model",
 "why": "one short line: why this label"}
```
- `id`: `RT7-001` to `RT7-060`, in file order, grouped by category in the order of the table below.
- `indexes`: sorted, one to three of `policy`, `reviews`, `specifications`.
- `region`: `US`, `CA`, `UK` or `EU`, mixed across the file.
- `reviewed_by`: always `model` in the draft. Only the user writes `human`.

| Category | Count | Label |
|---|---|---|
| `specifications` | 16 | that one index |
| `reviews` | 14 | that one index |
| `policy` | 14 | that one index |
| `multi` | 12 | exactly two indexes: at least 3 of each of the three pairs |
| `all` | 4 | all three: at least one vague, one chat, one carrying injected text |

Each index is then in at least 20 labels.

## Rules for the queries
- They read like a real shopper's, vary in length and phrasing, and include typos or fragments in at least 6.
- At least 15 use a word typical of another index without asking for it ("return", "review", "policy", "fit", "washed").
- Any named product is public, in `data/fixtures/products.jsonl`, and sold in the item's region. No real brand or person.
  No catalog price or stock count. Policy facts are not needed.
- No query repeats one in `evals/fixtures/` or in another item.
- `data/fixtures/_answer_key.json`, `evals/src/`, `services/` and cassettes are not read while drafting.

## Review sheet
About 20 of the hardest items go on one sheet for the user. The rest stay `model`.
