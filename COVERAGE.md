# COVERAGE: Claude Code feature matrix (PRD §6)

Evidence that each Claude Code feature was exercised with a passing test. The lab is done
when every row has evidence (PRD §9, P7). Status: todo | partial | done | dropped.
Features and pass tests are defined in docs/PRD.md §6; this file only tracks evidence.

| ID | Feature | Status | Evidence |
|----|---------|--------|----------|
| CC-01 | CLAUDE.md hierarchy, @import, CLAUDE.local.md, /memory, /init, /context | partial | 2026-11-03: project CLAUDE.md with stack, layout, hard rules, commands; imports @PROGRESS.md. Still to do: per-service CLAUDE.md files, a shared-standards import, a clean-clone check |
| CC-02 | Path-scoped rules in .claude/rules/ | todo | |
| CC-03 | Auto memory | todo | |
| CC-04 | Plan mode and /plan | partial | 2026-11-03: plan mode used before writing the PRD conversion and FOUND-0. Still to do: plans for the payment port and the RAG pipeline epics |
| CC-05 | Context control: /compact, /context, /model, /effort, /usage | todo | |
| CC-06 | Session management: --resume, --continue, /fork, /branch | todo | |
| CC-07 | Custom skills | todo | |
| CC-08 | Legacy commands in .claude/commands/ | todo | |
| CC-09 | Consuming MCP servers | todo | |
| CC-10 | Building MCP servers | todo | |
| CC-11 | External payment MCP server | todo | |
| CC-12 | Plugins | todo | |
| CC-13 | Output styles | todo | |
| CC-14 | Custom subagents in .claude/agents/ | todo | |
| CC-15 | Built-in Explore and Plan subagents | todo | |
| CC-16 | Chaining and parallel subagents | todo | |
| CC-17 | Git worktrees | todo | |
| CC-18 | Background agents | todo | |
| CC-19 | Hooks, lifecycle events | todo | |
| CC-20 | Hook handler types | todo | |
| CC-21 | Headless and print mode | todo | |
| CC-22 | GitHub Actions (claude-code-action) | todo | |
| CC-23 | Agent SDK (Python) | todo | |
| CC-24 | Scheduled tasks | todo | |
| CC-25 | Settings and permissions | todo | FOUND-0 proposes .claude/settings.json (deny .env read/edit/write, allow make/uv/pnpm/compose, ask before push). It is pending your review: Claude cannot write its own permission settings |
| CC-26 | Checkpointing and /rewind | todo | |
| CC-27 | Sandboxed Bash | todo | |
| CC-28 | Bundled review skills | todo | |
| CC-29 | IDE integration | todo | |
| CC-30 | Agent teams, workflows, goals, Chrome, Remote Control, channels | todo | |
