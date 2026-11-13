# RAG-08 + NFR-03 (partial), row #13b: advisor-reviewer row review and re-read (raw, with triage)

Saved verbatim. The caller's triage follows each report.

---

## 1. Row review at `52f4ea9` (diff `base 225204a…`, `head 52f4ea9…`)

findings

**SHA lines confirmed.** `/tmp/claude-502/northwind-review-RAG-08b.diff` begins with `base 225204a604638b4b2c8898d5826ea5b4cded1cad` and `head 52f4ea9f48d4073ac07e2f52014ff0c2c6fa8ee8`, which match the SHAs you gave me. These lines catch accidents, not tampering. I re-read `CLAUDE.md` from disk. The copy on disk adds `evals/contexts/**` and `evals/adversarial/**` to the control surface (line 245), which an older copy did not have, and I used the disk version.

There are no critical or high findings. The (3a) output-check change is sound (details under item 4).

### Findings (most severe first)

1. **Item 7 / A4, medium: the control-surface list in the PR body is incomplete, and plan §6 misclassifies one file.**
   - `RUNNER_SYNC_PATHS` (`Makefile:179`) includes `services/rag`. That puts three changed test files on the control surface: `services/rag/tests/test_context_checks.py`, `services/rag/tests/test_enriched_index.py` and `services/rag/tests/test_enrichment.py`.
   - `docs/reviews/2026-11-13-RAG-08b-pr-body.md:76-84` lists none of them.
   - Plan §6 (`2026-11-12-RAG-08b-plan.md:307-308`) puts `test_enrichment.py` under "Not on the control surface". That is wrong.
   - **Fix:** add all three to the PR body's control-surface list and to the A4 runner-sync list in the gate report. Correct plan §6.

2. **Item 1, medium: two `evals/src` files are outside plan §6's bounded list, which says "Any further file is a plan revision" (plan:294-295).**
   - `evals/src/northwind_evals/testing.py` is new (diff:65708). Its only trace is security L7; no plan revision or approval from the user is recorded.
   - `evals/src/northwind_evals/integrity.py` (diff:65536) is covered by the user's add-only refinements decision (item 5), but §6 was never updated to name it.
   - Two more files fall outside §6's "changed" lists, although the user approved one and the others are pins: `services/assistant/tests/test_render_nfkc.py` sits under the "Not changed: `services/assistant/**`" line; `services/rag/tests/test_context_checks.py` and `services/rag/tests/test_enriched_index.py` are pins.
   - **Fix:** update §6 and name `testing.py` in the gate report for the user's explicit acknowledgement.

3. **Item 2, medium: some security tests were not seen failing on their own behaviour.**
   - In `test_rag08b_adapter.py`, the failing-first evidence for `test_the_r00970_steer_is_caught_only_where_r00970_was_a_source` (and its other tests) is a module collection `ImportError` (`failing-first.log:5-16`). That error proves no individual assertion.
   - In `test_rag08b_criteria.py`, only one test appears in the log (`failing-first.log:56`). `test_trap_inj_016_asserted_fails_and_hedged_does_not`, `test_the_steer_needs_r00970_among_the_sources`, `test_the_greeting_and_tag_items_are_adversarial_contain` and `test_a_refinement_with_an_internal_source_refuses_the_write` have no failing-first entry.
   - No mutant covers the TRAP-INJ-016 `PlantedClaim` (`harness.py`, diff:65324-65332) or `steered()` (diff:65365-65381).
   - **Fix:** run two mutants (delete the TRAP-INJ-016 entry; make `steered` return `False`) and log the counts and the names of the failing tests.

4. **Item 3 / R2, low: the by-eye reads leave out claims and are misformatted.**
   - `docs/reviews/2026-11-13-RAG-08b-answers.md:28` states 26 unsupported claims (other 4, policy 1, site 21), but §2 lists only 24 rows.
   - EV-090's W3b claims are missing from §2, including the `site`-labelled "member pricing" claim the user ruled a containment failure. R2 asks for "every unsupported claim of every kind".
   - `answers.md:18-20` pastes the W3c EV-090 claims, with the wrong column shape, into the §1 table, which holds the W3 judge-v4 results.
   - **Fix:** restore the W3b EV-090 rows, marked as superseded by W3c, and repair §1.

5. **Plan §5.6, low: items with a `blocked` count are not listed.**
   - EV-102's shown answer contains the check's `false_policy` sentence (`output_check.py:47`; `answers.md:429`). So the check fired, and the model did not contain the 365-day term on its own.
   - Yet `threat-model.md` (diff:61277) says "the recorded answer treats the 365-day term as a review claim".
   - **Fix:** list the blocked counts per item in `answers.md`, and reword the threat-model row to say the check contained it.

6. **Item 5, low: W3c's expected spend was not stated in the plan before the window.** §11 covers W3b only. The evidence (`evidence.md:267-270`) shows `RUN_BUDGET=1` and the result, but no estimate given in advance. **Fix:** either cite where the estimate was stated beforehand, or record the omission in the gate report.

7. **Plan §6 "Docs", low: promised doc updates were not done.**
   - The `PROGRESS.md` pointers were not updated: `:52` (row 10), `:63` (#17a check 6, which still reads "carried, open") and `:186`/`:187` (R00970).
   - The #13b note in decision 0015 (diff:61241-61254) does not cover the adapter, F8 or `fabricated_ok` as promised.

8. **Item 8, low: the main-aggregation test can pass as a skip.** `evals/tests/test_rag08b_citations.py:121` skips when `origin/main` is missing. **Fix:** show that the `make ci` run at `52f4ea9` ran it rather than skipped it, and say so in the gate report. Do not loosen it.

9. **Item 3, low: two tests now exercise a test double.** `test_harness.py::test_untrusted_text_cannot_close_a_prompt_tag` and `test_harness.py::test_answerer_and_judge_run_on_separate_clients_with_no_tools` drove the deleted `SingleShotAnswerer`. Their answerer half now runs against `MessagesAnswerer`, so it proves nothing about `AssistantAnswerer`. The judge half still stands. Not a weakening of live code, but it should be named in the gate report.

10. **Low: stale text.** The comment at `services/rag/tests/test_enriched_index.py:47` still says "before RAG-02", but the value is now the `DATA_VERSION` 2 index. `evidence.md:9` still says "No call made by this row so far".

### Unconfirmed
- **The HARDEN-1 merge (item 6).** No row artefact records the conflict resolution or the plan §5.4 re-read of `promotion.py` (`IDENTITY_FIELDS`, `CANDIDATE_FIELDS`, `METRIC_NAME`, `parse_candidate`, `decide`) and of `RUNNER_SYNC_PATHS`. I confirmed separately that `evals/contexts` is in `tools/ship.sh:151` and `CLAUDE.md:245`.
- **The `check-` hash.** I did not confirm that the baseline's `check-0cb61e75` (diff:63331) equals the hash of the current `output_check.py`. A mismatch only prints a note. Check the `make ci` gate output for an "answerer check changed" line.
- **Who wrote the marks.** The artefacts cannot show that the user, not the session, wrote the `human` marks on EV-101 and EV-102 (`starter_set.jsonl`, diff:64472-64473).

### Checked
1. **Scope.** It matches #13b plus #13 §9. The HARDEN-1 paths are untouched, and nothing from window A appears. The deviations are findings 1 and 2.
2. **Failing first.** Logged for A1, refusals and fallback, the refinements integrity rule, judge v5, citations and member pricing. Six mutants were killed, plus the v5 and citation mutants. The gaps are finding 3.
3. **No weakening found.**
   - The count pins (2350→2351, 15→16) keep exact equality.
   - `PLAIN_INDEX_VERSION` and `MACOS_HASH` moved to the v2 values recorded before the failure (`evidence.md:63-73`), with the same strictness.
   - The NFKC exemptions are exact to R01221 and its rendering is pinned.
   - (b1) keeps exactly 15 traps and asserts that R01221 has no entry.
   - The upstream detector in `test_rag12_followup` only lets through the exact URL of the in-process mcp-rag; proxy upstream and the ledger are still caught.
   - `JUDGE_VERSION == 5` is pinned.
4. **Hard rules and protected paths.**
   - `known_failures.json`, workflows, `conftest` files and the root `pyproject.toml` are unchanged.
   - Cassettes: 219 added, 404 deleted, 0 modified.
   - The only change on a hard-rule path is (3a): `member{_S}prices?` → `member{_S}pric(?:e|es|ing)`. It matches strictly more than before, its width (≤16) is under `L = 64`, and EV-090 and the old cases are pinned in `test_member_pricing.py`. Not security-re-reviewed; no problem found.
5. **Spend and window.** Stated before W3 (plan §5.5) and W3b (§11 R3). W3c is finding 6. The ledger reads $2.950179 → $4.801024.
6. **Requested changes.** A1–A5, R1–R4, (A), citations by product and policy claims, (3a), the manifest line, `.ndjson`, `DATA_VERSION` 2 and the R01221/EV-102 targets are all present as stated. `metrics.py` is unchanged. The gaps are findings 4 and 7.
7. **Control surface.** The PR body is incomplete (finding 1).

### Caller's triage (row review)

| Finding | Action, commit `559e7ae` |
|---|---|
| 1 | Fixed: PR body and plan §6 list the three `services/rag/tests` pins; `test_enrichment.py`'s classification corrected |
| 2 | Fixed: plan §6 names `testing.py` (for the user's acknowledgement, in the gate report), `integrity.py`, `test_render_nfkc.py`, `test_member_pricing.py` |
| 3 | Fixed: both mutants killed, failing tests named (`failing-first.log` §10) |
| 4 | Fixed: reads §1 and §2 repaired. Its post-W3c totals sentence was wrong; see the re-read, finding 1 |
| 5 | Fixed: reads §5 blocked counts; threat-model row reworded (the check contained EV-102) |
| 6 | Recorded: the estimate (about $0.005) was stated in the session before the window, not in the plan (evidence, PR body) |
| 7 | Fixed: PROGRESS pointers (row 10, #17a check 6, R00970) and decision 0015's #13b note |
| 8 | Shown: 10 passed, 0 skipped (`-rs`), recorded in the evidence |
| 9 | Named in the PR body and the gate report |
| 10 | Fixed: both stale texts |
| Unconfirmed | Merge record and seam re-read written into the evidence; check hash equal and no "answerer check changed" line; marks: the user's own edits, committed by the session at `149409d` and `02e68ec` |

---

## 2. Scoped re-read at `559e7ae` (diff `base 225204a…`, `head 559e7ae…`)

**findings**: two low findings, both in text the fix commit added. Several points are unconfirmed. Nothing is high or critical.

The SHA lines are fine. `/tmp/claude-502/northwind-review-RAG-08b.diff` begins with `base 225204a604638b4b2c8898d5826ea5b4cded1cad` and `head 559e7aebdc4a82116fbf7ba10432f03a86d22951`, which match what you gave me. As CLAUDE.md says, a match catches accidents, not tampering.

### What I could not check (unconfirmed)
- **My earlier findings 1–10 and the three unconfirmed points.** I have no copy of that report. It is not in `docs/reviews/` and not in `/tmp/claude-502/`. So I could only check closure against the session's own account in `docs/reviews/2026-11-12-RAG-08b-evidence.md:300-337`, which names findings 3 and 5 plus several list fixes. To close this, pass me the verbatim earlier report as a file.
- **That the fix commit alone is 9 files, +113/−15, with no code change except one comment.** `make review-diff` gives the cumulative diff against `origin/main`, and I have no Bash, so I cannot isolate `52f4ea9..559e7ae`. At the head, `services/rag/tests/test_enriched_index.py` changes only a comment and the `PLAIN_INDEX_VERSION` pin, and the pin keeps exact equality.
- **Claims that rest only on session statements.** No log for: the main-aggregation test result, "10 passed, 0 skipped"; who wrote the `human` marks on EV-101 and EV-102; that `check-0cb61e75` equals `assistant_id()` at the head.
- **The `make ci` result at 559e7ae.** Your message reports it; I saw no log.

### Findings
1. **Item 6, low.** `docs/reviews/2026-11-13-RAG-08b-answers.md:27` has the post-W3c totals wrong. It says "Since W3c, the totals are other 5, policy 1, site 20". Remove EV-090's two W3b rows and add its three W3c `other` rows, and `other` is 6, not 5. Policy plus site is then 21 claims on 19 items. This sentence was added by the repair.
2. **Item 6, low.** `docs/reviews/2026-11-13-RAG-08b-pr-body.md:56-57` and `:97` give "22 policy and site claims on 20 items" under "Results (promoted baseline, 102 items)". That count is W3b's. The promoted baseline has W3c's EV-090 verdict, so it is 21 on 19. `PROGRESS.md:208` labels the same count correctly as W3b. Either label the PR body figure as W3b or update it.

### Confirmed from evidence I read
- **Finding 3:** `failing-first.log` §10 shows both mutants killed, two tests each; all four named tests exist.
- **Finding 5:** answers §5 lists the blocked counts per item; the threat-model row cites §5 and an existing test; decision 0015's #13b note lists the same counts.
- **Control-surface lists:** plan §6 (`:310-323`) and PR body lines 77-88 now cover every control-surface file in the diff, including `testing.py` (flagged for the user's acknowledgement), `integrity.py` and the three `services/rag/tests` pins. The PR body also lists the `services/assistant` files under "Control surface changed"; they are hard-rule 6 paths, not control surface. Listing more than needed is harmless.
- **Reads file §1 and §2:** EV-090's W3b rows kept and marked superseded, with the W3c rows after them. The W3b totals (other 4, policy 1, site 21; 22 on 20) match the table.
- **Threat model:** the F8 row cites an existing test; the citation figures (0.971 and 0.941) match the PR body.
- **PROGRESS pointers:** the R00970, HARDEN-1 `deleted: 1` and row 9, 13b and 17a entries are updated.
- No text in the diff or the docs tries to steer the review.

### Checked
1. Scope matches the row; plan §6 now lists every changed file.
2. Failing first: §10 mutants confirmed.
3. No test weakened: the three `services/rag/tests` pins keep exact equality; the R01221 NFKC exemption is narrow, pinned and approved.
4. Hard rules and protected paths: nothing new; `output_check.py` only gets stricter.
5. Spend and window: not in scope.
6. Requested changes: present, but see findings 1 and 2.
7. Control surface: every changed file is named in plan §6 and the PR body.

### Caller's triage (re-read)

| Finding | Action |
|---|---|
| 1 | Fixed: reads §2 says "other 6, policy 1, site 20: policy plus site 21 claims on 19 items" at the promoted baseline, recounted from the run record |
| 2 | Fixed: the PR body gives 21 on 19 at the promoted baseline and 22 on 20 at W3b; PROGRESS's #12 residual gives both, each labelled |
| Unconfirmed (no copy of the row review) | This file now holds the row review verbatim. Raw logs for the `-rs` run, the check-hash comparison and `make ci` at `559e7ae` are in the session's `$TMPDIR` (not committed); the figures are in the evidence |
