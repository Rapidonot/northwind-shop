# CC-27 sandboxed Bash: evidence (2026-11-04)

**Status:** `partial`. The rig results below were measured against the proposal files of the time,
which excluded three targets from the sandbox. The user installed that proposal (committed in
1d07a99), and a live session ran under it: `example.com` got the proxy's 403 and a write to `~` was
refused. The PRD's "sandbox payment host" is left for P4, because no provider is chosen yet.

**Decision 0005 replaces the exclusions** with a key-holding runner, as a proposal not yet installed.
See "Clean runner" below; the install steps and checks at the end are the current ones. Sections on
`excludedCommands`, and the F1, F3 and first two Limits entries, describe the earlier setup.

**Files:** `tools/settings.proposed.json` becomes `.claude/settings.json`.
`tools/settings.user.proposed.json` merges into `~/.claude/settings.json`.
**Environment:** Claude Code 2.1.318 on macOS (Seatbelt), Python 3.12, pnpm 12.16.1.

## Method

Fields were confirmed from the schema text in the installed binary and the published docs. The
docs came back only partly verbatim, so the binary is the source for anything that mattered.

Runs used a throwaway rig under `~`, because the temp directory is writable by default and would
have hidden denials. The rig was a real main checkout plus a linked worktree, with a fake key. A
nested `claude -p` session ran each command in the sandbox. Toolchain runs used a clone of this
repo in the same layout. The real settings, hook and key file were never touched.

Differences from an installed setup:

- `autoAllowBashIfSandboxed` was true and the `ask` rules were dropped, except where stated, because
  `-p` cannot answer a prompt. This changes prompting, not OS policy. With auto-allow off, a separate
  permission layer refuses any command holding a variable or substitution before it runs.
- For the R1 and R2 runs the session loaded its settings from `--settings` with absolute paths, and
  the rig's `.claude/` files were fixtures. Run against what it took to be its own settings, the
  nested model declined the tampering commands. The project-file checks below cover the `./` and `~`
  path forms.
- The sandbox-off control is a plain script, not an agent. A nested agent with permissions bypassed
  was refused by the auto-mode classifier, and the control does not need one.

## R1 and R2 at the OS level

Same commands, sandbox on and off. Both protected files were hashed after every case.

| Finding | Command | Sandbox on | Sandbox off |
|---------|---------|------------|-------------|
| R1 | `git status $(rm .claude/settings.json)` | `rm: Operation not permitted` | File deleted |
| R1 | `` git log `cp ../outside/weak.json .claude/settings.json` `` | `cp: Operation not permitted` | Overwritten, 3 bytes |
| R1 | `git diff > >(tee .claude/settings.json)` | `tee: Operation not permitted` | Emptied, 0 bytes |
| R1 | `git commit -m "$(rm .claude/hooks/guard_env.py)"` | `rm: Operation not permitted` | Hook deleted |
| R2 | `git diff " -m '" > .claude/settings.json "'"` | `operation not permitted` | Redirect ran; file already empty from the case above, so this case alone shows no change |
| R2 | `git log --grep=" -m 'x" > .claude/settings.json # '` | `operation not permitted` | 0 bytes |

With the sandbox on, both hashes stayed the same across all seven checks on the final proposal. The
0-byte result is the same damage as the empty-settings incident.

Each of these still passes the hook, so the 22 strict xfails in `test_guard_env.py` stay.

## Reading the key

| Attempt, sandboxed | Result |
|--------------------|--------|
| `python3 -c` reading `../main/.env`, name built so the guard cannot see it | `PermissionError: Operation not permitted` |
| The same by absolute path | `PermissionError` |
| `python3 -c` reading this checkout's `.env.local` | `PermissionError` |
| `cat .en*` and `cat ../main/.en*` | `Operation not permitted` |
| `os.path.exists` on the key file | `False`: the deny hides that it exists |
| Control: `python3 -c` reading `../main/README` | Works |
| A non-excluded target in the rig's Makefile (`lint`), whose recipe reads the key file | `Operation not permitted` |
| The rig's `make eval-live`, `make record SUITE=a`, `make llm-proxy` (excluded), same recipe | Read it: `key-file-bytes=58` |

The first two rows also passed from a project file with `./` and `~` paths and the real guard
active. This closes the OS side of `cat .en*`, `cat .e''nv` and finding 15c for Bash.

The proof used a fake key by design. A proof against the real file would have printed the real key
if the deny had failed (hard rule 4).

## How `excludedCommands` matches

A probe was written to a directory the sandbox denies, so a probe file only appears if that part ran
outside the sandbox. The first pattern tried, `make record*`, was too wide:

| Command | With `make record*` | With `make record SUITE=*` |
|---------|---------------------|----------------------------|
| `make record SUITE=a` | Unsandboxed | Unsandboxed |
| `make recording` | **Unsandboxed** | Sandboxed |
| `make record -f evil.mk` | **Unsandboxed** | Sandboxed |
| `make record SUITE=a -f evil.mk` | Unsandboxed | **Unsandboxed: known limit** |
| `make record 'SUITE=x; cmd'` | Not tested | Sandboxed: the quote stops it matching |
| `make record SUITE='x; cmd'` | Not tested | **Ran `cmd` unsandboxed.** Fixed in the recipe (F7) |

Exact patterns are exact: `make eval-live -f evil.mk`, `make eval-live TAG=arg`,
`make llm-proxy -f evil.mk` and `make llm-proxy LLM_MODE=live` all stayed sandboxed.

**`make eval-live` now needs `EVAL_SLICE=*`.** RAG-12 made `EVAL_SLICE` required, so the exact pattern
above could only match a command that prints usage, and a real run would have stayed sandboxed. The
proposal now excludes `make eval-live EVAL_SLICE=*`. `EVAL_SLICE` had reached the shell unquoted, so
the recipe now takes digits only and quotes it, as F7 did for `SUITE`. Tests in
`test_makefile_targets.py` were seen failing first. `all` is refused too: a number at or above the
item count is a full run, and `EVAL_SLICE=100` covers today's 100 items. The new pattern was not run
on the rig. It has the same shape as `SUITE=*`, so expect the same matching and the same limit.

**Compound commands are safe.** `&&`, `;`, `|`, `$(...)`, backticks, process substitution and
redirects all left the whole call sandboxed, including the first command. No probe appeared.

**The word-order gap from finding 1 is closed.** `make -s record`, `make TAG=x record`,
`FOO=1 make record` and `cd . && make record` stayed sandboxed and got a 403 from
`api.anthropic.com`. A direct `curl` got the same 403.

**The practical trap:** `make record SUITE=x | tail` runs sandboxed and fails, because the pipe is a
second command. Run the three targets bare.

The `ask` rules still fire on excluded commands: with them kept, `make record SUITE=a -f evil.mk`
stopped before running. From a project file even a bare `make eval-live` stopped with "This command
requires approval", despite an allow rule, so the positive case is on the checklist below.

## Network

| Host, sandboxed | Result |
|-----------------|--------|
| `example.com` | `deny network-outbound example.com:443 (host is not on the allow list)`, HTTP 403 |
| `api.anthropic.com` | Denied the same way |
| `huggingface.co` | Denied the same way |
| `github.com`, `pypi.org` | 200 |
| `127.0.0.1:5432` | Connects, through `allowLocalBinding` |

"For the proxy only" cannot be expressed as an allow-list entry: the list covers every sandboxed
command. So `api.anthropic.com` is not listed, and sandboxed commands cannot reach it directly. They
can reach it indirectly while a record or live proxy is running: see F3 below.

`strictAllowlist` only counts from user, managed or `--settings` sources, and while it is on,
project `allowedDomains` are dropped. That is why the domains live in the user file.

## Strict mode and other repos

| Check | Result |
|-------|--------|
| `claude sandbox status`, repo without `sandbox.enabled`, user block installed in a temp config dir | `enabled: false`, `strictMode: false`: the same as today |
| The same, rig with `sandbox.enabled` | `enabled: true`, `strictMode: true` |
| Live session in a repo without it, strict block loaded through `--settings` | `example.com` 200, write outside the working directory succeeded |
| Code | `strictAllowlist` is read only when building the sandbox config |

Your user settings have no `sandbox` block, and no project settings file under `~/Projects` sets
one. Not tested live: WebFetch in another repo. The schema says in-process tools are not gated.

A repo that turns on `sandbox.enabled` later will inherit this allow-list and lose its own
`allowedDomains`.

## Credentials

The plan said sandboxed `git` and `gh` would lose the saved sign-in. That was wrong: with only the
original proposal, `gh api user` returned the login, `gh auth token` returned a 41-character token,
and `git push --dry-run` authenticated. With `github.com` allowed, any sandboxed command could have
used that token.

Adding `~/Library/Keychains` to `denyRead` fixes it: `gh auth token` gives the error text, `gh api
user` gets a 401, and `git push` fails with "could not read Username". Keychain access was outside
the brief, so this entry is an addition to review. Removing it restores pushes from Claude and the
exposure together.

## Toolchain under the final proposal

`make lint` exit 0 and `make test` exit 0, with this branch's code and tests copied into the clone.
`uv sync`, `pnpm install`, `git commit` in a linked worktree, `make llm-spend` and `make help` in a
main checkout all work. `make model` works offline from a warm cache, with `huggingface.co` blocked.
`make setup` in a main checkout exits 2: `.env` looks absent, so it tries `cp`, which is refused, and
the fake key's hash was unchanged. Do not run it from your own terminal: that runs lifecycle
scripts unsandboxed (decision 0005). Copy the file by hand, and let the rest run sandboxed.

Vitest prints `ERROR: failed to copy trust settings` 12 times, starting at `vitest run`. The tests
pass. It is probably the keychain deny, but vitest was never run sandboxed without it.

The deny exposed two breakages, fixed on this branch with tests seen failing with the fix reversed:

- `include .env` stopped every `make` target in a main checkout. Now `-include`.
- `make scan-cassettes` crashed on `PermissionError`: on Python 3.12 `Path.exists()` raises rather
  than returning false. It now runs regex only and says `regex only (no readable key file)`.
- `load_api_key` had the same bug, which would have left a worst-case reservation open. It now raises
  `RuntimeError`, the error the proxy already handles.

Other findings: pnpm takes a cross-store lock at `/private/tmp/pnpm-store-operation-locks-502`, so
that path is in `allowWrite`. A wildcard for it matched the directory but not the lock file inside.
`docker ps` is denied.

**Embedding model (item 4):** pre-cached, not allow-listed. The download needs `huggingface.co`, then
`cas-server.xethub.hf.co`, and possibly more. `make model` fills a stable `FASTEMBED_CACHE_PATH`.

## Docker and Postgres

Measured in a live session in this worktree with the sandbox active, not on the rig. The worktree
has no key file, by design.

**Docker runs from the user's terminal, outside the sandbox**, in the runner clone since decision
0005: `make -C ~/.northwind/runner up`. That covers `make up` and `make down`. In the sandbox, `docker ps` fails with `permission denied while trying to connect to
the docker API at unix:///Users/rapido/.docker/run/docker.sock`.

**FOUND-2 review #12 is fixed.** `make up` stopped in a linked worktree with `couldn't find env
file`, before it reached Docker. `COMPOSE` now passes `--env-file` only when the file exists.
Otherwise compose uses the defaults in `docker-compose.yml`, which match `env.example`.
`test_compose_gets_the_env_file_only_when_it_exists` covers `up` and `down`, with and without the
file, and its no-file cases were seen failing before the fix. `docker compose config --quiet` exits
0 here without the file. Not run: a real `make up` after the fix, which needs the user's terminal.

**`make test-integration` reaches Postgres from inside the sandbox.** With the stack already up from
the user's terminal, it exited 0 with 39 passed and 512 deselected, on two runs. The healthz test
asserts `db` is `ok` and that pgvector is installed. With no key file, the tests use the default
`DATABASE_URL` in the code, which matches `env.example`.

The shared database was safe to use while RAG-12 is open. The 38 RAG tests each work in a throwaway
`rag01_test_<uuid>` schema and drop it, and the healthz test only reads. Another process's test
schema appeared and cleared itself just after these runs, and no test schema remained afterwards.

**The stack is shared.** The compose project name is fixed as `northwind`, so `make up` and
`make down` act on the same stack from any checkout. `make down` in a worktree stops the database
for the main checkout too. If the main checkout's key file sets non-default `POSTGRES_*` values,
`make up` from a worktree would recreate the container with the defaults. Read from the compose
file, not run. The main checkout's values were not read.

After the RAG-12 merge, `make test-integration` gave 49 passed in the sandbox. `make ci` was not run
here: it now includes `make eval-gate`, which writes to the shared index through `make ingest`, so
that is left for `/req-done`.

## Security review

The `security-reviewer` subagent read the diff and the proposal: 2 high, 5 medium, 4 low, plus 5
unconfirmed candidates. As settings, the proposal is stronger than today's. Everything marked
measured was run on the rig with a fake key.

**Fixed on this branch**

| Finding | Severity | Issue | Fix |
|---------|----------|-------|-----|
| F2 | High | `~/.northwind` in `allowWrite` let a sandboxed command erase the spend record | Narrowed to `~/.northwind/ledger.lock`. Measured: the ledger is refused, the lock opens, `make llm-spend` works |
| F5 | Medium | `load_api_key` raised `PermissionError`, leaving a reservation open | See above |
| F7 | Medium | `make record SUITE='x; cmd'` ran `cmd` unsandboxed. Measured: the probe file appeared | `SUITE` is checked as a plain name and quoted. The make-level form remains, see Limits |
| F8 | Medium | The tests passed on a weakened proposal, for example an added `allowRead` | Blocks are compared whole |
| F9 | Medium | No file-tool deny for the main checkout's key | Added `Read(~/Projects/northwind-shop/.env*)`. Measured: the Read tool is refused. The Grep route does not exist here: this build has no Grep tool |
| F10 | Low | `make setup` tries to overwrite the key in a main checkout | Added `./.env*` to `denyWrite`. Measured, see above |
| F12 | Low | Documentation gaps | Fixed here |

**Candidates measured and refused:** a hard link to the key, a hard link to the settings file,
`.claude/SETTINGS.LOCAL.JSON`, `.claude/HOOKS/`, `.CLAUDE/`, `mv .claude`, `rm -rf .claude`,
`cd .claude && touch settings.json`, a symlink to the key, `cp` of the key, and rewriting the
worktree's `.git` file.

**Open, for the user to decide**

| Finding | Severity | Issue | Options |
|---------|----------|-------|---------|
| F1 | High | `make record` runs the proxy inside the pytest process, so code the sandboxed session can edit (`conftest.py`, `proxy.py`, `.venv`) later runs with the key and no sandbox. The prompt shows only the normal command. `make model` and `make up` have the same shape from your terminal | Best: run the key-holding proxy from a clean checkout the session cannot write, and drop the exclusions. Minimum: a check in the write-protected hook that refuses the three targets on a dirty tree. Not run |
| F3 | Medium | While a record or live proxy runs, a sandboxed command can send requests through it, because its only check is the public placeholder. The key is not disclosed and caps still apply | A random per-process token. Not run |
| F4 | Medium | `allowLocalBinding` is not loopback-only. Measured: a sandboxed listener bound `0.0.0.0` and accepted a connection on this machine's LAN address. Tested from the same machine | No narrower setting found. Enable the macOS firewall. Our tests need the setting |
| F6 | Medium | `~/.cache/uv` and the pnpm store are shared with other projects, so a sandboxed write could outlive the worktree. Whether uv and pnpm re-verify cached content is unconfirmed | Per-worktree caches, then drop both entries |

Also unconfirmed: a change staged with `git update-index` and committed in the sandbox could alter a
protected file when you later check it out. It would show in the diff.

## Limits

- **Anything after `SUITE=` or `EVAL_SLICE=` reaches make.** `make record SUITE=a -f evil.mk` and
  `make record SUITE='$(shell cmd)'` run unsandboxed, and `make record SUITE=a LLM_BUDGET_USD=1000`
  would raise a cap (read from the Makefile, not run). `make eval-live EVAL_SLICE=1 -f evil.mk` has
  the same shape (not run). Both patterns have to be wildcards, so the `ask` prompt is the only gate:
  read the whole command before approving. `$(shell ...)` is pinned as a strict xfail for both.
- **Excluded targets are fully unsandboxed**, with the key and the network. See F1.
- **The sandbox covers Bash only.** Read, Edit and Write follow permission rules and the guard. The
  `Edit` and `Write` deny rules closed R3's Write tool case in the rig, with `notes.txt` as the
  allowed control.
- **`allowLocalBinding`** opens every local service to sandboxed commands, including Postgres and a
  running dev proxy. See F3 and F4.
- **`make scan-cassettes` is regex only in the sandbox**, the same as CI, and `make ci` then passes
  without the key-fragment check. That check needs your terminal.
- **`denyRead` names the main checkout's path.** A key file moved with `LLM_ENV_FILE` is not covered.
- **Other copies of a key**, such as shell history, stay readable. Not checked.
- **macOS only:** `~/Library/...` and the `502` uid are specific to this machine.
- **After install, nothing fails if `.claude/settings.json` loses its `sandbox` block.** The tests pin
  the proposal file. Extend `test_settings.py` to the installed file when you install.
- **Not run under the sandbox:** a real `make record` or `make eval-live EVAL_SLICE=…`, because both
  spend.

## Closed follow-up: `make scan-cassettes` false positive

This was not caused by the sandbox: it failed the same way unsandboxed. Ledger lines 103 and 104, from
the RAG-12 run, were flagged `luhn-valid-pan`. The scanner flagged only their shared `id` field, a
random 32-character hex id holding a 14-digit run that happened to pass Luhn. It was not card data.
RAG-12 fixed it on main with digit-free ledger IDs and a narrow scanner exemption. After the merge,
`make scan-cassettes` exits 0 in this worktree with 0 findings. That run was in the sandbox, so
regex only (see Limits).

## Clean runner (F1 and F3, decision 0005)

**Measured on this branch, in a sandboxed session under the settings installed at 1d07a99.** These
are not yet the new settings, which are for the user to install.

| Check | Result |
|-------|--------|
| Probe write in `~/.northwind` | Refused |
| Probe write in the interpreter's `bin` and stdlib (Homebrew) | Refused |
| Probe write in `~/.local/share/uv/python` and `~/.local/bin` (uv) | Refused |
| Write to `~/.northwind/ledger.lock` | **Allowed**, as the installed settings say. The proposal removes it |
| Probe write in the git common dir's `hooks/`, append to its `config` | Refused, so review finding 4 does not reach here. `make runner-check` keeps measuring both |
| Write in `<common dir>/worktrees/<name>/` | Allowed, and needed for commits. `config.worktree` is only read if `config` turns it on, which is refused |
| `curl https://api.anthropic.com/`, no credentials | `deny network-outbound api.anthropic.com:443 (host is not on the allow list)` |
| `.venv/bin/python -I` imports `northwind_llm_replay.runner` | Works, and the shell's DaVinci Resolve `PYTHONPATH` entry is gone |

So the interpreter needs no private install today: `UV_PYTHON_INSTALL_DIR` is not set. `make
runner-check` repeats this against the real clone after install.

`api.anthropic.com` is not in `tools/settings.user.proposed.json`, and a test pins that. The
installed `~/.claude/settings.json` was not read here, so confirm it at install.

**Not measured:** a real recorded call through the runner, because it spends. Everything else about
the runner ran against a scripted upstream, with tests in `test_runner.py` and
`test_runner_support.py`. That includes a record run writing its cassette in the worktree while the
ledger entry came from the runner.

**F1 and F3 outcome:** see decision 0005, including what stays open. `$(shell ...)` and
`-f evil.mk` on `make record` no longer matter, because nothing runs outside the sandbox, so their
two strict xfails are gone.

### Security review of the runner

`security-reviewer` read the first build, by reading only: 17 findings, and it advised against
installing until 1 to 3 were fixed. "Confirmed" means confirmed by reading, unless a row says measured.

| # | Severity | Issue | Outcome |
|---|----------|-------|---------|
| 1 | High | The request fragment check was a free oracle: about 600 requests, at $0, could recover the key | **Fixed.** Runs after every free refusal, any hit closes the window, 5 refusals close it, 2 MiB cap |
| 2 | High | The worst case did not bound cost: URL sources, server tools and MCP servers bill what the ledger cannot see | **Fixed.** Allow-lists measured from 208 recordings, 4 in flight, window closes on a spent budget. Upstream billing was not verified |
| 3 | High | The key moved to a path existing settings do not deny, and the install order exposed it | **Fixed in the steps below**: settings first, key last, denies also in user settings. Whether user and project lists merge is measured per checkout by `make runner-check` |
| 4 | High if real | Hooks or config in the git common dir, run by your terminal's git | **Measured, refused.** Above |
| 5 | Medium | Docs sent the terminal to sandbox-owned code | **Fixed**, including copying the settings from the clone |
| 6 | High if real | A Grep route to the key through a symlink | **Open.** Unverified, and the hook is the user's. See step 4 below |
| 7 | Medium | `runner-sync` filtered what the runner loads; a no left `origin/main` moved and blocked windows | **Fixed.** Unfiltered log and stat, full diff of key paths, `.venv-runner`, fetch to its own ref |
| 8 | Medium | The in-process proxy reopens F3 one hop down | **Not fixed; stated as a limit in decision 0005, for the user to decide** |
| 9 | Medium | File names and commit subjects reach the terminal raw | **Fixed.** Escaped, and `cat -v` |
| 10 | Medium candidate | Postgres `COPY … TO PROGRAM` runs outside the sandbox | **Open**, not run. Predates this work |
| 11 | Low | `runner-check` could pass falsely | **Fixed**: a missing key file is reported. It still proves things only for an honest session |
| 12 | Low | A non-ASCII header left a reservation open | **Fixed.** Refused in the gate |
| 13 | Low | Work before auth: idle resets, unlimited body reads, unstructured 500s | **Fixed** |
| 14 | Low candidate | A held lock could hang the runner | **Fixed**: the lock is in `denyRead` |
| 15 | Low | Scanner gaps: a parent swap race, skipped types, roots | **Stated as limits.** Not fixed |
| 16 | Low candidate | Duplicate JSON keys | **Fixed.** Refused |
| 17 | Medium | Decision 0005 understated limits | **Fixed** in the decision |

A second pass read the fixes above: 1 high, 6 medium and 7 low, and it advised against putting the
real key on disk until the first was dealt with. **The fixes for this second pass have not been
reviewed.**

| # | Severity | Issue | Outcome |
|---|----------|-------|---------|
| 1 | High | Merging leaves `excludedCommands` in the tracked settings, so F1 returns after install | **Needs the user.** Claude cannot write that file. Install step 3 commits it, and `runner-check` fails on any checkout with exclusions |
| 2 | Medium | A header that broke JSON parsing made a miss free and stranded a reservation; "one bit per window" was wrong | **Fixed**, and upstream 400s count as refusals. The decision is corrected |
| 3 | Medium | `image` and `document` bill beyond the reservation, and were never measured | **Fixed.** Removed |
| 4 | Medium | The allow-list stopped at the top level | **Fixed.** Message keys and nested settings pinned to the recordings |
| 5 | Medium | User-level denies miss running sessions and sessions without the sandbox | **Step 3 says quit every session. The second route is a stated limit**, for the user to decide |
| 6 | Medium | `model` and `up` code was not in the full diff | **Fixed**, and the key now goes in last |
| 7 | Medium | Answering no still moved `origin/main` | **Fixed** with `--refmap=`, and measured with real git in a test |
| 8 | Low | `runner-check` would crash on a denied stat | **Fixed** |
| 9 | Low | `claim` read an unbounded body; refused callers could flood the terminal | **Fixed.** A token holder stalling four bodies until the time limit remains |
| 10 | Low | `.venv-runner` could lag merged code | **Fixed**: `llm-runner` refuses if it does not match the lock |
| 11 | Low candidate | `.gitattributes` could hide a diff | **Fixed** |
| 12 | Low candidate | A UTF-16 body passed the fragment check | **Fixed.** Refused |
| 13 | Low candidate | The file scan as an oracle | **Fixed**: no line number, plus a rotate notice |
| 14 | Low candidate | How to enter the key | **Fixed** in step 6 |

## Install

Order matters: **settings first, key last.** Until a session restarts on the new settings, nothing
denies the key's new path.

1. Merge. The runner needs this code on `origin/main`.
2. Once: `git clone https://github.com/Rapidonot/northwind-shop.git ~/.northwind/runner`, then
   `make -C ~/.northwind/runner runner-sync`. It holds no key yet.
3. **Commit the settings to main.** From a fresh branch, copy
   `~/.northwind/runner/tools/settings.proposed.json` over `.claude/settings.json`, using the clone's
   copy because a session can edit a worktree's. Commit, merge, and merge main into every open
   branch, or retire it. Merge `~/.northwind/runner/tools/settings.user.proposed.json` into
   `~/.claude/settings.json` and confirm it lists no Anthropic host.
4. **Quit every Claude Code session in every checkout**, and confirm none remain. A running session
   keeps its old policy.
5. **Fake key first.** Create `~/.northwind/secrets` (0700) and `anthropic.env` (0600) with a fake
   value. Start one session. Ask Claude to try to reach it with a subagent's Grep: by path, from
   `~/.northwind`, and through a symlink in the worktree. Then ask for `make runner-check` in each
   checkout. If anything gets through, stop and fix the hook.
6. In your own terminal: `make -C ~/.northwind/runner model`. It runs unsandboxed, so run it before
   the real key arrives.
7. Revoke the old key. Put the rotated one in with an editor, not `echo`, because shell history is
   readable from the sandbox. Clear the clipboard afterwards. Remove the old key from the main
   checkout's key file.

## Check after installing

1. `claude sandbox status` shows `enabled: true` and `strictMode: true`.
2. In each checkout, ask Claude to run `make runner-check`: exit 0, every write refused, key hidden,
   no checkout with exclusions.
3. With the stack up from your terminal, `make test-integration` passes inside the sandbox. Measured
   in this worktree: 49 passed after the RAG-12 merge.
4. With no window open, `make record SUITE=<suite>` stops at once and prints the command to open one.
5. In your own terminal: `make -C ~/.northwind/runner scan-cassettes
   CASSETTES_DIR=<worktree>/cassettes` reports `regex + key-fragment`.
6. **Spends:** open a small window, `make -C ~/.northwind/runner llm-runner SUITE=eval-answers
   RUN_BUDGET=0.50`, then ask Claude to run `make eval-live EVAL_SLICE=1`. Unchanged requests replay
   for free. Your terminal prints a line per request that does go out.
