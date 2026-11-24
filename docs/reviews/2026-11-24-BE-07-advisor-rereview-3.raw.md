# BE-07 advisor-reviewer narrow re-read of 39aec00..16c513a (verbatim)

`advisor-reviewer` on the scoped diff (`base 39aec00`, `head 16c513a`) and the refreshed `make review-diff`
(`base 6e46b8a…`, `head 16c513a…`), 2026-11-24.

---

pass

Both lows are fixed, nothing else in the row changed, and nothing on the control surface moved. `make ci` can run at `16c513a09fc483d452b9563b2ad21074ae93a58b`.

SHA lines:
- `/tmp/claude-502/northwind-review-BE-07-scoped-3.diff` reads `base 39aec00f3e520d18b33c61610999b5cfa9fb259f` and `head 16c513a09fc483d452b9563b2ad21074ae93a58b`.
- The refreshed `make review-diff` file `/tmp/claude-502/northwind-review-BE-07.diff` reads `base 6e46b8a3204fc8e26cd60ba006b024664c193078` and `head 16c513a09fc483d452b9563b2ad21074ae93a58b`.

Both match the SHAs you gave. The file list in the `make review-diff` file is the same as at `39aec00`, plus the new `docs/reviews/2026-11-24-BE-07-advisor-rereview-2.raw.md`. No code, test, cassette or control-surface file was added or changed.

## The five changes
1. **L1 is closed.** `docs/reviews/2026-11-24-27-plan-draft.md` lines 3-4 now say it is committed only to keep the branch's tree clean, that it is not part of BE-07, and that it is row #27's `/req-start 27` input. All three are true. I did not review its design.
2. **L2 is closed.** Line 3 of `docs/reviews/2026-11-24-BE-07-pr-body.md` now gives `7296c04` as where the branch started and records the merge of `6e46b8a` (#17) as `9f44b28`. Both are accurate.
3. **The Reviews line is accurate.** The PR body's new line says my re-read of `40632d9..39aec00` "found two lows in the records' wording, both fixed". That matches my report.
4. **The saved copy is verbatim.** Below its 3-line header, `docs/reviews/2026-11-24-BE-07-advisor-rereview-2.raw.md` is my report word for word. The header gives the right range, SHAs and date.
5. **The failing-first log's lead-in does not overstate what I said.** It reads "advisor re-read 2 noted that the line above is a claim, not output". I said the summary line was written by the session, not each run's output, and that it was a claim, not a transcript. The lead-in reports that and adds no judgement I did not make. It also leaves out that I called the evidence enough, which is fine.
   - There are 20 `1 passed in …s` lines, which matches "20 of 20".
   - They come from your captured output file, which I was not given. I have read them as your transcription, not compared them with the file.
   - The earlier line says "each about 3 s", while pytest reports 1.74 to 2.07 s. That is consistent if the earlier figure is wall time including `uv run` startup. It is not a finding.

## Checked
1. Scope: docs-only, within the row and its approved bundle. The #27 draft is now presented correctly.
2. Failing first: does not apply to this commit. No test or code changed; earlier evidence unchanged.
3. No test weakened: no test changed.
4. Hard rules and protected paths: no path changed beyond `docs/reviews/`.
5. Spend and window: does not apply. Nothing was recorded.
6. Requested changes: both lows are fixed as suggested. The added Reviews line, saved report and log lines are accurate.
7. Control surface: nothing in this scope.

## Control surface
- This commit (`39aec00..16c513a`): none.
- The whole row (`6e46b8a..16c513a`), unchanged since the last read:
  - `cassettes/backend-care/*.json`: 18 files, added by `make record SUITE=backend-care` in the user's window.
  - `services/backend/tests/backend_audit/test_be08_views.py`: not on CLAUDE.md's list, but the row treats it as a pinned control (stop 5, approved by the user).
