# 0003: Containment in the eval gate is absolute

Date: 2026-11-04. Requirement: RAG-12. Decided by the user after the second security review.

## Context

The RAG-12 gate compared scores with a baseline, so an adversarial item that already failed in the
baseline was never gated, and one override could remove an item's protection for good.

## Decision

1. Any `behaviour_ok` failure on an adversarial or unanswerable item fails the gate, whatever the
   baseline says. The user's wording was adversarial containment; unanswerable items are held to
   the same rule because a failure there is a fabricated attribute (hard rule 5).
2. The only exception is `evals/fixtures/known_failures.json`. Each entry names the PROGRESS row
   expected to fix it. Only the user sets `approved_by: human`, and the gate refuses anything else.
   An entry that now passes must be removed, so the list can only shrink without a recorded approval.
3. `make eval-live` and `make eval-rescore` write a candidate. Only `make eval-promote` replaces the
   baseline. A regression needs a reason, which is stored in the baseline. Containment failures,
   dropped items and lost metrics cannot be overridden.
4. Every adversarial and unanswerable item must stay in the baseline.
5. Groundedness is not scored on an answer that declines. A fabrication on an unanswerable item is
   still scored.

6. Recorded evidence is checked, not trusted. Replayed answer scores must equal the baseline exactly,
   its summary must match its scores, and `make eval-integrity` refuses any modified cassette or lost
   override, compared with `origin/main`. Promoting with no baseline needs a recorded reason.

## Consequences

- Promotion is a user step, so `make ci` stays red on a baseline that doesn't meet rule 4.
- A zero-tolerance item cannot be deleted or relabelled through the tools at all. That needs a new
  decision and a visible hand edit to the baseline.
- `approved_by` and override reasons are plain strings, so they record a claim, not proof. Stronger
  proof needs something Claude cannot produce: permission rules, a second reviewer or signed commits.
- After the credits expire, stale recordings need `EVAL_ALLOW_STALE=1` in CI, which is a separate
  decision to record then.
