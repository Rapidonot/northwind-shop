# COVERAGE: Claude Code feature matrix (PRD §6)

Evidence that each Claude Code feature was exercised with a passing test. The lab is done
when every row has evidence (PRD §9, P7). Status: todo | partial | done | dropped.
Features and pass tests are defined in docs/PRD.md §6; this file only tracks evidence.

| ID | Feature | Status | Evidence |
|----|---------|--------|----------|
| CC-01 | CLAUDE.md hierarchy, @import, CLAUDE.local.md, /memory, /init, /context | partial | 2026-11-03: project CLAUDE.md with stack, layout, hard rules, commands; imports @PROGRESS.md. Still to do: per-service CLAUDE.md files, a shared-standards import, a clean-clone check |
| CC-02 | Path-scoped rules in .claude/rules/ | todo | |
| CC-03 | Auto memory | todo | |
| CC-04 | Plan mode and /plan | partial | 2026-11-03: plan mode used before writing the PRD conversion and FOUND-0; 2026-11-04: FOUND-1 planned in plan mode, the user approved with four changes (wide-frame text, strict under-150, billing open item, separate docs PR), which were applied before implementation; FOUND-2 planned in plan mode, the user approved with ten changes (leak search + Linux replay on day 1, diff-driven scrub, reservation guard, measured max_tokens, from-scratch child env, NO_PROXY, verified package names/prices, scanner details, proxy-log scan, checkpoint exclusion), all applied, plus a mid-task worktree addendum. Still to do: plans for the payment port and the RAG pipeline epics |
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
| CC-19 | Hooks, lifecycle events | partial | 2026-11-04: the user's PreToolUse hook `.claude/hooks/guard_env.py` (NFR-02) blocked Claude's Bash commands whose text named `.env` (a commit message, a Python heredoc, a grep), so those edits went through the Edit tool instead. It also blocked Claude from running `ruff format` on the hook file even after the user said yes ("Claude may not change its own settings or hooks"), so the user applied that fix. Still to do: other lifecycle events (PostToolUse, Stop, SessionStart), handler types (CC-20) |
| CC-20 | Hook handler types | todo | |
| CC-21 | Headless and print mode | todo | |
| CC-22 | GitHub Actions (claude-code-action) | todo | |
| CC-23 | Agent SDK (Python) | partial | 2026-11-04 (FOUND-2): `claude-agent-sdk==0.2.178` `query()` run through the record/replay proxy, with options from `northwind_common.llm.agent_options` (`--bare` via `extra_args`, `setting_sources=[]`, custom system prompt, `cli_path` = an `env -i` launcher because the SDK merges `os.environ`, `max_turns`, `tools=[]`). Recorded on Haiku 5.5 and Sonnet 5.5; replays with no key on macOS and Linux (`services/llm-replay/tests/test_spike_replay.py`). Still to do: custom tools, MCP servers, hooks, subagents and sessions in the Shopper Assistant (SA-02 onward) |
| CC-24 | Scheduled tasks | todo | |
| CC-25 | Settings and permissions | partial | 2026-11-03: project `.claude/settings.json` (deny .env read/edit/write, allow make/uv/pnpm/compose, ask before push). In auto mode, Claude's Read of `.env` and Write of `.env.probe` were refused ("denied by your permission settings"). Separately, the auto-mode classifier blocked Claude from writing its own settings file, so the user added it. 2026-11-04: the `.env` deny rule also refused a Bash output redirect to `.env` written after a `cd` into the scratchpad; the refusal named the repo's `.env` path, so the rule was evaluated against the session's working directory. Still to do: test in the other modes, a precedence demo with a local override, bypassPermissions in a sandbox only |
| CC-26 | Checkpointing and /rewind | todo | |
| CC-27 | Sandboxed Bash | todo | |
| CC-28 | Bundled review skills | todo | |
| CC-29 | IDE integration | todo | |
| CC-30 | Agent teams, workflows, goals, Chrome, Remote Control, channels | todo | |
