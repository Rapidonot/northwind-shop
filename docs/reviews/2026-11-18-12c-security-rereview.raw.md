# 12c scoped security re-review (raw, verbatim)

Reviewer: `security-reviewer`, 2026-11-18. Input: `/tmp/claude-502/northwind-12c-security-rereview.diff` (the F3
fix, T19, and PROGRESS.md/COVERAGE.md against origin/main) and the first review.

---

Scoped re-review, row 12c (read-only). Inputs: /tmp/claude-502/northwind-12c-security-rereview.diff and my first review, /Users/rapido/Projects/northwind-shop/docs/reviews/2026-11-18-12c-security-review.raw.md. I also read in full or in part:
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/integrity.py (all of it)
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py:731-772
- /Users/rapido/Projects/northwind-shop/evals/tests/test_context_refinements_integrity.py:1-100 and 280-318
- /Users/rapido/Projects/northwind-shop/.github/workflows/guard/integrity-main.py (its print and import lines)
- /Users/rapido/Projects/northwind-shop/PROGRESS.md:245-248

**Verdict:** the F3 fix is correct and complete for the new message, and T19 pins it. I found no new finding at medium or above, and nothing unsafe. The only medium still open is F1, which stays open by design: it is logged with owner 12b and an interim control. There are three new lows: two about the records and one about how tightly T19 pins the fix. None of them blocks the row.

## 1. Findings (most severe first)

### L1: low. Not unsafe. Hard rule 6 (data posing as output). The F3-rest record is narrower than the code.
Where: PROGRESS.md:248 (the F3, rest clause), compared with integrity.py:179, and with :148-149, :338 and :341.

What is wrong:
- The record says only the duplicate messages in `_frozen_lines` and `_refinement_lines` still echo a key unquoted. My first review said the same, so the record follows it accurately, but both are incomplete.
- integrity.py:179 (`added_frozen_vectors`, `f"{path} ({where}): key {key} is not a vector"`) takes `key` from the HEAD or disk copy, which the branch controls. That message reaches the log unquoted through cli.py:759 and :763. Guard does not call `added_frozen_vectors`, so only the PR-run log is affected.
- :148-149, :338 and :341 also put keys into messages unquoted. Those keys come from the base, but add-only accepts any new key, so an earlier PR could have planted one.

How it fails: a frozen vector line whose `key` holds a newline, and whose vector is not numeric, prints a forged second line such as "eval-integrity passed". The exit code is still 1, so this fails closed, as F3 did.

None of this is 12c code. The new 12c messages are clean: see below.

Suggested fix: widen the hardening line to cover every integrity.py message that puts a key in the text (:118, :148-149, :179, :195, :338, :341). Quote each key with `json.dumps`.

### L2: low. Not unsafe. Decision 0009 (Guard is the unwaivable job). The interim control reads the output of code the PR controls.
Where: PROGRESS.md:247 (the interim control until 12b lands).

What is wrong:
- The control checks that "`make ci` at the merge head prints exactly the expected lines". `make ci` runs the PR's own `cli.py` and `integrity.py`.
- A #32, or any later PR, that also changes `evals/src/northwind_evals/{integrity,cli}.py` controls the very output the control checks. That is the gap F1 describes.
- For #42, the evidence at PROGRESS.md row 12 says the advisor also checked "on disk with main's integrity code". The 12c wording drops that. My own F1 suggestion had the same gap, so this is a tightening, not a misstatement.

Suggested fix: add to the record that the advisor confirms the 3 lines (EV-084#1, EV-093#1, EV-102#1, `"reason": "context"`) by running main's `replaced_refinements` (post-12c) against the merge head. As an alternative, confirm that the PR's diff leaves `evals/src/northwind_evals/integrity.py` and `cli.py` unchanged.

### L3: low. Not unsafe. Test strength only.
Where: evals/tests/test_context_refinements_integrity.py:310-317.

What is wrong:
- T19 forges only `\n`.
- The fix escapes every risky character. `json.dumps` defaults to `ensure_ascii=True`, so `\r`, U+0085 and U+2028 are escaped too. The code is complete.
- The test pins less than the code does. A hand-rolled mutant would pass both asserts while leaving `\r` raw: `'"' + item_id.replace("\n", "\\n") + '"'`.

Suggested fix (optional): parametrize `forged` over `\n`, `\r` and ` `. Alternatively, assert the whole message: `f"{CONTEXTS} (disk) holds context {json.dumps(forged)} twice"`.

## 2. Checked, no findings

F3 fix and the rest of the new 12c code:
- integrity.py:241 is correct. `json.dumps(item_id)` quotes the id and escapes every control and non-ASCII character, so no line break survives.
- `source` in `_context_chunks` is built only from the `CONTEXTS` constant, the merge-base SHA and fixed suffixes. It carries no branch text.
- The other new 12c messages carry no branch text. They are :238 ("not a saved context", source only) and :333 (deleted file, constant).
- `replaced_refinements` output is printed through `json.dumps` (cli.py:768).
- `item_id` and `n` in the replaced entries come from base keys, and `int(n)` fails closed (U1, logged).
- Guard imports `integrity_problems` from main's `evals/src` (integrity-main.py:147). Once 12c merges, Guard's FAILED line gets the quoted message too.

T19 reaches the fixed line and pins it:
- The forged lines are written to disk only, so the base and HEAD copies parse cleanly.
- The path to the fixed line: `changed_refinements` passes the base refinements check, `answerer_replaced` returns False with the same prompt, and `context_changed_items` raises while parsing the disk copy.
- Without the fix, both asserts fail: the message holds a raw `\n`, and it lacks `json.dumps(forged)`.
- The forged string is test data built to look like log output. It is not an attempt to steer the review.

PROGRESS.md and COVERAGE.md records:
- F1 (PROGRESS.md:247): accurate. It is medium, owned by 12b with 12a's F1, and says the context exemption needs no `baseline-change` label. The interim control's lines and file check match my suggestion (see L2 for the tightening).
- 12a's F2 marked "closed in 12c" (PROGRESS.md:244 area, the 12a Medium entry): accurate. A deleted file is refused under either exemption (:331-334). T6a and T6b pin it.
- F2 (PROGRESS.md:248): accurate. It is low, owned by hardening, and keeps the CI caveat. It leaves out my note that the reverse case (disk changed, HEAD not) is reported as if committed. That omission is minor and weakens nothing.
- F3 (row 12c and :248): accurate that the new message is fixed and pinned by T19. The F3-rest clause is incomplete (L1).
- F4 residual (PROGRESS.md:246): accurate, and reworded as I asked. It covers the forged added cassette under 0009's open residual, a promote only if the scores change, and the `source_id` and `source_type` stale-check gap. It says the exemption covers that item alone, names the human control (`cassettes/**`, `evals/contexts/**` in ship's list), and states what 12c adds over before. Nothing is overstated or weakened.
- U1 (PROGRESS.md:248): accurate. It is still unconfirmed, owned by hardening, with the tightening I suggested.
- RECORD-ALL low (PROGRESS.md:245): accurate. With the same prompt and the same `chunks`, neither exemption opens (:225, :264).
- COVERAGE.md CC-04: one entry added, and it has no security content.
- No record suggests loosening a check, cap, scanner, label or baseline.

Hard rules:
- 1, 2, 5, 7, 8, 9, 10, 11: not applicable to this change.
- 3: checked, no finding (synthetic ids, `test@example.invalid`).
- 4: checked, no finding (no secret or .env read or printed).
- 6: checked. F3 is fixed for 12c's message. The remaining items are L1, which is pre-existing code.

Decision 0001:
- Placeholder key and proxy-only real key; Makefile key export; CI secrets and replay; key file in a worktree: not applicable to this change.
- Budget caps: checked, no finding. Spend is $0 and nothing is recorded.
- Cassette scanner coverage: checked, no finding. Cassette checks and the scanner are unchanged.

I suggested no fix that weakens any control.
