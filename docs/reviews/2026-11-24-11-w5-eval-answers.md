# Row #11, window 5: `eval-answers` (2026-11-24): stopped before the promote on a `variant` regression

- **Window and head:** opened by the user with the guarded command at head `e68f1a7`. That is the
  `make eval-snapshot` commit: 48 of 308 contexts changed (41 from the rules, 7 routed to Haiku), and the retriever
  is v3 on all 308. Runner HEAD = `origin/main` `f84f97d`. DB `northwind_16`.
- **Command:** `make eval-live EVAL_SLICE=308 DATABASE_URL=…/northwind_16`. Exit 0; all 308 items "ok"; 0 refused.
  It wrote the candidate itself, so `eval-rescore` is not needed.
- **Spend:** `make llm-spend` total $26.780250 → $27.387333, which is **$0.607083**.
- **Hit log (this run's entries only):**
  - Answerer calls: 74 recorded, 279 hits.
  - Judge calls: 48 recorded, 260 hits.
  - **Exactly 48 items recorded.** They are the 48 changed contexts. The other 260 were all hits.
- **Cassettes:**
  - 122 added; 0 modified.
  - 118 old cassettes were not read. **They are kept, not deleted**: the fix below would replay most of them.
  - Scan clean. 0 refusals in the new cassettes.
- **`refinements.ndjson`:** removed `(EV-065, 1)`; added `(EV-050, 1)`; changed none.
- **Identity:**
  - `retriever_id` v2 → v3, as expected.
  - `answerer_id` differs from the promoted baseline on its check hash, `check-a1e0e605` against `check-74c8ba9d`.
    This is #17's accepted state (the user accepted a1e0e605 without a second promote), not this row.
  - `judge_id` and `index_version` are unchanged.

## Seven gated metrics against the promoted baseline (`gate.py` tolerances)

| Metric | Baseline | Candidate | Change | Within tolerance |
|---|---|---|---|---|
| recall@5 | 0.930233 | 0.925581 | −0.004652 | yes |
| mrr | 0.888726 | 0.885083 | −0.003643 | yes |
| groundedness | 0.977082 | 0.970643 | −0.006439 | yes |
| relevance | 0.926948 | 0.900974 | −0.025974 | yes |
| false_refusal (lower is better) | 0.009302 | 0.009302 | 0 | yes |
| behaviour_ok | 0.977273 | 0.948052 | **−0.029221** | yes, by 0.0008 |
| citation_ok | 0.977273 | 0.977273 | 0 | yes |
| *fabricated_ok* (reported, not gated) | 0.983766 | 0.967532 | −0.016234 | n/a |

**Containment:** `adversarial` and `unanswerable` `behaviour_ok` are both 1.000.

**Segments that moved by 0.03 or more:**

| Segment | Metric | Before → after |
|---|---|---|
| `variant` | `behaviour_ok` | 0.844 → **0.594** |
| `variant` | `relevance` | 0.812 → 0.594 |
| `variant` | `fabricated_ok` | 0.906 → 0.719 |
| `variant` | `groundedness` | 0.918 → 0.869 |
| `variant` | `citation_ok` | 0.938 → 0.906 |
| `variant` | `recall@5` | 1.000 → 0.969 |
| `ambiguous` | `behaviour_ok` | 1.000 → 0.950 |
| `ambiguous` | `relevance` | 0.975 → 0.925 |

## The items that moved

19 items moved, all among the 48.

- **`behaviour_ok` lost on 10:** EV-042, EV-047, EV-204, EV-208, EV-214, EV-217, EV-219, EV-221, EV-223 and EV-263.
  - **Gained** on EV-220.
- **Other changes:**
  - EV-044 lost `fabricated_ok` and `citation_ok`; EV-206 lost `fabricated_ok`.
  - EV-209 gained a false refusal.
  - EV-065's relevance fell to 0.5.
  - EV-213 lost `citation_ok`.
  - EV-050, EV-190 and EV-212 gained `citation_ok`.

**Cause (a design flaw in this row; not noise):**
- Each item that lost something names a product and asks for a colour-and-size combination that does not exist.
  - Example, EV-042: "Is the Corvane Ridge Fleece Jacket in Glacier Blue available in 4XL?"
- The colour and size filters then drop every variant chunk, since each variant has one colour and one size.
  Reviews and documents fill the top 5.
- So the evidence that answers "no, it comes in these" is gone.
  - Before: EV-042's context was the product and 4 Glacier Blue variants.
  - After: the product and 4 reviews.
- All 13 regressed items (those that lost any metric: the 10 above, plus EV-044, EV-065 and EV-206) name a
  product. So do 40 of the 48 changed items.

## Asked-for counts
- **D8** (#17's script, at `/tmp/…/d8.py`): 29 claims on 24 items.
  - check 17, self 8, other 4.
  - **12 model-made, 9 unasked.** #17 had 10 (7).
  - The check class rose mostly through the stock fixed sentence on the regressed `variant` answers.
- **Money replacement on price items:**
  - The price-filter items are EV-029 and EV-030. The replacement fired on 0 of 2.
  - EV-029 compares the shopper's $150 limit with live prices from `get_price` that turn ($149.00 and $150.00).
    It states no threshold as fact without a live price.
- **The 7 Haiku-routed items:**
  - EV-018, EV-067, EV-102, EV-272 and EV-284 are unchanged against the baseline.
  - EV-190 gained `citation_ok` (groundedness 1.0 → 0.83).
  - EV-307 groundedness went 0.8 → 1.0.
  - All 7 have `behaviour_ok` true.

## Not promoted. The proposed fix needs the user's decision
- **The fix:** for a message that names a product, run the plain first search (no colour, size or live-filter
  narrowing). Filters are still extracted and reported, so RAG-04's 99/100 does not change.
- **Expected effect:**
  - The 40 named-product items get their pre-row search back. Their old cassettes are still on disk, so a re-run is
    mostly hits.
  - About 8 items keep a changed context: EV-018, 029, 030, 031, 035, 067, 068 and 190.
- **Then:** `eval-snapshot`, a short W5 re-run, and the promote.
