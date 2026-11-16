# Row 12a: security-reviewer report (verbatim)

Diff reviewed: `git diff origin/main` (working tree against `c54f991`), written to
`$TMPDIR/northwind-12a-security.diff` before the commit. One pass; nothing changed afterwards, so no
re-review.

---

I found no critical or high problems, and nothing that breaks a hard rule. The exemption cannot be opened by faking the prompt part. There are two medium findings. In the one integrity job a PR cannot change, the exemption applies but its report never prints. Under the exemption, deleting the refinements file passes with nothing reported. The two lows and the unconfirmed point stay inside the residual the user accepted.

Security review: row 12a, refinement-integrity exemption (diff `/tmp/claude-502/northwind-12a-security.diff`, working tree against origin/main `c54f991`)

Files read: `CLAUDE.md` (Hard rules), `docs/decisions/0001-llm-first-order-and-record-replay.md`, `docs/decisions/0009-promotion-and-gate-control.md`, `docs/reviews/2026-11-16-12a-plan.md`, `evals/src/northwind_evals/integrity.py` (in full), `evals/src/northwind_evals/cli.py` (`cmd_integrity`, `_clients`, eval-gate identity path), `evals/src/northwind_evals/gate.py` (`identity_mismatches`), `evals/src/northwind_evals/answerers.py` (`assistant_id`), `.github/workflows/guard.yml`, `.github/workflows/guard/integrity-main.py`, `tools/ship.sh` (changed_paths, classify, check_labels).

## 1. Findings (most severe first)

### F1: medium. Not unsafe (no hard rule broken), but it weakens a control on a control-surface path (`evals/contexts/**`) beyond the plan's stated design. Decision 0009 clause 11.
`.github/workflows/guard/integrity-main.py:147-160`, together with `evals/src/northwind_evals/integrity.py:258-259` and `:292`.

**What is wrong.** `Guard / eval-integrity-main` is the integrity job that runs main's code and that nothing waives. It has its own copy of the `cmd_integrity` logic: it imports `integrity_problems`, `added_known_failures` and `merge_base`, but not `replaced_refinements`.

Once this row merges, main's `integrity_problems` passes `baseline` to `changed_refinements`. So the Guard job will honour the exemption, but it will never print a "refinement replaced" line. The new `try` block in `cli.py` also makes `replaced_refinements` parse the disk file and raise on bad input. The Guard job never calls it, so under the exemption it does not parse the refinements file at all. With `answerer_replaced` True, `changed_refinements` returns `[]` before reading anything.

The per-refinement report the plan relies on ("each is printed as refinement replaced") therefore comes only from:
- `CI / eval-gate`'s "Eval integrity" step, which runs the PR's own `cli.py`;
- local `make ci`.

The PR controls both.

**How it fails.** Take a PR whose committed baseline holds a new prompt part, for example PR #42 (#12) once 12a is on main. It can rewrite, drop, duplicate or garble every base refinement. The Guard log then shows only `eval-integrity passed against refs/remotes/origin/main`, with no list of what was replaced.

If the PR also deletes the print loop in its own `cli.py`, no CI log shows the list at all. `gate_hit` in `ship` does not call for `gate-change` here, because only `Guard / eval-gate-main` uses that label. The remaining checks are reading `ship`'s full diff (`evals/contexts/**` and `evals/src/**` are on its surface list) and the `baseline-change` label.

**Constraint on the fix.** The fix belongs in `integrity-main.py`, which is control surface and outside this row's three files (stop 7 or 8). It also cannot ship in this PR. Guard runs main's `evals/src`, which has no `replaced_refinements` until this row merges, so importing it now would raise `ImportError` on this PR's own Guard run. Either:
- add it in a follow-up row after merge; or
- make the user's scope decision now and record that, until then, the Guard log does not report replaced refinements.

### F2: medium. Not unsafe. This fails open on unreadable input (the fail-closed question in the brief).
`evals/src/northwind_evals/integrity.py:237-238` (`replaced_refinements`) together with `:258-259` (`changed_refinements`).

**What is wrong.** Under the exemption, `changed_refinements` returns `[]` without reading HEAD or disk. `replaced_refinements` returns `[]` when `not disk.is_file()`. A deleted `evals/contexts/refinements.ndjson`, at HEAD and on disk, therefore gives:
- no problem;
- no "refinement replaced" line;
- exit 0.

Without the exemption, the same deletion is refused twice (`deleted (HEAD)` and `deleted (disk)`, lines 272-273). The `replaced_refinements` docstring says it lists "every refinement at `commit` that the file on disk changed or dropped". A missing file drops all of them and lists none.

**How it fails.** A PR with a new prompt part commits `git rm evals/contexts/refinements.ndjson`. `make eval-integrity` prints `eval-integrity passed against origin/main` and nothing else, which looks exactly like "no refinement touched". Whether the eval-gate replay then goes stale or errors on the missing refinements is unconfirmed: I did not trace `load_refinements` on a missing file.

**Tightening, not loosening.** Under the exemption, treat a missing disk file as "every base entry dropped" and report them all. Or refuse a deleted file even under the exemption, because a re-record rewrites the file but never removes it (`cli.py:467-472` keeps entries for items that were not re-run).

### F3: low. Not unsafe.
`evals/src/northwind_evals/integrity.py:239-240`.

**What is wrong.** Under the exemption, only the disk copy of the refinements file is read. The committed HEAD copy is never parsed, compared or reported. The add-only rule, and `answerer_replaced` itself for the baseline, deliberately check both HEAD and disk. Decision 0006 is the reason: a failed switch under `denyWrite` leaves the old file on disk.

**How it fails.** Locally, the disk file and the commit can differ. The local `make ci` report then describes the disk file, while the commit carries other changes, or duplicates that `_refinement_lines` would refuse, and nobody sees them. In CI the checkout makes HEAD and disk the same, so CI output is accurate. The risk is to local evidence such as the gate report.

### F4: low. Not unsafe. Within the residual the user accepted.
`evals/src/northwind_evals/integrity.py:258-259`.

**What is wrong.** The exemption is all-or-nothing. Once the prompt part differs, every refinement for every item may change or disappear. Nothing checks the replaced set against:
- the items re-recorded in this PR;
- the cassettes the PR adds under `cassettes/eval-answers/`.

A legitimate slice re-record (`make eval-live EVAL_SLICE=<n>`, the policy after about 2026-11-24) rewrites only the slice's items (`cli.py:467`).

**How it fails.** A slice re-record that also edits refinements for items outside the slice passes integrity. Only a person comparing the printed list with the slice, or the gate's replay going stale, would notice: a changed refinement changes the model request, so the replay misses. That backstop is inferred, not traced item by item. Under F1, the trusted job does not print the list at all.

### F5: low. Not unsafe. Fails closed, but without the usual message.
`evals/src/northwind_evals/integrity.py:245-247` and `:222`; `cli.py:752`.

**What is wrong.** `cmd_integrity` catches only `RuntimeError`. Two inputs raise other errors:
- `int(n)` on a key whose `n` was not an integer in the base file (for example `1.0`, `"x"`, or an `item_id`/`n` holding `#`) raises `ValueError`;
- a baseline that is not valid UTF-8 on disk raises `UnicodeDecodeError` in `baseline.read_text`.

**How it fails.** Each one crashes with a traceback and a non-zero exit, so the check still fails closed, but the output has no `eval-integrity FAILED` line. The `n` comes from main's file, so a PR cannot plant it. This is informational.

### U1: unconfirmed. Low.
`answerer_replaced`, `evals/src/northwind_evals/integrity.py:217-223`.

**What I suspect.** The exemption compares against `merge_base(base)`, not current main. A branch whose merge base is older than a prompt change on main gets the exemption without changing the prompt against current main. For example: a branch from before #12, which copies main's new baseline and `prompt.py` instead of merging main. The branch's refinements, measured against its old base, could then be rewritten.

**Controls I confirmed.** `ship` diffs from the merge base (`tools/ship.sh:101-105`), so the copied baseline still counts as a baseline change and needs the user's `baseline-change` label (`:183-186`). I did not work out how GitHub's three-way merge would combine those refinement edits with main's.

## 2. Checked, no findings

**Questions from the brief**

- **Faking the prompt part through the regex** (`_PROMPT_PART`, `integrity.py:198-210`): no finding. The prompt part in the baseline can be edited by hand, but the eval-gate's F8 check (`gate.py:160-176`, `cli.py:553-571`) fails any baseline `answerer_id` that is not the computed `assistant:v1:<model>:prompt-<sha8 of prompt.py>:check-…` (`answerers.py:259-265`), up to `:check-`.
  - That prefix ends in the real prompt part followed by `:check-`, so the regex's first match is the real part. You cannot make integrity see a different part while F8 passes.
  - A faked part with an unchanged `prompt.py` fails F8 in both `CI / eval-gate` and `Guard / eval-gate-main`, and needs `baseline-change` as well.
  - A real prompt change, even whitespace only, makes every eval-answers cassette miss, so zero-tolerance containment goes stale. Only a re-record in the user's window, or a forged cassette, which can be added (the stated residual in 0009), gets past that.
  - Note: integrity parses with the first regex match anywhere in the string, while the gate splits on `:check-`. They agree only while the model ID contains no `:prompt-<8 hex>:`. Today it does not.
- **Disk baseline differing from HEAD:** no finding. Both must be readable and both must differ from the base (`integrity.py:219-223`). `test_an_uncommitted_baseline_change_does_not_open_the_exemption` covers it.
- **Baseline that cannot be read or has the wrong shape:** fails closed. A missing file at the base, at HEAD or on disk, JSON that is not an object, or no prompt part all give `False` (tested by `test_an_unreadable_answerer_id_keeps_refinements_add_only`). A symlinked baseline at HEAD is shown as its link text, does not parse and gives `False`. `ship` refuses tracked symlinks (0009 clause 17).
- **Refinements file with the wrong shape on disk, under the exemption, in `cmd_integrity`:** fails closed. `_refinement_lines` raises `RuntimeError` on a malformed or duplicate line. The gaps are in the Guard job (F1) and on deletion (F2).
- **Text in a refinement posing as CLI output:** no finding. Only `item_id`, an integer `n` and two 16-hex digests are printed, through `json.dumps(..., sort_keys=True)`. The default `ensure_ascii=True` escapes newlines, control characters and non-ASCII. Refinement text is never printed, and `item_id` comes from the base commit's file.
- **Inert on main:** confirmed. The diff does not touch `evals/baselines/`, so the prompt part at HEAD and on disk equals the base's `eb39dcf5`, and `answerer_replaced` is False.
- **Prompt injection in the diff, plan, log or PROGRESS/COVERAGE text:** none found. Nothing in them tries to steer the review.

**Hard rules**

- Hard rule 1 (card data): not applicable to this change. No payment path is touched, and the scanner's coverage of refinements is unchanged.
- Hard rule 2 (live price and stock): not applicable to this change. Refinements are eval-only and this change does not alter how they are served.
- Hard rule 3 (synthetic data): checked, no finding. The tests use `test@example.invalid` and synthetic IDs.
- Hard rule 4 (secrets): checked, no finding. No secret, env file or key is read, printed or committed.
- Hard rule 5 (cited product claims): not applicable to this change. The eval evidence for it stays protected by F8 and the stale and containment checks.
- Hard rule 6 (retrieved text is data): checked, no finding. Refinement contents are never printed or interpreted, and the leak check at serve time (`cli.py:254-257`) is unchanged.
- Hard rule 7 (internal documents, regional policy): not applicable to this change. `_refinement_leaks` and `_chunk_guard` are unchanged.
- Hard rule 8 (least privilege): not applicable to this change.
- Hard rule 9 (idempotency and monetary confirmation): not applicable to this change.
- Hard rule 10 (structured tool errors): not applicable to this change.
- Hard rule 11 (AI disclosure): not applicable to this change.

**Decision 0001 key handling and spend**

- Child processes get only a placeholder key; only the proxy holds the real key: not applicable to this change, nothing in the LLM client or proxy is touched.
- The Makefile does not export the key: not applicable to this change, the Makefile is not changed.
- No key in CI secrets, and CI runs in replay: not applicable to this change, no workflow is changed.
- The key file is never copied into a worktree: not applicable to this change.
- Budget caps are enforced and have not been raised: checked, no finding. The change makes no LLM call, and the plan states $0 spend.
- Cassettes are still covered by the secret/PAN scanner: checked, no finding. `modified_cassettes` and `linked_cassettes` are unchanged, the scanner is untouched, and refinements stay in scanner scope (`evals/tests/test_refinements_coverage.py`).

**Decision 0009 controls**

- `baseline-change` label, required by Guard and checked by `ship` against who added it: unchanged. It is still required for any baseline edit that opens the exemption.
- `gate-change` on `evals/src`: applies to this PR, as the plan states.
- `Guard / eval-integrity-main`, which nothing waives: still runs main's code, but under the exemption it does not report (F1).

No fix was suggested that weakens any check, cap, scanner, label or hook.
