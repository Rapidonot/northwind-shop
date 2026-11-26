# COVERAGE.md evidence draft (2026-11-26, paperwork only)

Status: a **draft** for the user, read from the repository on 2026-11-26. **`COVERAGE.md` is not changed here.** For
each CC row still `todo` or `partial` it gives:
- the evidence the project already has, with file paths or commits;
- the PRD §6 pass test, quoted;
- a recommendation: **done**, **partial** or **todo**.

A row moves to `done` only when the evidence meets the PRD's pass test as written, not the feature description.
Moving one is the user's decision: this draft only proposes.

## Candidates for `done`

| Row | PRD pass test | Evidence that meets it | Caveat |
|---|---|---|---|
| **CC-03** Auto memory | "A second session reuses a command learned in the first" | COVERAGE CC-03, 2026-11-09 (RAG-11, #9): the memory saved in the #8c session (`save-reviewer-raw-output`) was recalled in a new session and followed for all five `security-reviewer` passes (`docs/reviews/2026-11-09-RAG-11-security-review.md`). The same rule was followed again in #23: `docs/reviews/2026-11-26-SA-06-*.raw.md`. | The feature text also says "review and prune the index", which has not been done; the pass test does not ask for it |
| **CC-14** Custom subagents | "The security reviewer cannot edit files; each agent uses an intentionally chosen model" | COVERAGE CC-14, 2026-11-04: a fresh `claude -p --agent security-reviewer` listed only Read, Grep and Glob on Opus, and when asked to edit `COVERAGE.md` it refused, making no tool call beyond Read (`docs/reviews/2026-11-04-found-2-security-review.md`, line 4). `.claude/agents/{security-reviewer,test-writer,advisor-reviewer}.md`: each model's reason is in its description (Opus for rarely run, high-stakes reviews; Sonnet for frequent, routine work) | The PRD's example agents (`rag-evaluator`, `a2a-contract-tester`, `catalog-data-generator`) do not exist; the pass test does not name them |
| **CC-27** Sandboxed Bash | "An attempt to reach an unlisted domain fails" | `docs/reviews/2026-11-04-cc-27-sandbox-evidence.md`: `deny network-outbound example.com:443 (host is not on the allow list)` while `github.com` returns 200; installed setting committed in `1d07a99`; `tests/hooks/test_sandbox_settings.py` pins the proposal. Seen again in every session since: this session's sandbox refused `.env` reads and writes outside the allowed paths | The feature text's "sandbox payment host" does not exist yet (P4); the pass test does not need it |
| **CC-04** Plan mode | "A reviewed plan exists for both epics before the first edit" | Payment port: BE-05 + CHK-05 (row #21), planned in plan mode, `docs/reviews/2026-11-11-BE-05-plan.md`, approved before code. RAG pipeline: RAG-01 to RAG-04 and RAG-11 planned in plan mode (COVERAGE CC-04 entries, e.g. `docs/reviews/2026-11-24-11-plan.md`) | **Check first:** the payment port *interface* was built in FOUND-3 (#19, `docs/reviews/2026-11-05-found-3-evidence.md`). If that row had no reviewed plan before its first edit, the pass test holds only from #21 onward. That is arguable, so the user decides |

## Stay `partial` (evidence exists, pass test not met)

| Row | PRD pass test | Evidence | Missing |
|---|---|---|---|
| CC-01 | "`/memory` lists the expected files; a clean clone behaves the same; a convention is followed without being prompted" | `CLAUDE.md` with `@PROGRESS.md`; conventions followed unprompted every row (requirement tags, commit subjects, the five git error strings) | A `/memory` listing; a clean-clone check; there are no per-service `CLAUDE.md` files (`find` finds only the root one) |
| CC-07 | "Each skill runs from the slash command and, where allowed, by model invocation; forked output stays out of the main context" | `/req-start`, `/req-done` and `gate-report` in `.claude/skills/`, each run from its slash command (COVERAGE CC-07; `/req-start SA-06` on 2026-11-26) | No model-invoked project skill; no `context: fork` skill |
| CC-15 | "Main-context usage stays below a budget you set, compared with doing discovery inline" | Explore and Plan subagents used (SA-02, RAG-03) | The budget comparison was never measured |
| CC-16 | "Three service builds complete concurrently with a combined review" | The full chain on many rows (AUTO-1 onward); #23 ran `test-writer` and `security-reviewer` in parallel on phase B (`docs/reviews/2026-11-26-SA-06-phase-b-security-review.raw.md`) | Three concurrent *builds* |
| CC-17 Git worktrees (now `todo`) | "Three branches advance at once with no file conflicts" | `git worktree list` on 2026-11-26: `northwind-shop` on `req/SA-06-human-handoff` and the linked worktree `northwind-shop-b` on `req/18a-record-all-prereqs`. Windows A and B ran in parallel all along: per-worktree ports (`LLM_PROXY_PORT`, CLAUDE.md), worktree layout in `5041f95` (FOUND-2), decision 0006's recovery for a failed switch, decision 0011 on linked worktrees. On 2026-11-26 `req/CHK-07-returns` (B) shipped and was merged into `req/SA-06-human-handoff` (A) with no conflicts (`06713f1`) | Only **two** worktrees at once, plus the runner, a clone (not a worktree) that does not advance a branch. `--worktree`, `isolation: worktree` and `.worktreeinclude` are unused. **Recommend `partial`**, not `done` |
| CC-18 | "You can attach to, read logs from and stop a background session" | In-session background tasks and subagents with completion notifications, logs read from output files, and a stalled run stopped with TaskStop (COVERAGE CC-18; #23's four background subagents) | No `--bg` session, no `claude agents`, no attach |
| CC-19 | "At least eight different events demonstrably fire; every blocked action fails to execute" | `PreToolUse` guard `.claude/hooks/guard_env.py`, with deliberate refusals recorded (COVERAGE CC-19) | One event type, not eight |
| CC-23 | "Epics 1, 3 and 5 acceptance pass; the same refund-cap rule behaves identically here and in Claude Code hooks" | Shopper Assistant and backend agents on the Agent SDK with in-process MCP tools, `allowed_tools` and `dontAsk` (`services/common/src/northwind_common/llm.py`; BE-01 to BE-08 rows); #23 adds the summary-only Care variant (`services/backend/src/northwind_backend/agents/care.py`) | Epic 5 (A2A) acceptance; the refund-cap rule as a Claude Code hook |
| CC-25 | "A denied action is refused in every mode you test; the precedence demo matches the documented order" | `.claude/settings.json` denies `.env` (COVERAGE CC-25); this session in auto mode: the sandbox denied `.env` (`make: stat: .env: Operation not permitted` on every `make` call, 2026-11-26) and the settings files are write-denied | Other modes tested; the precedence demo |
| CC-05 Context control (now `todo`) | "A cost-and-context report per epic" | Sessions summarized mid-row and resumed (COVERAGE CC-05, #17, #27). The per-suite LLM cost report exists (`make llm-spend`: per suite, per model, cache hit rate), but it covers the runtime's API spend, not the session's context | A per-epic cost-and-context report of the sessions themselves (`/usage`, `/context`). **Recommend `partial`**, since its own entries already show evidence |

## No honest evidence yet (stay `todo`)

| Row | What the repo shows |
|---|---|
| CC-02 Path-scoped rules | No `.claude/rules/` |
| CC-06 Session management | Sessions resumed from summaries, not `--resume`, `--continue` or `/fork` |
| CC-08 Legacy commands | No `.claude/commands/` |
| CC-09 Consuming MCP servers | No `.mcp.json` in the repo. Sessions use account connectors, which are not the project's servers |
| CC-11 External payment MCP | P4, not built |
| CC-12 Plugins | None packaged |
| CC-13 Output styles | No `.claude/output-styles/` |
| CC-20 Hook handler types | `command` only |
| CC-21 Headless and print mode | `claude -p` was used once, for checks (CC-07, CC-14, the CC-27 rig), with no `--output-format json`, `--json-schema` or `--max-budget-usd`. The pass test (schema-valid output; a budget cap stops a runaway run) is not met. **Could become `partial`** on that use, if the user counts it |
| CC-22 GitHub Actions | No `claude-code-action` in `.github/` |
| CC-24 Scheduled tasks | Fallback wake-ups were scheduled in-session (COVERAGE CC-18, BE-05) but not `/loop`, cron or `/schedule`; no failing-check report |
| CC-26 Checkpointing and `/rewind` | Not used |
| CC-28 Bundled review skills | `docs/reviews/2026-11-04-found-2-security-review.md` line 16 says the bundled `/security-review` and `/code-review` skills were not used; the project's own `security-reviewer` stands in for them |
| CC-29 IDE integration | This session (#23) runs in the VS Code extension. That is not evidence that a change was reviewed inline in the IDE or rewound from the editor; only the user can show that. **Could become `partial`** if the user confirms they read #23's diff in VS Code |
| CC-30 Agent teams, workflows, goals and the rest | No findings page per feature |

## Summary
- **Could move to `done` now:** CC-03, CC-14 and CC-27. CC-04 too, after the user checks FOUND-3's plan.
- **Could move from `todo` to `partial`:** CC-05 and CC-17. CC-21 and CC-29 too, if the user counts the uses above.
- Every other open row stays as it is.
