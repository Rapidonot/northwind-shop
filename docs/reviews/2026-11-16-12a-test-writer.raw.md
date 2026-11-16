# Row 12a: test-writer report (verbatim, read and report only per D1)

Row 12a test review (read only, no files changed). `uv run pytest evals/tests/test_refinements_integrity.py -q` gives 13 passed.

Criterion coverage (all in evals/tests/test_refinements_integrity.py; the 5 new tests carry req RAG-09):

1. Exemption opens only when the prompt part differs in both the HEAD and the disk baseline.
   - Positive case: `test_a_new_answerer_prompt_lets_its_refinements_change_and_reports_each`. The baseline is committed with the new prompt, so HEAD and disk agree.
   - Negative case on disk: `test_an_uncommitted_baseline_change_does_not_open_the_exemption`. Disk has the new prompt and HEAD has the old one, so the rule stays add-only.
   - The reverse split is not covered (see gap G1).

2. A change only in the check part, a missing or unreadable answerer_id, or an uncommitted baseline change keeps refinements add-only.
   - Check part only: `test_the_same_answerer_prompt_keeps_refinements_add_only`. It also asserts that `replaced_refinements` returns `[]`.
   - Unreadable id: `test_an_unreadable_answerer_id_keeps_refinements_add_only`. This is an id with no prompt part.
   - Uncommitted baseline change: `test_an_uncommitted_baseline_change_does_not_open_the_exemption`.
   - A missing `answerer_id` key, malformed JSON, and an absent base or baseline file are not tested directly (G2, G3).

3. Each replaced refinement is reported, by the function and by the CLI.
   - Function: the positive test checks `replaced_refinements` returns EV-1 (changed) and EV-2 (dropped) in order. It also checks the old digest is 16 characters long, the new digest differs from the old one, and a dropped entry has new "-".
   - CLI: `test_cmd_integrity_prints_each_replaced_refinement` checks two "refinement replaced:" lines and the exit code 0. It mocks `added_frozen_vectors` and `added_known_failures`.
   - Digest values are not checked (G4).

4. On main the rule is inert (baseline `prompt-eb39dcf5`, unchanged).
   - Only indirectly covered: the same-prompt and unchanged-file tests show no exemption when the prompt part is equal.
   - There is no test against the real main baseline (G5).

Gaps:

- G1 (medium). Only one half of "both HEAD and disk" is tested. The case where HEAD has the new prompt but disk has reverted to the old one is missing. It matters because a mutant that drops the disk check, or only checks HEAD, would survive. One-line test: commit `baseline_with("bbbbbbbb")` plus changed refinements, then `write(BASELINE, [baseline_with("aaaaaaaa")])` and assert `integrity_problems(...)` contains `"refinement EV-1#1 changed (HEAD)"` and `answerer_replaced("main", BASELINE) is False`.
- G2 (medium). There is no test that a missing `answerer_id` key, or one that is not a string, fails closed. Only the "no prompt part" string is covered. One-line test: write a baseline of `{"overrides": []}` (no key), change a refinement, and assert it is refused.
- G3 (low). The tests do not cover malformed baseline JSON (the `except ValueError` branch), a baseline missing at `commit` (new file on the branch, which makes `before.returncode != 0`), or a baseline file deleted from disk (`is_file()` is False). One-line test: write `"{not json"` to the baseline, commit it, and assert `answerer_replaced("main", BASELINE) is False`.
- G4 (low). The digest values are not pinned. The test checks only length 16 and that old differs from new. A wrong hash (for example a different slice of the sha256) would pass. One-line test: assert `changed["old"] == hashlib.sha256(entry("EV-1", 1).encode()).hexdigest()[:16]`.
- G5 (low). Criterion 4 has no direct test. One-line test: read `evals/baselines/dense-only.json` at HEAD and assert its `answerer_id` contains `prompt-eb39dcf5`, and that `answerer_replaced("origin/main", BASELINE)` is False. Alternatively, treat it as a manual check, since the plan says the baseline is unchanged in this row.
- G6 (low). The exemption's effect on the other integrity checks is untested. A prompt change should not relax cassette, frozen-vector or override checks, and `integrity_problems` still appends them. One-line test: in the positive scenario also modify a base cassette and assert the problem list is non-empty.
- G7 (low). The CLI test does not check that "refinement replaced:" lines are absent when the exemption is closed, or that the digests in the printed JSON match the function's output. One-line test: in the check-part-only scenario, call `cmd_integrity` and assert `"refinement replaced:" not in out`.
- G8 (low). `replaced_refinements` is untested when only a new entry is added under an open exemption. It should return `[]`, because `before` has no key for the new entry. This is partly covered, since EV-3 is added in the positive test and is not listed.

Overall: every criterion has at least one test, and the two mutants already run were killed. The only medium gaps are G1 and G2, both fail-closed paths of the exemption (the HEAD-versus-disk split and a missing `answerer_id`).

---

## Session note (not part of the report)

G1, G2 and a non-string `answerer_id` were exercised once with an uncommitted scratch test (the
failing-first log, section "scratch"): 3 passed. The code fails closed there; the committed tests do not
pin it. Under D1 no test is added in this row (byte-identity with `374dae8`). G1–G8 are open items.
