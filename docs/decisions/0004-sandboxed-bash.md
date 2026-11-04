# 0004: Claude's Bash runs in the OS sandbox (CC-27)

**Date:** 2026-11-04. **Status:** proposed; the user installs it after RAG-12.

## Decision

Claude's Bash commands run in Claude Code's sandbox, with these rules:

- Writes are denied to `.claude/settings*` and `.claude/hooks/`.
- Reads are denied to `.env*` in this checkout and the main checkout, and to `~/Library/Keychains`.
- The network allow-list is the package indexes and GitHub, in strict mode.
- `api.anthropic.com` is not on the list. Only `make record SUITE=*`, `make eval-live` and
  `make llm-proxy` reach it directly, because they are excluded from the sandbox and stay behind
  `ask` rules.
- `dangerouslyDisableSandbox` is ignored, and a missing sandbox stops the session rather than
  running unconfined.

## Why

The guard hook is a text match. R1 and R2 write to a protected file through a command it treats as
safe. The sandbox refuses those writes at the OS level, whatever the command looks like.

## What changes

- **The user pushes.** Claude can commit, but with the keychain denied it cannot `git push` or use
  `gh`, so PRs are opened from the user's terminal.
- **The user runs Docker.** `make up` and `make down` run from the user's terminal, outside the
  sandbox, because the sandbox denies the Docker socket. A linked worktree needs no key file for
  this (FOUND-2 review #12); one is never created there.
- **`make test-integration` runs inside the sandbox** once the stack is up. It reaches Postgres on
  the local port and needs no Docker access. Measured: 39 passed.
- **`make model` runs once in the user's terminal.** The sandbox cannot reach the model's hosts.
- **The domain list lives in user settings.** `strictAllowlist` is ignored in project settings, and
  while it is on, project `allowedDomains` are dropped.

## Differs from the PRD

CC-27 lists "the package index, GitHub and the sandbox payment host". The payment host is left for
P4, because no provider is chosen. `api.anthropic.com` is reachable by the three excluded targets,
which the PRD row does not mention; decision 0001 needs it for recording.

## Open before install

Finding F1: excluded targets run code the sandboxed session can edit, with the key available. The
user decides how to handle it. F3, F4 and F6 are also open.

## Evidence

docs/reviews/2026-11-04-cc-27-sandbox-evidence.md, which also lists the limits.
