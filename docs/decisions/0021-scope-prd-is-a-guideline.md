# 0021: The PRD is a guideline; scope is cut to a working, demonstrable shop

Status: accepted by the user, 2026-11-23 ("going forward we will follow it"). Drafted by the advisor at the
user's request; the input is `docs/reviews/2026-11-23-scope-decision-input.md`. Recorded by row 32b.

## Context

The API credit ends around Dec 22 (decision 0013). The input (`docs/reviews/2026-11-23-scope-decision-input.md`)
counts about 71 Low open items.

## Decision

The PRD is a guideline, not a contract. The goal is a working, demonstrable Northwind Shop that:
- replays after the API credit ends (Dec 22);
- is built the agentic way with Claude Code;
- is evidenced in COVERAGE.md.

A PRD criterion is pursued only when it does at least one of these:
- adds visible behaviour;
- protects money, secrets or the evidence;
- is needed for RECORD-ALL.

Extra hardening is not pursued for its own sake.

## Keep (unchanged)

1. **Hard rules and existing guardrails:**
   - spend caps (`LLM_BUDGET_USD`, `RUN_BUDGET`);
   - replay-only CI;
   - the secret/PAN scan of cassettes (the repo is public);
   - the eval gate and promotion control;
   - Guard integrity, never waived;
   - control-surface changes made by the user;
   - `ship`'s checks;
   - one `make ci` per row.
2. **Window A rows:**
   - #16 (cart tools);
   - #11 (RAG-04 query understanding);
   - #17 (polish): the offer-drop rule, the D8 prompt line, and one live first-token measurement recorded as
     NFR-13 partial. Nothing more.
   - 32b;
   - #18 RECORD-ALL.
3. **RECORD-ALL prerequisites that protect secrets or money:**
   - FOUND-2 review 6 (scanner widening) and review 10 (fixed-precision costs);
   - the A2A-05 partner-surface decision, which is small and needed to re-record a2a-05;
   - the fix or quarantine of #12's timing-dependent smoke test, because a flaky required check costs every row.
4. **Tier B LLM rows, in priority order, while time allows before about Dec 10:** SA-04, SA-03, RAG-10+07,
   SA-06, BE-07, BE-01, CHK-06+07.
   - **BE-04, BE-06 and BE-03 run only if time is left.** Otherwise they move to Window B on scripted fakes, or
     are dropped.
5. **Window B (after the credit):** the visible portal pages (SA-01) and anything needed to demo.
   - The rest of the Window B list is optional and the user picks from it later: P4 payments beyond the fakes, P5
     A2A beyond A2A-05, P6 hardening, RAG-05/06.

## Cut (won't-do unless the user revives one by name)

- **SEC-1** (git-escape hardening, full) and **9a** (least-privilege DB role).
- **P6 hardening:** NFR-01, and the rest of NFR-03.
- **Open items with severity Low** (about 71), and **Medium items whose owner is "hardening"**: won't-do.
  - Exception: one that is a real bug that breaks a demo, loses money, leaks a secret or corrupts evidence.
  - Medium items owned by a named feature row stay with that row (#17's prompt item, #18's containment flips).
- **Chasing partial PRD criteria that were settled on evidence:**
  - RAG-02 enrichment (rejected in #8c);
  - RAG-03's remainder;
  - judge-caused containment flips (accepted as measured).
- **The `ship.sh` fix for 12b's M1** (show Guard's replaced lines).
  - The interim advisor check stays, but only for a PR that changes `evals/contexts/**`.

## How rows run from now on

1. **No new Low items in Open items.**
   - A low finding that is not a real bug goes in the PR body only.
   - A real bug is fixed in the row or logged with a named owning row.
   - In CLAUDE.md's routine triage: a Low that is a real bug in the row's files is fixed; a real bug elsewhere goes
     in `PROGRESS.md`, "Open items", with a named owning row; any other Low goes in the PR body only.
2. **One security pass per row.** A scoped re-review happens only when a Medium or higher finding was fixed.
3. **Failing-first tests for behaviour changes.** Mutants only for gate, integrity, spend and ship code.
4. **Plans stay short:** criteria, changes, tests, paths, stops.
   - Ask the user only for decisions that are theirs: scope, spend, control surface, accepting a regression.
   - Take sensible defaults otherwise.
5. **Records stay short.** A PROGRESS row's notes cell is a few lines plus links to `docs/reviews/`, not a
   transcript. Detail lives in the review files.
6. **No hardening rows.** Bundle any needed fix into the next feature row that touches the same code.
7. **`test-writer` runs for gate, integrity, spend, ship and hard-rule code.**
   - Hard-rule code means the hard-rule 1, 6, 7, 8 and 9 paths listed under CLAUDE.md's "Routine rows".
   - Elsewhere the session writes the tests itself, failing first (rule 3).
8. **The chain order (added by the user in row 32b):**
   - implement;
   - `test-writer` where rule 7 says;
   - one `security-reviewer` pass, then fixes;
   - a scoped re-review only after a Medium or higher fix;
   - commit;
   - PR body;
   - **`advisor-reviewer`, with its fixes**;
   - **the single `make ci`**, once, at the head the advisor read (a re-read if its fixes moved the head);
   - the gate report.

   Any change after `make ci` is a new commit and another `make ci`.
9. **Stop 10** is a high or critical finding still open after its fix, and after the re-review when one runs.

## Consequences

- PROGRESS:
  - "Ordering" points here;
  - SEC-1 (S1) and 9a are `cut (0021)`;
  - BE-04, BE-06 and BE-03 are "if time (0021)";
  - one line at the top of "Open items" marks Low items and hardening-owned Medium items as won't-do.

  The items themselves are not moved or rewritten in row 32b: window A's #16 edits the same section. The archive move
  belongs to the conflict-free-records row after #16 ships.
- `CLAUDE.md` is control surface, so row 32b does not edit it.
  - The user edited it for this decision in `5b01c9f` and `9ba412e`.
  - Rules 1, 7, 8 and 9 above record those edits, so CLAUDE.md's "(decision 0021)" covers them.
  - `tests/hooks/test_stop_conditions.py` pins the new wording as tightly as the old: the chain order with the advisor
    before `make ci`, stop 10, and the Low rule with its owner clause.
