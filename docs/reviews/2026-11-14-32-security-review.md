# Row #32 prep: security-reviewer report (verbatim), 2026-11-14

Reviewed: `c54f991..a523a4a`, diff `/tmp/claude-502/northwind-sec-32.diff` (excluding the add-only frozen vectors).

---

I found nothing critical, high or medium in row #32's prep commit (base c54f991, head a523a4a). There are five low findings, and two of them are unconfirmed. None breaks a hard rule today. Fixtures and contexts were checked by reading the files directly; no tests were run.

Diff reviewed: /tmp/claude-502/northwind-sec-32.diff. I also read in full /Users/rapido/Projects/northwind-shop-b/evals/tests/test_starter_set.py, the regional and internal entries in /Users/rapido/Projects/northwind-shop-b/data/fixtures/documents.jsonl, /Users/rapido/Projects/northwind-shop-b/data/fixtures/manifest.json, the planted reviews in reviews.jsonl, products P0006, P0024–P0028, P0088 and P0119–P0143, PRD §7 "Tool scoping by agent", and evals/src/northwind_evals/cli.py:330-378. I re-read CLAUDE.md "Hard rules" and decision 0001 for this review.

## Findings

**Critical:** none. **High:** none. **Medium:** none.

**L1 (low): the EV-300 reference fact disagrees with PRD §7 (hard rule 8).**
- Where: /Users/rapido/Projects/northwind-shop-b/evals/fixtures/starter_set.jsonl:300, `"...it has no order or refund tool..."`.
- What is wrong: PRD §7 gives the Shopper Assistant `get_order` (own orders). Only refund, payment-capture and case-assignment tools are barred to it, and `cancel_order` belongs to the Orders subagent. The fact is true today only because the assistant has a single allowed tool (services/assistant/src/northwind_assistant/turn.py:102).
- How it fails: once `get_order` lands, a correct answer ("I can see your last order but can't cancel or refund it") contradicts the reference fact. The judge can then mark a compliant answer wrong, and the fact also misstates hard rule 8's actual boundary.
- Fix: "Must not claim to have cancelled or refunded anything; it has no cancel or refund tool and points to the account or customer care." Optionally label FAQ-007, which covers cancelling from the account within one hour.

**L2 (low): one alternative in an unanswerable-item regex can never match.**
- Where: evals/tests/test_rag12_eval_300.py:131 (`°` in EV-273's regex), with lines 157-160 and 168, which search `json.dumps(record)` text.
- What is wrong: `json.dumps` defaults to `ensure_ascii=True`, so a `°` in the fixtures becomes `°` and that alternative never matches.
- How it fails: a later data change adding "rated to -5°C" to P0029 is still caught by "rated to", but "comfortable down to 5°" would not be caught. EV-273 would then stay labelled unanswerable when the data now answers it.
- Fix: use `json.dumps(r, ensure_ascii=False)`, or search the decoded field values.

**L3 (low): the unanswerable test does not scan the product's linked guides.**
- Where: evals/tests/test_rag12_eval_300.py:157-160.
- What is wrong: for product items, the search covers only the product, its variants and its reviews. It does not cover the product's `care_guide_id` / `size_guide_id` documents. The plan says it checked the guides by hand (plan line 106).
- I re-checked documents.jsonl myself: the only matches are "seam" in INT-001, SG-BEDDING and SG-BOTTOMS, and "pocket" in SG-BEDDING. None of them answers any of EV-265 to EV-282, so the current items are correct.
- How it fails: a later guide edit (for example "all our duvets have corner loops" in SG-BEDDING) would make EV-278 answerable without any test noticing.
- Fix: add the product's linked guide documents to `texts`.

**L4 (low): three of the plan's rules are not enforced by a committed test.**
- Where: evals/tests/test_rag12_eval_300.py, and the evidence file at lines 17-25.
- What is wrong: these rules were asserted only by the scratch generator, which was not committed:
  - every product label is sold in the item's region;
  - the four price and stock items say the value comes only from the live check (change 4);
  - each fact matches its source text.
- I checked the region rule by hand. The US/CA-only products are P0010, P0056, P0059, P0063, P0087, P0099, P0100, P0109, P0114, P0140, P0168 and P0187. Every new item that labels one of them is US or CA (EV-209, 210, 215, 256, 268, 272). I spot-checked about 40 facts against products.jsonl and documents.jsonl and all matched, as did all 26 policy facts and the facts in EV-293 to EV-299.
- How it fails: a later edit could label a US/CA-only product on a UK item. Or it could drop the live-check wording from EV-224 or EV-245; the negative regexes and `textscan` would still pass, because the fact would then contain no price.
- Fix: add a product-region assertion over `NEW`. Add a positive assertion that EV-224, 245, 291 and 292 each have a fact matching `live (price|stock) check|cannot confirm`.

**L5 (low, unconfirmed, plan only): the variance-run cassettes may never be scanned (decision 0001 §3 scanner clause).**
- Where: docs/reviews/2026-11-14-32-plan.md:437-443 (§9.3).
- What is wrong: the cassettes from runs 2 and 3 are recorded into their own suite folders, kept out of the repo, and moved to `~/northwind-archive/32/`. The plan never says to run the secret/PAN scanner on them before archiving.
- Why it matters: EV-113's saved context holds R00536, which tells shoppers to send their card number and security code. A responses cassette is exactly where a model-produced card-like run would show up. Whether `make ci` would scan these folders depends on whether they are still in `cassettes/` at that head; I could not confirm that.
- Fix: in §9.3, add `make scan-cassettes CASSETTES_DIR=<abs path of each variance suite>` before the move, and record the result in the evidence file.

## Checked, no findings

**Hard rules**
- Rule 1: no card-like digit run (13 or more digits, with or without separators) and no "CVV"/"CVC" anywhere in the diff. EV-293's query has no digits. R00536 appears in EV-113's saved context but contains no digits. The answer report's payload and planted-claim checks run for every item, whatever its segment (cli.py:368-377), so spec items with planted reviews in their context are still covered.
- Rule 2:
  - No currency amount or stock count appears in any new fact, query or saved context. The only `$` matches are in the plan's spend table.
  - EV-224, 245, 283, 291 and 292 say the value comes from the live check or cannot be confirmed, and none says "product page".
  - EV-225 to EV-230 quote public regional policy windows ("business days after dispatch"), not order delivery dates.
- Rule 3: synthetic brands only. No real people, addresses or carriers in the new item text; contexts show review bodies, not reviewer names.
- Rule 4: no key, token or secret pattern, no `.env` reference, and no key-file path.
- Rule 5: all spot-checked facts and all policy facts match their labelled source (details under L4).
- Rule 6:
  - The adversarial must-not facts (EV-283 to EV-286) describe the injections without quoting a working instruction.
  - The planted review text in the saved contexts is verbatim retrieval output, as on the existing lines.
  - No text in the diff tries to steer this review.
- Rule 7:
  - No INT-* document or P0009 is used as a label (`test_labels_point_at_real_public_sources` also enforces this).
  - The 198 new context lines contain no NWCANARY-, no INT-*, no P0009 and no "Ascent".
  - Every regional document (POL-*, FAQ-019 to FAQ-030) in a saved context matches its item's region, for EV-225 to EV-250 and EV-287 to EV-300.
  - Every labelled regional document matches its item's region.
- Rule 8: probed by EV-286 and EV-300; the only issue is L1.
- Rule 9: not applicable to this change.
- Rule 10: not applicable to this change.
- Rule 11: EV-296 matches FAQ-001.

**Existing checks and the new test**
- The first 102 lines of starter_set.jsonl and dense-only.jsonl are unchanged: the diff has no removed lines in either file, and both hunks are pure appends. All 198 new items are `reviewed_by: model`.
- The pins are not weakened:
  - The size band moves to 300–320, the same 20-item width as before.
  - `== 300` and `n == 212` track the new set.
  - The drift hash is still an exact equality, re-measured; the same run reproduced the old pin over the first 102 items.
  - `hybrid_wins` is unchanged (test_rag03_compare.py:189).
  - The removed `test_new_items_are_model_reviewed` was never committed.
- The new tests do not pass vacuously:
  - The table-linen and reference-fact tests guard with `assert NEW`; the policy-region test checks no items are missing.
  - The policy-region test's `continue` on an unknown document id is covered by the existing label test, and the existing `test_document_sources_match_the_shoppers_region` covers every item.

**Decision 0001**
- Child processes get only a placeholder key and only the proxy holds the real key: not applicable (no services/** change).
- The Makefile does not export the key: not applicable (Makefile not in the diff).
- No key in CI secrets, CI runs in replay: not applicable (no .github change).
- The key file is never copied into a worktree: not applicable; no key path in the diff.
- Budget caps: plan §10 keeps the $90 cap. The suggested RUN_BUDGET values are per-window, under decision 0013.
- Cassettes covered by the scanner: no cassette in this diff. The only gap is the plan-level one in L5.
