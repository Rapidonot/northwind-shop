# 12c security review (raw, verbatim)

Reviewer: `security-reviewer`, 2026-11-18. Input: `/tmp/claude-502/northwind-12c-security.diff` (working tree
against `5539421`, untracked files as new), the plan, and the files it names.

---

Security review, row 12c: per-item context exemption for replaced refinements

I found nothing critical or high, and nothing that breaks a hard rule or touches a hard-rule path. The exemption cannot be opened for one item by another item's context change. Every missing, renamed or unreadable input either exempts nothing or raises, so nothing fails open. integrity.py is still stdlib-only.

There is one medium finding: the job that nothing can waive (Guard) applies the new exemption but never prints what it let through. This is 12a's F1, but it matters more here, because this exemption needs no `baseline-change` label. There are three lows. One of them is about your planned residual: the stated preconditions are too strong, though the attacker gains no real new capability.

Diff reviewed: /tmp/claude-502/northwind-12c-security.diff (working tree against 5539421). I also read in full or in the relevant parts:
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/integrity.py
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py (cmd_integrity 754-772, cmd_gate 541-609, _clients)
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/contexts.py and answerers.py (load_contexts, ContextChunk, load_refinements, SavedFirstIndex)
- /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/gate.py (Baseline, passed, score_drift)
- /Users/rapido/Projects/northwind-shop/.github/workflows/guard.yml and guard/integrity-main.py
- the plan, with "Changes from the user at approval"; the 12a raw review; PROGRESS.md line 243; decision 0001; decision 0009 lines 131-133

## 1. Findings (most severe first)

### F1: medium. Not unsafe and does not change the design. Decision 0009 (Guard is the unwaivable integrity job).
Where: /Users/rapido/Projects/northwind-shop/.github/workflows/guard/integrity-main.py:147-160, together with integrity.py:322 and :335.

What is wrong:
- Once this merges, Guard's main copy of `changed_refinements` applies the context exemption through `integrity_problems`.
- Guard never calls `replaced_refinements`, so no "refinement replaced" line appears in any log the PR cannot control. Its own CI and local `make ci` run the PR's `cli.py`.
- 12a's F1 is open and owned by 12b, and the plan leaves it out of scope. For the answerer exemption, the `baseline-label` job at least forces your label. The context exemption opens on an edit to `evals/contexts/dense-only.jsonl` alone, and that needs no label. Only `ship`'s control-surface listing (`evals/contexts/**`) would show it.

How it fails: after merge, a PR changes item X's `chunks` and rewrites or drops X's refinements. It can also delete the print loop in its own `cli.py`. That needs `gate-change`, but `gate-change` is routinely granted for `evals/src` rows. Guard then prints only `eval-integrity passed`, and no trusted log lists what was replaced.

Suggested fix: 12b keeps ownership and adds the `replaced_refinements` print to integrity-main.py. It cannot ship in this PR: main's `evals/src` has no `reason` field or context exemption yet. Until 12b lands, extend 12a's interim control to #32 and any later PR that changes `dense-only.jsonl`. Before ship, the advisor checks that `make ci` at the merge head prints exactly the expected lines. For #32, that is 3 lines (EV-084#1, EV-093#1, EV-102#1) with `"reason": "context"`, and the refinements file exists. Record this in PROGRESS.md Open items next to the 12a interim control.

### F2: low. Not unsafe. Same class as 12a's F3, now also covering reason "context".
Where: integrity.py:291 (`replaced_refinements` reads only the disk copy), compared with :325-343 (`changed_refinements` exempts HEAD and disk separately).

What is wrong: the exemption lets an exempt item's entry change or drop at HEAD and on disk independently. The report only compares disk with the base.

How it fails: you asked whether the report is complete when HEAD and disk differ. It is not.
- Commit a change to EV-1#1 on an item whose context changed, then restore EV-1#1 on disk without committing. `changed_refinements` passes, because EV-1 is exempt at HEAD, and `replaced_refinements` lists nothing for EV-1#1. The commit still carries the change.
- The reverse case (disk changed, HEAD not) is reported, though as if committed.
- In GitHub CI, HEAD and disk are the same, so CI output is accurate. Local `make ci` output used as gate-report evidence can be incomplete.

Suggested fix (tightening): build the replaced list from both HEAD and disk, with a `where` field (or the union of both), as `changed_refinements` does. Add a test with HEAD changed and disk reverted.

### F3: low. Not unsafe. Report accuracy, hard rule 6 (data posing as output).
Where: integrity.py:240 (`holds context {item_id} twice`), printed raw at cli.py:763 and integrity-main.py:153.

What is wrong: `item_id` comes from the branch-controlled HEAD or disk contexts file. It is put into the error message through `str()` and printed without escaping. A newline in it can forge lines such as `  refinement replaced: {...}` or `eval-integrity passed against …` in the log. The `print(..., file=sys.stderr)` in cli.py:763 adds no protection.

How it fails: the exit code is still 1 and the check is red, so this fails closed. Only a person reading the log can be misled. The existing `_frozen_lines` and `_refinement_lines` messages have the same pattern. This row adds a third copy.

Suggested fix: format the id with `json.dumps(item_id)` or `!r` in the message. That matches the cli.py comment "one JSON line each, so text inside an entry cannot pose as output".

### F4: low. Not unsafe. Your planned residual is not worse in effect, but its preconditions are overstated.
Where: plan, "Logs", Residual; integrity.py:245-264.

The plan says a hand-edited context "would also need re-recorded cassettes (runner only) and a promote (the baseline-change label, user only)". Neither is strictly needed:
- Integrity refuses only modified cassettes. A hand-written new cassette for the changed request passes. Decision 0009 line 131 lists this as an open residual.
- `score_drift` (gate.py:217-224) needs a promote only if the scores change. A forged answer and verdict that reproduce the baseline scores exactly need no promote and no `baseline-change`.
- The gate's stale check (contexts.py:73-77, cli.py:558) compares only `(chunk_id, text)` with live retrieval. An edit to a chunk's `source_id` or `source_type` therefore opens the exemption without going stale, and without touching fixtures.

Why it is not worse: anyone who can forge an added cassette already controls the scored answer and verdict for that item. Before 12c they could also edit the context and add new refinement entries. 12c only adds rewriting or dropping that item's existing entries. Without a forged cassette, a hand-edited refinement on an exempt item misses in replay, the item goes stale, and the gate fails (`EVAL_ALLOW_STALE` is empty in guard.yml:96). I could not make the exemption open on an edit the model would not see. `chunks` must load as `ContextChunk(**chunk)` with exactly four string fields, so an extra key raises, and all four fields reach the model through `RetrievedChunk` (answerers.py:217-219).

Suggested fix: reword the residual: "…would also need a re-recorded **or forged added** cassette for that item (0009's open residual), and a promote only if its scores change". `ship`'s control-surface list (cassettes/**, evals/contexts/**) is the human control.

### U1: unconfirmed. Low. Key parsing with '#'.
Where: integrity.py:296 and :335 (`key.rsplit("#", 1)[0]`).

The item is derived from the joined key `f"{item_id}#{n}"`, not from the record. A base entry with a string `n` containing '#' would map to a different item prefix. Add-only accepts any `n` on a newly added entry, so an earlier PR could plant one. For it to cross into another item, though, an eval item id would itself have to contain '#'. Real ids are `EV-nnn`. A planted non-int `n` is also inert in replay, because `load_refinements` keys on the raw `(item_id, n)` and searches use an int `n`. If the answerer exemption holds, `int(n)` at :312 raises `ValueError`. That crash fails closed but prints no FAILED line (12a's F5).

Suggested fix (tightening): keep `(item_id, n)` as a tuple from the record in `_refinement_lines`, and refuse a non-str `item_id` or non-int `n`.

## 2. Checked, no findings

Questions from the brief:
- Cross-item leakage: none. The exempt set is built only from the base's item ids (:260-264). `guarded` uses the base keys (:335). T5 and T18 pin this.
- `str()` normalisation and type confusion: none. Both files use `str(item_id)`. On the branch side only the literal base id string matches. A duplicate after `str()` (1 and "1") raises. `chunks` is compared as `json.dumps(sort_keys=True)`, and `ContextChunk(**chunk)` stops any variant that load_contexts would treat as identical.
- Duplicate JSON object keys: both json.loads callers keep the last value, so integrity and the loader agree.
- HEAD vs disk for contexts: both must differ from the base. They may differ from each other (T17), which is acceptable. A change on one side only exempts nothing (T3, T4).
- Missing or renamed files: contexts missing at the base, at HEAD or on disk, or renamed, gives an empty set. A refinements file deleted at HEAD or on disk is refused under either exemption (12a's F2 closed, T6a and T6b). Under the answerer exemption, refinements at HEAD and on disk are now parsed too, which is a tightening.
- Unreadable input: a malformed or duplicate line raises `RuntimeError`, and the run fails. Invalid UTF-8 on disk, or very deep nesting, raises an uncaught exception, which is still a failure with no message (same as 12a's F5).
- Symlinked contexts or refinements file: no gain. HEAD shows the link text, which normally does not parse and raises. A crafted link text that is valid JSON is only another way to commit content. `ship` refuses tracked symlinks (0009 clause 17).
- Merge-base tricks: Guard's checkout is the pull_request merge ref (guard.yml:39-45), so HEAD already includes main and the merge base is main. A stale-base branch that copies main's contexts gets no exemption there. Locally, 12a's U1 still applies (12b owns it).
- Fails open anywhere: no.
- Additions for exempt items are not reported as replaced. That is by design ("New entries pass, as now").
- Stdlib-only for Guard: yes. The imports are hashlib, json, os, re, subprocess, pathlib and typing. The `integrity_problems` signature is unchanged, so integrity-main.py:147-150 still works.
- cli.py change: comment only (:767).
- Prompt injection in the diff, plan, acceptance file or tests: none. Nothing tries to steer the review.
- PROGRESS.md line 243 already says "F2, closed in 12c", but PROGRESS.md is not in the reviewed diff, so I did not review that edit.

Hard rules:
- 1 (card data): not applicable to this change.
- 2 (live price and stock): not applicable to this change.
- 3 (synthetic data): checked, no finding. The tests use `test@example.invalid` and synthetic EV ids.
- 4 (secrets): checked, no finding. No secret or .env file is read, printed or committed.
- 5 (cited claims): not applicable to this change.
- 6 (retrieved text is data): checked. Chunk and refinement text is never printed or acted on. The only finding is F3, an id echoed in an error message.
- 7 (internal docs, region): not applicable to this change. `_refinement_leaks` and `_chunk_guard` are unchanged.
- 8 (least privilege): not applicable to this change.
- 9 (idempotency, monetary confirmation): not applicable to this change.
- 10 (structured tool errors): not applicable to this change.
- 11 (AI disclosure): not applicable to this change.

Decision 0001:
- Placeholder key in child processes, real key only in the proxy: not applicable to this change.
- Makefile does not export the key: not applicable to this change (Makefile not changed).
- No key in CI secrets, CI in replay: not applicable to this change (no workflow changed).
- Key file never copied into a worktree: not applicable to this change. The acceptance check used a --shared clone with nothing copied.
- Budget caps enforced, not raised: checked, no finding. Spend is $0 and no LLM call is made.
- Cassettes covered by the secret/PAN scanner: checked, no finding. `modified_cassettes`, `linked_cassettes` and the scanner are unchanged.

I suggested no fix that weakens a check, cap, scanner, label or hook.
