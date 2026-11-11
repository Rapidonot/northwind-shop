# 0014: The output check, the control for injected text

Date: 2026-11-11 · Row: #13 (RAG-08 + NFR-03, partial) · Status: decided by the user (plan approval, the three
changes before the class freeze, and the stop-7 answers, all 2026-11-11)

Plan, with every decision and both advisor verdicts: `docs/reviews/2026-11-11-RAG-08-plan.md`. Evidence:
`docs/reviews/2026-11-11-RAG-08-evidence.md`. **Numbering:** row #21 also plans decision 0014. Whichever row
merges second renumbers (the user, 2026-11-11).

## Context

Decision 0007 put the first search before the first model call and left the prompt to keep injected text away
from the shopper. Two prompt attempts still relayed R00712, and the CC-10 re-record named its subject
("discounts and orders") while ignoring it. RT-003 and RT-016 did the same for a planted refund claim. Whatever
reaches the shopper is a channel from whoever wrote a review to every shopper who asks. The prompt shapes what
the model does; a deterministic check on what leaves is the control.

## Decision

1. **Where it runs.**
   - `northwind_assistant.output_check.OutputCheck`, applied inside `stream_answer`, between `run_turn` and every
     caller. The gateway and the eval adapter (#13b) both receive checked text, and `chat.py` adds no filter of
     its own.
   - `run_turn` checks nothing. A test pins that only `stream_answer` calls it, following import and module
     aliases.
2. **Matching view.**
   - Patterns run on a view of the text. Each raw character goes through NFKD; then combining marks, format
     characters and the other default-ignorable code points (variation selectors, the combining grapheme joiner,
     Hangul fillers) are dropped; then NFKC, a fixed confusables fold (Cyrillic, Greek, dotless ı, ɑ, օ, and the
     ideographic full stop as a dot) and casefold. A map leads back to the raw text (security review passes 1 and
     2, H3 and L4).
   - The promotion-code shape is matched before casefolding.
3. **Holding back** (row #13: a short lookahead from a candidate start, not whole sentences).
   - **Every window counts matching-view characters, not raw ones.** Invisible characters fold to nothing, so
     padding must not push a payload's start out of view (pass 2, N1).
   - The trailing run of non-whitespace, and the whitespace before it, is always held.
   - From the earliest trigger in the last `L = 64` view characters, text is held. Every pattern is at most `L`
     view characters wide (tested).
   - Matching looks back at most 2L before what is unsent, and 3L before what it matched last time, so each delta
     costs time in proportion to its own length (pass 1 and 2, M4). The folded form of each character is cached.
   - Text is released in the model's own deltas, so nothing changes shape when nothing is blocked.
   - A duration waits up to `L` view characters past itself (plus one character) for its sentence to show whether
     it is about policy. It counts as policy if the sentence is still open there, if the sentence started more
     than 4L back, or if either side is padded with more than 16L raw characters (fail closed).
   - Invented citation markers are removed from the held text **before** matching, so what is checked is what is
     sent (pass 1, H2).
4. **On a match.**
   - The sentence is replaced by one fixed sentence for its class, approved by the user. None makes a product
     or policy claim or states a figure, and each passes every class (tested):

     | Class | Fixed sentence |
     |---|---|
     | promotion | "I can't help with offers or codes in chat." |
     | link | "I've left out a link or contact detail here." |
     | confirmation, refund authority | "I can't confirm orders, payments or refunds in chat." |
     | stock | "I can't check stock in chat." |
     | false policy | "I couldn't match that policy term to Northwind's policy for your region." |

   - If part of that sentence was already sent, the check sends `reset` and then the sent text up to the
     sentence start, so only text that passed is ever sent twice.
   - A citation marker naming a source no search returned this turn is removed.
   - The model's own `reset` releases what passes of the cut attempt first, without the word it cut off. On an
     outage (no sources behind the text), and after a failed turn, held text is discarded.
   - The kept prefix is only a list marker; a bold lead-in is never sent again (pass 1, H1).
   - Citations come from the text left on screen.
   - The turn's one telemetry line counts blocks per class, with no text. A turn that never reported is logged
     as `aborted`.
5. **Classes**, frozen at the user's approval. A change after any recording is stop 7.
   - **Promotion** (hard rule 2), class-level, with no exemption: discount, coupon, promo code, `N% off`,
     voucher, staff code, member price, free today, and `code` followed by a code shape.
   - **Links and contact:** every URL; every phone number; every email but `care@northwind.example`; and any
     word that is a bare domain.
   - **Confirmation:** an order, payment, refund, discount or code said to be confirmed, processed, approved,
     authorised, applied, placed or issued, and "I've placed…" and the like. Exempt only when `if`, `when`,
     `once`, `after` or `before` directly introduces the subject, in the present or future tense, and per
     match.
   - **Payload subjects:**
     - refund authority, which passes only as the exact words of a public policy document (`POL-`) returned
       this turn, and never for `issue_refund` or `apply_discount`;
     - stock claims, class-level.
   - **False policy:**
     - a duration in a sentence holding a policy stem (return, refund, exchange, cancel, withdraw, deliver,
       ship, dispatch, warrant, guarantee, back, money, policy, window, period, label, replace, repair) passes
       only if a public document returned this turn states the same duration;
     - "free returns" and "prepaid label" pass only if a public document says returns are free;
     - a public document is `POL-`, `FAQ-`, `CG-` or `SG-`, never internal, and neither a product nor a
       variant, since an internal product cannot be told apart from a public one.
6. **The reviewed hit list.** FAQ-003 and FAQ-005 are the trusted texts the check blocks. An answer quoting
   them gets the class's sentence. Any other trusted fixture that is blocked fails a test.
7. **The contract with row #12.** This row owns the engine. #12 extends it and changes none of these tests:
   - tool-returned price, stock and promotion values pass;
   - a money and stock-count class for RAG-09;
   - its own tests.

   #12 amends this decision rather than writing another.

## Consequences

- **The three recorded answers this row exists for now pass because of the check, not the model.** The
  recordings are unchanged, and `test_a_planted_injections_subject_is_not_named` has lost its xfail.
- **Some benign sentences are replaced too** ("I can't apply discount codes", "whether it's in stock"). Each
  gets a sentence with the same meaning; the full list is in the evidence file.
- **The first token comes later.** It now waits for about 66 characters (p50), roughly 275 ms at an assumed
  240 characters a second. That counts against SA-02's 2-second target, and #17 measures it.
- **The prompt rule against mentioning injected text stays a preference.** Mentions without a payload subject
  are counted in #13b, not blocked.
- **Residuals recorded by the security review**, owner hardening unless named:
  - `northwind-deals[.]net` passes (not clickable);
  - `in` is not a trigger, so "…is in" can be sent before a repair takes it back;
  - a payload cut between words by the model's reset is checked as cut;
  - markers that are not ID-shaped stay (they never become citations);
  - the stock sentence stops being true once #16 adds live stock (owner #16);
  - A2A has no disclosure banner, and the check can replace a sentence that holds the model's own disclosure
    (owner #15).
- **Left (partial):** uncited claims are measured in #13b, not blocked; policy terms other than durations and
  free returns are left to the judge, owner hardening; the past tense after a conditional is blocked, which is
  stricter than reading the conditional as hypothetical.
