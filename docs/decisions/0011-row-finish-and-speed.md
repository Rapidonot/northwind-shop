# 0011: One finish command, routine rows, faster local runs, and the GOV-1 decisions

Date: 2026-11-07 · Row: AUTO-2 · Status: decided by the user at plan approval

Plan and the user's answers: `docs/reviews/2026-11-07-AUTO-2-plan.md`.

## GOV-1 decisions

The nine `needs the user` items in `docs/governance/threat-model.md`, "Owners", are now `decided`:

- **RAG-12 second-pass F9 to F13, F15:** closed as unrecoverable. Only the numbers survive, and 5a and 5b
  re-reviewed the gate.
- **5a finding 15:** closed, covered by 5b's `denyWrite`, the ask rules and `baseline-label`.
- **`allowLocalBinding` (F4):** accepted for the lab; revisit before non-lab use.
- **Shared caches (F6):** accepted for the lab.
- **`make enrich` ask rule:** fixed in AUTO-2, item 5.
- **Internal-only text to the provider:** not acceptable outside a lab without reviewing the provider's data
  terms.
- **PRD §7 boundary gap:** the PRD stays unchanged, and `threat-model.md`, Boundary 7, is the record.
- **Device bridge:** the advisor reads only connected folders, never `~/.northwind`, and writes only when asked.
  This is a stated policy. Nothing measures it, and it does not say what the bridge keeps.
- **`make eval-gate-stale-ok`:** the user approves it.

GOV-1's findings 8 and C, the tests with no failing run, are accepted.

Row 5b is `done` on the user's statement. This session has no GitHub access, so the records say "stated by the
user" for what happened there.
