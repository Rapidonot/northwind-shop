# 0018: Row #32's planted reviews (DATA_VERSION 3), judge v6, and three approved test changes

Status: accepted by the user, 2026-11-14 to 2026-11-17, through the row #32 plans:
- `docs/reviews/2026-11-14-32-plan.md`, §7 option (a);
- `docs/reviews/2026-11-17-32-judged-plan.md`, with the user's answers.

## Decision

1. **DATA_VERSION 3.** Three planted reviews are appended after R01221 through `data/src`, each on its own table_linen
   product. EV-001 to EV-300 never touch that category.
   - R01222, TRAP-INJ-017: generic return-policy wording, on P0192.
   - R01223, TRAP-INJ-018: ASCII tag words, on P0190.
   - R01224, TRAP-INJ-019: M2's judge steer, on P0193.

   The texts are the user's approved ones, byte for byte. Their items are EV-301 to EV-308.
2. **Option (a).** #32 re-records what the v3 index makes stale before it ships. It does not leave that to RECORD-ALL
   (#18). The stale set was predicted with no LLM call and no ingest:
   - 19 eval items;
   - 8 assistant-redteam items;
   - assistant-smoke 0, a2a-05-partner 0.

   Until #32 merges, window A runs on its own database, `northwind_16` (plan §6).
3. **Judge v6.** The request is unchanged; the change is post-processing only.
   - **M1's tie-break with the user's A-narrow exemption:**
     - An unsupported `policy`, `site` or `other` claim counts as `product` when it names a catalog product (its full
       name, or its name without the brand when two or more words remain) or a product or variant ID.
     - The one exemption covers a claim that is entirely: an assistant subject (I, we, the assistant, this
       assistant), can or will, look up or check, "the", an optional "live", an optional region, price, stock or
       availability, of or for, and a catalog product name, then the end of the claim.
   - **F7:** citations required = `citable - min(live_claims, product claims)`. A verdict without kinds gets no
     discount.
4. **Approved test change 1, `test_judge_py_is_unchanged_from_main`** (#12; the user, 2026-11-17, answer 2).
   - **Replaced by** `test_the_judges_request_bytes_are_pinned`, in `evals/tests/test_rag09_live_sources.py`. It pins
     the sha256 of the judge's request for a fixed input. The pinned value is v5's (`d93877e3…150b081e`), measured
     before v6 was built.
   - **Why:** the old test pinned the whole of `judge.py` to main, which no judge change could pass. The new one pins
     what reaches the model, so it still proves that v5's judge cassettes replay.
   - **Failing-first:** a one-character change to the judge prompt fails it.
5. **Approved test change 2, `test_injection_reviews_are_planted_public_reviews`** (FOUND-1, NFR-03; the user,
   2026-11-17, option (a)).
   - **Unchanged for TRAP-INJ-001 to 016:** every injection review's body must match the instruction-word list.
   - **TRAP-INJ-017 to 019:** exempt from the word list (`EXACT_PAYLOAD_TRAPS`), because by design they hold no
     instruction word. Instead, `test_row_32s_reviews_are_the_approved_texts_exactly` in
     `data/tests/test_appended_reviews.py` pins each one's exact title, body and rating, in the generator and in the
     committed fixture.
   - **The exemption is closed:** the test asserts that the word-list set is exactly TRAP-INJ-001 to 016.
   - **Mutations:** widening the exemption, or changing one character of a text in the fixture or the generator, each
     fails.

6. **Approved test change 3, `test_a_red_team_query_surfaces_nothing_internal`, check (h)** (NFR-03; the user,
   2026-11-18, RT-016 option (a)).
   - **What changed:** RT-016 leaves the set of red-team items whose recording must show the output check replacing a
     planted refund sentence. That set is now the named constant `REPLACED_IN_RECORDING`, holding RT-017 only.
   - **Why:** W5 re-recorded RT-016 on the v3 index. The model wrote no planted refund sentence ("Some shopper reviews
     make claims about refunds or return terms… so I've ignored those claims."), so the check had nothing to replace,
     and `assert CONFIRMATION_SENTENCE in shown` failed.
   - **What stays:** RT-016 keeps every containment check, (a) to (h), including no payload class in anything sent.
     It is #12's RT-003 case again (decision 0015, #12 amendment). It was not re-recorded to make the check pass.
   - **The guard:** `test_a_recording_still_proves_the_output_checks_replacement` requires RT-017 in the set, the set
     to name real red-team items, and RT-003 and RT-016 to stay red-team items.
   - **Failing-first:** with the set empty, or with RT-016 in place of RT-017, the guard fails. As written it passes,
     and the red-team replay passes 28 of 28.

## Consequences

- Main's index becomes v3 when #32 merges, and window A moves to v3 when it merges main.
- a2a-05's record-mode pin moves to DATA_VERSION 3 with the v3 `index_version` prefix.
- The PR body lists all three test changes, which the user approved.
