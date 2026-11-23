# Scope decision input (the user, 2026-11-23): the PRD is a guideline

Drafted by the advisor at the user's request; **decided by the user, 2026-11-23** ("going forward we will
follow it"). Row 32b records it as decision `0021-scope-prd-is-a-guideline.md` and applies the PROGRESS
edits in "Records" below. From this decision on, every plan and review follows it.

## Decision

The PRD is a guideline, not a contract. The goal is a working, demonstrable Northwind Shop that replays
after the API credit ends (Dec 22), built the agentic way with Claude Code and evidenced in COVERAGE.md.
A PRD criterion is pursued only when it adds visible behaviour, protects money, secrets or the evidence,
or is needed for RECORD-ALL. Extra hardening is not pursued for its own sake.

## Keep (unchanged)

1. **Hard rules and existing guardrails:** spend caps (`LLM_BUDGET_USD`, `RUN_BUDGET`), replay-only CI, the
   secret/PAN scan of cassettes (the repo is public), the eval gate and promotion control, Guard integrity
   (never waived), control-surface changes by the user, `ship`'s checks, one `make ci` per row.
2. **Window A rows:** #16 (cart tools), #11 (RAG-04 query understanding), #17 (polish, the offer-drop rule,
   the D8 prompt line, one live first-token measurement recorded as NFR-13 partial, nothing more), 32b,
   #18 RECORD-ALL.
3. **RECORD-ALL prerequisites that protect secrets or money:** FOUND-2 review 6 (scanner widening) and
   review 10 (fixed-precision costs). The A2A-05 partner-surface decision (small, needed to re-record
   a2a-05). The fix or quarantine of #12's timing-dependent smoke test (a flaky required check costs every row).
4. **Tier B LLM rows, in priority order, while time allows before about Dec 10:** SA-04, SA-03, RAG-10+07,
   SA-06, BE-07, BE-01, CHK-06+07. **BE-04, BE-06, BE-03 only if time is left**; otherwise they move to
   Window B on scripted fakes, or are dropped.
5. **Window B (after the credit):** the visible portal pages (SA-01) and anything needed to demo. The rest of
   the Window B list (P4 payments beyond the fakes, P5 A2A beyond A2A-05, P6 hardening, RAG-05/06) is
   optional, picked by the user later.

## Cut (won't-do unless the user revives one by name)

- **SEC-1** (git-escape hardening, full) and **9a** (least-privilege DB role).
- **P6 hardening:** NFR-01, NFR-03's rest.
- **Open items with severity Low** (about 71), and **Medium items whose owner is "hardening"**: won't-do,
  unless one is a real bug that breaks a demo, loses money, leaks a secret or corrupts evidence. Medium
  items owned by a named feature row stay with that row (#17's prompt item, #18's containment flips).
- **Chasing partial PRD criteria that were settled on evidence:** RAG-02 enrichment (rejected in #8c),
  RAG-03's remainder, judge-caused containment flips (accepted as measured).
- **The `ship.sh` fix for 12b's M1** (show Guard's replaced lines): cut. The interim advisor check stays,
  only for a PR that changes `evals/contexts/**`.

## How rows run from now on

1. **No new Low items in Open items.** A low finding that is not a real bug goes in the PR body only. A real
   bug is fixed in the row or logged with a named owning row.
2. **One security pass per row.** A scoped re-review only when a Medium or higher finding was fixed.
3. **Failing-first tests for behaviour changes; mutants only for gate, integrity, spend and ship code.**
4. **Plans stay short:** criteria, changes, tests, paths, stops. Ask the user only for decisions that are
   theirs (scope, spend, control surface, accepting a regression); take sensible defaults otherwise.
5. **Records stay short:** a PROGRESS row's notes cell is a few lines plus links to `docs/reviews/`, not a
   transcript. Detail lives in the review files.
6. **No hardening rows.** Bundle any needed fix into the next feature row that touches the same code.

## Records (row 32b applies these)

- `docs/decisions/0021-scope-prd-is-a-guideline.md`: this decision (Decision, Keep, Cut, How rows run).
- PROGRESS "Ordering": one line pointing to decision 0021.
- PROGRESS Tier A: SEC-1 and 9a marked `cut (0021)`. Tier B: BE-04, BE-06, BE-03 marked "if time (0021)".
- PROGRESS "Open items": one line at the top, "Low items, and Medium items owned by hardening, are won't-do
  by decision 0021 unless revived by name". **Do not move or rewrite the items in this row** (window A's #16
  edits the same section; the archive move belongs to the conflict-free-records row after #16 ships).
- `CLAUDE.md` is control surface: if a rule there conflicts with this decision (for example a rule that
  every finding is logged), list the line in the gate report for the user to edit; do not edit it.
