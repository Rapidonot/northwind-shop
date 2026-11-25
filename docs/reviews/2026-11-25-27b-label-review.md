# CHK-07 return labels: independent re-labelling, for the user

A fresh read-only subagent labelled the 68 cases from the four public `POL-RETURNS-*` texts and the case inputs alone:
shuffled, opaque IDs, no labels, reasons, evaluator or plan. It was told to open only those two files, and its run shows
two tool calls. It was told a `duvet` is a filled duvet or comforter (D11) and what each condition means. Only outcomes are
compared. **58 agree, 10 disagree. No label was changed.** The 58 stay `reviewed_by: model`.

Both labels are a model's. Decide each group; a change also moves the evaluator, the sha256 pin and decision 0024, item 6.

## A. Not delivered yet (5 cases)
Policy: "You can return unworn, unwashed items within **N days** of delivery for a refund to the original payment method."

| Case | Input | File | Re-label |
|---|---|---|---|
| RC-014 | US pants, not delivered, unused | ineligible | ambiguous: window runs from delivery; policy silent on undelivered items |
| RC-015 | CA fleece, not delivered, sealed | ineligible | ambiguous: same |
| RC-016 | UK rug, not delivered, not stated | ineligible | ambiguous: same |
| RC-017 | EU duvet, not delivered, wear and tear | ineligible | ambiguous: undelivered yet worn is contradictory |
| RC-062 | UK pants, not delivered, unused, 2 lines | ineligible | ambiguous: window has not started |

**Recommend: keep.** "Not eligible yet, ask again after delivery" is the useful answer, and the plan you approved names
O000928 (not delivered) as the recorded ineligible scenario. RC-017's contradiction changes nothing: rule 1 comes first.

## B. Damaged on arrival (3 cases)
Policy: "You have a 14-day right to cancel from delivery … You do not need to give a reason." (UK; EU: "right of withdrawal").
No policy names damage on arrival; only wear and tear is excluded.

| Case | Input | File | Re-label |
|---|---|---|---|
| RC-035 | UK towel, day 2 | ambiguous | eligible: no reason needed; no exclusion for arrival damage |
| RC-036 | EU towel, day 2 | ambiguous | eligible: same |
| RC-037 | US towel, day 200 | ambiguous | ineligible: far past the window; no arrival-damage extension exists |

The re-label kept US and CA day 2 ambiguous, as the file does. **RC-035, RC-036: the re-label reads the text better;**
`eligible` is the literal answer, `ambiguous` the cautious one (a person sees the damage). **Recommend: keep all three**, so
damage on arrival is always a person's decision, as rule 4 of the approved plan says on purpose.

## C. The same item on two lines (2 cases)
Policy: the window sentence above. No order in data v3 has this.

| Case | Input | File | Re-label |
|---|---|---|---|
| RC-060 | US pants, day 10, unused, 2 lines | ambiguous | eligible: duplicate lines do not change eligibility |
| RC-061 | EU pants, day 90, unused, 2 lines | ambiguous | ineligible: past the window whichever line is meant |

**Recommend: change RC-061 to `ineligible`** (rule 2 moves after the window rule): nothing is unclear there. **Keep RC-060:**
which line and how many units is a real question.

## What you answer
A keep / change, B keep / change (RC-035 and RC-036; RC-037), C (RC-060; RC-061).

## The user's answers (2026-11-25)
- **A: keep** (RC-014 to RC-017, RC-062 stay ineligible). **B: keep** (RC-035, RC-036, RC-037 stay ambiguous).
- **C: RC-060 keep; RC-061 changed to `ineligible`,** the two-lines rule after the window rule, with the evaluator, the sha256
  pin and decision 0024 item 6 in one commit, failing first. It stays `reviewed_by: model`: only the user marks one `human`.
