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

## `make finish`

One user-run command closes a row: it commits what `/req-done` wrote, pushes, opens the PR from a committed
body file, adds labels, and runs `ship`, which still asks. It runs from the clone, because it holds the user's
GitHub login, and reaches it only through `runner-sync`.

- **`ci-change` is added by `finish`, so that label is weaker.** `ship`'s account check (decision 0008) still
  passes, but it no longer shows that a person chose it. What is left is the merge question, with the full diff
  of `.github/workflows` above it. The user accepted this at plan approval.
- **`baseline-change` stays by hand**, because it guards a High residual, and **`gate-change` is never added**: it
  accepts a failure that has not happened yet, so adding it before CI would pre-waive main's gate.
- **`NW_ALLOW_CONTROL` is never set.** If the hook refuses, the user reads the paths and reruns with it.

## Routine rows

`CLAUDE.md`, "Running a row", defines them. On a routine row `advisor-reviewer` approves the plan in place of
stop 1. Stops 2, 3, 6, 7 and 10 are the user's four reasons to stop. Stops 4, 5, 8 and 9 stay, because a row
that hits one has stopped being routine.

The user decided at plan approval that a routine row may add cassettes through `make record` in a window they
opened, so stop 8 does not fire for that one case. What "through `make record`" excludes (a modified, deleted or
hand-written cassette, anything added under `cassettes/eval-answers/`, `make eval-live`, `make enrich`) is the
advisor's reading of those words, adopted here.

**Residual.** The session briefs the advisor, so this approval catches mistakes, not a session steering its own
review. The user's named scope is the real bound. An added cassette's response cannot be verified either: it is
written in a session-writable tree, and the ledger holds no response hash. So that exception runs, with no plan
approval, over the open forged-added-cassette residual (`docs/governance/threat-model.md`, Residuals). The rule
is one the session keeps; no check proves it. `ship` still lists the paths and asks for the PR number.

## Speed

`make test` skips `test_ship.py`, `test_pre_push.py`, `test_refused_paths.py` and `test_finish.py` only when
`tools/`, `tests/ship/`, `.github/` and `Makefile` match the merge base with main. GitHub CI's plain
`uv run pytest` still runs them: `addopts` and `ci.yml` do not change. `test_guard.py` stays in `make test`.

`make ci` runs once in the chain, after the commit, so the SHA it ran at is the SHA reviewed. `make eval-gate` is
out of the chain because `make ci` holds it. `/req-done` keeps its own `make ci`, by the user's choice, so a row
costs two runs; its step 3 reads the gate result from step 1. `make check` is for during a row and is never
evidence.

## What the security review changed

`security-reviewer` read the row at `e9ae0c4`: one critical and one high, both in `tools/finish.sh`.

- **Critical, confirmed by running it.** A gitlink in the index makes `git status` run a child git in that folder,
  with that folder's config, so a planted `core.fsmonitor` runs outside the sandbox. The session can do all of it: the
  sandbox lets it write a nested `.git` file (measured 2026-11-07) and the index. `finish` now refuses a gitlink before
  anything reads the working tree, and runs `status --ignore-submodules=all` with `core.fsmonitor=false`.
  `tools/refused-paths.sh` and the pre-push hook refuse a committed gitlink, the hook once the user reinstalls it.
- **The same route is open for any `git status` outside the sandbox**, which includes the user's own terminal and may
  include an editor's git panel. The re-review rates this critical, and it needs the user's decision. A global
  `diff.ignoreSubmodules = all` narrows it but does not close it: a planted `.gitmodules` with `ignore = none` undoes
  that setting, even from the index alone (measured 2026-11-07). Only `--ignore-submodules` on the command line held.
  With no nested `.git` no child runs (measured). Refusing that write narrows the route but does not close it: see
  "Route B, after the user's answers".
- **Re-review finding A.** The index check alone was not enough: a gitlink at `HEAD` but not in the index passed it, and
  `git commit` with nothing to commit runs a status of its own, which descended into it (measured; the fsmonitor did
  not fire, because `core.fsmonitor=false` reached the child). `finish` now also checks `HEAD` and the reviewed commit,
  refuses a tracked `.gitmodules`, refuses a docs file changed both staged and unstaged, and commits with
  `--allow-empty`. This fix came after the one scoped re-review. The second re-review read it, and it holds.
- **High.** `CHECKOUT` could be any directory, and git trusts that directory. `finish` now takes only
  `~/Projects/northwind-shop`, or a linked worktree beside it whose `.git` file points into its worktrees folder.
- **Routine rows exclude hard rules 1, 6, 7, 8 and 9 by path.** The plan the user approved named only 1 and 9. The
  review rated that medium, and the user decided on 2026-11-07 to exclude 6, 7 and 8 too. That takes
  `services/assistant/**`, `services/rag/**`, `northwind_common/llm.py` and any later agent, MCP or tool-scoping code
  out of routine rows.
- **Stop 1 applies to every row until the `advisor-reviewer` proposal is installed**, because the installed copy
  cannot check that a row is routine.
- **`SHIP_FILTER` reads the local `origin/main`, which the session can write.** That weakens local evidence only:
  GitHub's `CI / python` still runs the ship tests, and `ship` requires that job. `make test` prints a line when it
  skips them.

## Route B, after the user's answers

The user decided on 2026-11-07: test a `denyWrite` glob for a nested `.git`, and add it to the settings proposal; deny
`./.claude/agents` and `./.claude/skills` in the same proposal; make `make runner-check` report any gitlink, `.gitmodules`
or nested `.git`; and allow one second `security-reviewer` read, an exception to the one re-review rule, covering the
fix to finding A and these changes.

- **The rule.** `~/Projects/northwind-shop/*/**/.git` and `~/Projects/northwind-shop-*/*/**/.git`. The `*/` keeps a
  checkout's own `.git` out, because a commit needs it. Not measured live: `sandbox-exec` cannot nest in a session, and
  the session cannot install settings. What is known: Claude Code 2.1.318 turns a glob into a Seatbelt `regex` deny
  (read from the installed binary), and the installed rule of the same class, `worktrees/*/gitdir`, refused a new
  file, the same name in another case, a rename onto the name, a symlink and a hard link (measured). The docs are
  silent on all of this, and the runtime's README says Linux skips such globs, so this is macOS only.
- **It narrows the route; it does not close it.** The same probe moved in a folder already holding the name, and the
  sandbox allowed it. So a folder built in `$TMPDIR`, where `.git` must stay writable for the test rigs, can be moved
  into a checkout. `make runner-check` reports that case; nothing refuses it.
- **Detection.** `make runner-check` reports a gitlink or `.gitmodules` in the index or at `HEAD`, and a nested `.git` or
  `.gitmodules` on disk in any case, ignored folders included, in every checkout. It uses no command that reads the
  working tree, and ignores replace refs.
- **Reviewers and skills.** The sandbox covers Bash and the file tools follow permission rules, so the proposal has both
  `denyWrite` entries and `Edit` deny rules. The `Write` twins follow the file's shape and do nothing: 5a found `Write(...)`
  is never matched. Once installed, changes to these folders are proposals the user installs. The user also added
  `.claude/commands`, `.claude/rules`, `.claude/output-styles`, `.claude/workflows` and `.mcp.json`, with `denyWrite` and
  `Edit` deny rules. `CLAUDE.md` stays writable by design, and a root-only deny does not cover a nested `.claude`. In this row the session edited `.claude/agents/test-writer.md` directly, which the installed settings
  allowed. The user recorded that as found, and allowed it this once.

## A new route: replacing a worktree's admin folder

Found by the same probe, and not in the user's answers. The rules on `worktrees/*/gitdir`, `commondir` and
`config.worktree` protect those names, not the folder holding them. The sandbox allowed a folder holding `gitdir` to be
moved into and back out of `.git/worktrees` (measured with a probe folder, never a real worktree). In a throwaway repo,
moving a linked worktree's admin folder out, rewriting `commondir` to a planted common directory and moving it back made
a plain `git status` in that worktree run the planted config's `core.fsmonitor` (measured). No gitlink is involved.
Decision 0006 did not cover this. Each half is measured; the whole chain has never run on a real admin folder, and the
runtime may already refuse moving a folder that existed when its profile was built (read from the binary, not measured).

## The second re-review, at `b1763dd`

One read, by the user's exception. Critical, provisionally, for the admin folder: code outside the sandbox can read the
key file, and `finish` was exposed. Route B stays critical. So stop 10 applies to the row. Finding A's fix holds.

Fixed after it, with no further read:

- **`finish` no longer trusts the admin folder.** Measured first: a planted hook ran through its flags. Before its first
  git call it requires `commondir` to read `../..` and `gitdir` to name this checkout, refuses a symlink for either or for
  the folder, and refuses a `config.worktree` or a main `commondir`. It then sets `GIT_DIR`, `GIT_COMMON_DIR` and
  `GIT_WORK_TREE`, and with those set a redirected `commondir` ran nothing (measured), so a swap after the check does
  not help. This protects `finish` only. **A plain `git status` from a terminal stays exposed.**
- **The `.gitmodules` refusal.** `| grep -q` under `pipefail` missed the file 5 times in 5 at size (measured), and now goes
  through a file.
- **`make runner-check` reads the admin folders.** It reads `commondir`, `gitdir` and `config.worktree`, and walks
  `.git/worktrees` itself, because `git worktree list` skips one with no `gitdir`.
- **A main checkout's `.git/commondir`.** Git honours one (measured), which the reviewer rated high, unconfirmed. The
  sandbox already refuses creating it (measured with `runner-check`'s own probe), through a list the runtime builds. The
  proposal now names it and `runner-check` measures it, so that is not left to the runtime.
- **The file tools.** `Edit` deny rules for a nested `.git`, `.git/worktrees` and `.git/commondir`: they never meet the
  sandbox. Not run.

## Moved to SEC-1, by the user's decision

On 2026-11-07 the user moved route B and the admin-folder replacement out of AUTO-2, into a new row, SEC-1, which lands
before #8. Both criticals are pre-existing, not caused or worsened by AUTO-2, and `make finish` refuses both. SEC-1 also
takes the `.git/commondir` high, the trusted read-only check from the runner clone, the rig that settles the rating, the
VS Code git panel question and the `make runner-check` medium. Stop 10 does not apply to the two moved criticals. It
still applies to anything else high or critical.

## The third read, at `7ab271a`

One read of `tools/finish.sh`, `runner_check.py` and the fixes after the second read, by the user's exception. No high or
critical. What it changed:

- `make runner-check` no longer takes the expected checkouts from `git worktree list`, because git builds that from the
  same `gitdir` files. It looks for `<main>-*` beside the main checkout, and requires each `gitdir` to name a linked
  checkout whose denied `.git` pointer points back. It compares values as git reads them, trimming line ends only.
- Neither `finish` nor `runner-check` reads an admin file that is not a plain file: a FIFO would block for ever.
- `finish` refuses a symlinked main checkout, and a `grep` failure is no longer read as no match.
- The pre-push hook clears `GIT_COMMON_DIR` as well. The reader's worry that `git init` rewrites the main config did not
  hold: the config stayed byte-identical (measured), and the real hook now runs under the pin in a test.

## `tools/finish.sh`'s first pass through `ship`

`make ship` and `runner-sync` run from the clone's current `Makefile`, whose `RUNNER_SYNC_PATHS` gains `tools/finish.sh`
only when this row merges. So on this one PR they list the file and its `--stat` but show no diff, although the row put it
on that list because it runs with the user's GitHub login. The user reads it in full before answering `ship`. From the next
PR on, it is shown like the rest.

A note on item 4: the user said a routine row may change `cassettes/**` through `make record`. `CLAUDE.md` allows only
added files, which is narrower. That is the advisor's reading, adopted because a modified cassette is a hand edit by
another name.

