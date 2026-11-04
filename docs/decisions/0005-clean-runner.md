# 0005: The key lives in a runner the sandbox cannot touch (CC-27 F1/F3)

**Date:** 2026-11-04. **Status:** built on `req/CC-27-clean-runner`. The settings are proposals in
`tools/`, for the user to install. Supersedes the `excludedCommands` part of decision 0004, and in
decision 0001 the default key location, `LLM_RUN_BUDGET_USD` as a run's budget, and the rule that
every spending proxy holds the key.

## Why

Decision 0004 left two findings open. `make record`, `make eval-live` and `make llm-proxy` ran
outside the sandbox with the key, and they ran code the sandboxed session can edit (F1). While a
spending proxy ran, any sandboxed command could send requests through it, because its only check
was the public placeholder (F3).

## Decision

**The key is used by one process, from a clean clone.** Nothing is excluded from the sandbox any
more.

```
sandbox:          pytest / evals -> in-process proxy (record) -> cassettes, in the worktree
                                          | loopback, claimed token
user's terminal:  runner = proxy in live mode -> checks, ledger, budget, key -> api.anthropic.com
```

- **The runner** is the existing proxy in `live` mode with a gate. It holds the key, the ledger and
  the caps. It writes nothing into any worktree, so it never touches a sandbox-writable path.
- **The in-process proxy** keeps matching, cassettes and the scanner, and has no ledger and no key
  path. `proxy_config` is the one way to build it, for pytest, the eval CLI and `make llm-proxy`.
- **The clone is separate, from GitHub, at `~/.northwind/runner`.** It is not a linked worktree: its
  git directory must be one the sandbox has no route to.
- **The key file** is `~/.northwind/secrets/anthropic.env`, directory 0700, file 0600, outside every
  checkout. The runner takes only `ANTHROPIC_API_KEY` from it, and refuses a symlink, wider
  permissions or a missing key. The other reader is the key-fragment scan (`scan-cassettes` from the
  clone), which only compares it with file text.
- **Writes to all of `~/.northwind` are closed to the sandbox**, and reads of `secrets/` and the
  ledger lock. Bash is covered by `denyWrite` and `denyRead`, and the file tools by deny rules. The
  ledger and the clone stay readable. `make llm-spend` reads without the lock.
- **The denies are in user settings as well as project settings**, because project settings are per
  checkout, and an older branch keeps the old ones.
- **Inside the clone** uv uses a private cache (`~/.northwind/uv-cache`), because `~/.cache/uv` is
  sandbox-writable and a poisoned wheel would run with the key. `PYTHONPATH` is dropped.
- **What touches the key runs on `.venv-runner`**, which holds only `northwind-llm-replay` and
  `northwind-common`, with `python -I`. `make model`, which runs beside the key file, has its own
  `.venv-model` with `northwind-rag` and `northwind-data`. A full `.venv` puts every workspace
  member's `src` on `sys.path`, so a `sitecustomize.py` in any of them would load, and the clone has
  none.

### What the runner forwards

Only `POST /v1/messages` (query empty or `beta=true`), only `claude-sonnet-5-5` and
`claude-haiku-5-5`, and `max_tokens` up to the factory value. The body is also limited to what the
budget can bound:

- Top-level fields, `anthropic-beta` values, message keys and the nested settings (`output_config`,
  `thinking`, `context_management`, `metadata`) are allow-lists measured from all 208 recorded
  requests. A test replays every cassette through the gate.
- Content blocks are `text`, which is recorded, and `thinking`, `redacted_thinking`, `tool_use` and
  `tool_result`, which are not yet recorded and are allowed ahead for the assistant's tool loop.
- Server tools, MCP servers, images and documents are refused. They bill what the reservation cannot
  see, and some make Anthropic call a URL the caller picks.
- Bodies must be strict UTF-8 and declare a size of at most 2 MiB. Duplicate JSON keys are refused,
  because the gate and upstream might read different ones. Forwarded headers must be ASCII. At most 4
  requests run at once.

**A new need widens these lists in a reviewed merge.** The assistant's tools will be the first test:
client tools pass, but a new beta header, `tool_choice` or a new setting is refused until added.

Batches are refused too: the ledger cannot price them yet, so RAG-02 (#7) has a prerequisite. The
upstream is a constant in code that neither the CLI nor the environment can set. The runner binds
127.0.0.1 and refuses to start on a taken port.

### Key fragments

The runner checks each request, and buffers and checks each whole response, for fragments of the
key: raw text, decoded JSON and joined-up model text, because a key split across two SSE deltas only
shows once they are joined. A hit is a structured 502 that never echoes the match.

**That check is also an oracle, so its fragments are built from the secret part only.** The runner
drops the public prefix (`sk-ant-api03-`) first, so no 16-character fragment overlaps it. Otherwise
a window starting on the prefix would leave 3 unknown characters, and a hit could be extended one
character at a time. Now a guess has to get at least 14 unknown characters right at once (16, or 14
at the end of the key if keys carry a fixed `AA` suffix, as public scanner patterns suggest), which no
budget buys. The runner refuses a key with no known prefix, because that prefix would stay in the
fragments. The offline cassette scan keeps its whole-key fragments, because it answers nothing per
request: it reports a hit as one line with no path, so a guess per line or per file cannot be located.

On top of that, the request check runs after every free refusal, and a miss is charged against the
window's budget. Any hit closes the window and prints `ROTATE THE KEY`, and five refusals or upstream
400s close it too. **Treat that line as an incident: rotate the key, every time.**

### The window (F3)

A token in a file the sandbox can read would separate nothing, and a token passed through a prompt
or a command line would break hard rule 4. So the user opens a window, and the first caller claims
it:

- `make -C ~/.northwind/runner llm-runner SUITE=<suite> RUN_BUDGET=<usd>`. The budget is required
  and has no default. The time limit defaults to 30 minutes (`RUNNER_MINUTES`) and the idle limit to
  10 (`RUNNER_IDLE_MINUTES`).
- The first `POST /_runner/claim` for that suite gets a random token, which the run keeps in memory
  only. A second claim gets a 409, and another suite is refused. Requests must present the token.
- Release, the time limit, the idle limit, a spent budget, a blocked fragment or repeated refusals
  closes the window and the runner exits. One window is one run.
- The terminal prints one line per request: model, time to first upstream byte, tokens, cost, run
  total and ledger total. Nothing the caller chose is printed.

With no window nothing spends, whatever the session does. The caps come from the user's terminal, so
`LLM_BUDGET_USD=1000` on a sandboxed command does nothing.

No pytest run uses more than one process: `pytest-xdist` is not installed. The claim is per process,
so a spending run under an xdist worker is a usage error rather than a stream of 409s.

## What changes for the user

- **`.claude/settings.json` must be committed to main.** A merge of this branch alone leaves the
  tracked file with `excludedCommands`, so a new worktree or an older branch would run spending
  targets outside the sandbox with the key in reach, which is F1 again. Only the user can make that
  commit. `make runner-check` fails while any checkout has exclusions.
- **Each spending run needs a window from your terminal.** That is now the real gate: the `ask`
  prompts are a courtesy, because `Bash(make *)` and `Bash(uv run *)` reach the same code without one.
- **After each merge that matters, `make -C ~/.northwind/runner runner-sync`.** It shows every commit
  and changed file, the full diff of the key paths, and asks before it merges. Answering no leaves
  the runner working on what it had.
- **Run `model`, `up`, `down` and the key-fragment scan from the clone**, with
  `make -C ~/.northwind/runner …`. From a worktree those are unsandboxed runs of code the session can
  edit, the same shape as F1. `scan-cassettes` takes `CASSETTES_DIR=<worktree>/cassettes`, follows no
  symlink, and prints only path, line and kind, with non-printable characters escaped.
- **`make setup` in a worktree is the same shape**: `uv sync` and `pnpm install` run lifecycle
  scripts. Let it run sandboxed. The one exception, creating the main checkout's first local
  settings file, is a `cp` you can do by hand.
- **`COMPOSE_PROJECT_NAME` is pinned to `northwind`**, the name already in
  `infra/docker-compose.yml`. Any other name would be a new stack with an empty volume.

## Limits

- **The window authorises the sandbox, not one command. Accepted by the user, 2026-11-04.** F3 is
  narrowed, not closed. Any sandboxed command can claim first. While a run is up, any sandboxed
  command can also post to its in-process proxy with the placeholder, and the proxy adds the token. A
  session that can edit `conftest.py` is inside the run anyway, so no 409 need ever appear, and
  terminal lines look the same. A per-run local credential was considered and left out: the Agent SDK
  launcher writes the child's environment to a file in the temp directory, so it would raise the bar
  very little. What holds is the bound: one suite, the two models, the run budget, the time limit.
  **The habit that goes with it: open a window only to record, with a small explicit budget and the
  default time limit.**
- **The budget bound rests on the allow-lists.** It assumes a request built from allowed fields
  bills about what the reservation priced. If a run does overshoot, settled spend closes the window
  after that call, so the overrun is one call's worth per request in flight.
- **The fragment check sees text, not encodings.** A base64 or reversed key is not caught.
- **Timing is the one per-character signal left.** The check runs on the event loop, and its run time
  depends on the text. Estimated at microseconds per character, with every sample a charged upstream
  call, so it is impractical. Not measured.
- **The cassette-write key-fragment check is gone**, because the sandbox has no key. Regex scanning
  stays at write time, and the runner checks every response. The file-level fragment scan from the
  clone covers `CASSETTES_DIR` only, not a worktree's `.llm-proxy-log/` or `evals/`.
- **Buffering hides first-token latency.** The caller sees the first token when the response ends.
  So the runner logs time to first upstream byte per request, in its terminal line and the ledger's
  settle entry. #17 measures local overhead in replay with a streaming scripted model, and the
  estimate is the sum, recorded as NFR-13 partial. It is an estimate, and the first upstream byte of
  a stream comes slightly before the first token.
- **`preflight` catches accidents, not attacks.** A tampered copy can delete the check, which is why
  the docs always give the `make -C` form.
- **`make runner-check` proves things only for an honest session**, because it runs from the worktree
  a hostile session can edit. Its value is catching a wrong install, per checkout. It reads every
  checkout's settings, but only measures the sandbox of the one it runs in.
- **User-level denies need the sandbox to be on. Accepted by the user, 2026-11-04: no user-level
  sandbox for now.** In a repo without `sandbox.enabled`, or on a commit before 1d07a99, `denyRead` is
  inert, so a session there can read the key by Bash. A session started before the settings changed
  keeps its old policy. Turning the sandbox on at user level would close this, and would change every
  other repo.
- **A key typed into a shell lands in its history**, which the sandbox can read. Use an editor.
- **`services/llm-replay/spike/run_spike.py` still builds a direct proxy.** It is not wired to any
  target, and in the sandbox it can neither read the key nor reach the host.
- **Reading a symlink by file tool is unverified.** See Open.

## Open

- **A Grep route to the key (review finding 6): closed 2026-11-04.** The sandbox covers Bash only,
  so the user tested this with a fake key at the real path, before the real key went in. A
  subagent's Grep by path, Grep from `~/.northwind`, Grep through a directory symlink, Read and Bash
  `cat` were all denied: the guard hook stopped the first, second and fifth, the permission layer
  the third and fourth. This is a manual result as reported by the user; no automated test repeats it.
- **Postgres as a way out (review finding 10, candidate).** The app connects as the image's
  superuser, so `COPY … TO PROGRAM` may run code in the container, with open network and no key.
  PROGRESS row #9a replaces it with a non-superuser app role.
- F4 (`allowLocalBinding` is not loopback-only) and F6 for worktrees (shared caches) still stand.
- Runner support for Batches charging is a prerequisite of #7.

## Evidence

docs/reviews/2026-11-04-cc-27-sandbox-evidence.md, section "Clean runner".
