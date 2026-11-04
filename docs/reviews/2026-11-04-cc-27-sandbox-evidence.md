# CC-27 sandboxed Bash: evidence (2026-11-04)

**Status:** `partial`. Tested against the proposal files only; nothing is installed. The PRD's
"sandbox payment host" is left for P4, because no provider is chosen yet.

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
the fake key's hash was unchanged. Run it from your own terminal.

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

**Docker runs from the user's terminal, outside the sandbox.** That covers `make up` and
`make down`. In the sandbox, `docker ps` fails with `permission denied while trying to connect to
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

`make ci` was not run: `make scan-cassettes` fails until RAG-12's ledger-ID fix is on main (see the
follow-up below).

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

- **Anything after `SUITE=` reaches make.** `make record SUITE=a -f evil.mk` and
  `make record SUITE='$(shell cmd)'` run unsandboxed, and `make record SUITE=a LLM_BUDGET_USD=1000`
  would raise a cap (read from the Makefile, not run). `SUITE=*` has to be a wildcard, so the `ask`
  prompt is the only gate: read the whole command before approving. `$(shell ...)` is pinned as a
  strict xfail.
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
- **Not run under the sandbox:** a real `make record`, because it spends. `make eval-live` ran but
  exercised nothing, because there is no `evals/` directory until RAG-12 merges.

## Separate follow-up: `make scan-cassettes` fails today

This is not caused by the sandbox: it fails the same way unsandboxed. Ledger lines 103 and 104, from
the RAG-12 run, are flagged `luhn-valid-pan`. The real scanner flags only their shared `id` field, a
random 32-character hex id holding a 14-digit run that happens to pass Luhn. It is not card data.
Like finding 10 this is a scanner false positive, but on ids rather than float costs, so fixing
finding 10 will not fix it. It fails `make ci` until it is handled. Not changed here.

## Install (after RAG-12)

Decide F1 first.

1. In your own terminal: `make model`.
2. Copy `tools/settings.proposed.json` over `.claude/settings.json`.
3. Merge `tools/settings.user.proposed.json` into `~/.claude/settings.json`.
4. Restart Claude Code.

## Check after installing

1. `claude sandbox status` shows `enabled: true` and `strictMode: true`.
2. Ask Claude to run a bare `make eval-live`: it should prompt, then run outside the sandbox.
3. With the stack up from your terminal, `make test-integration` passes inside the sandbox. Measured
   in this worktree: 39 passed.
4. In your own terminal: `make scan-cassettes` reports `regex + key-fragment`.
