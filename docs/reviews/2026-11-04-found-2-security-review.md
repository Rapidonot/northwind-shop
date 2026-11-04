# FOUND-2 security review, triaged (2026-11-04)

**Scope:** the PR #4 merge, `git diff c01cb2e^1 c01cb2e` (48 files, +4,412/-18).
**Reviewer:** the `security-reviewer` subagent, in a fresh `claude -p --agent security-reviewer`
process on `claude-opus-5-5`, with only Read, Grep and Glob.
**Triage:** the main session. The reviewer executed nothing, so each finding was checked against
the code, and by an offline probe where noted. Probes used a scratch ledger and a sandbox test
card; none spent credit or touched a key.

**Result:** 15 findings and 2 unconfirmed notes, triaged as 19 rows because finding 15 splits into
three: 16 accepted, 3 rejected. Only 15a is fixed on this branch; the rest are follow-ups. The
reviewer found no committed secrets, and the default key path held. The weak area is the spend
guard (findings 1–5).

This counts toward CC-28's triage practice (a recorded accept or reject reason), but CC-28 names the
bundled `/security-review` and `/code-review` skills, so its status does not change.

## Fix before these milestones

| Before | Findings | Why |
|--------|----------|-----|
| The first `make record` (RAG-12, #4) | 1, 2, 3 | They decide whether the caps and replay default actually hold |
| SA-02 crude (#5) | 8 | The first agent with retrieved text in context must not get default tools |
| RECORD-ALL (#18, Nov 20) | 10, 6 | 10 can refuse recordings already paid for; 6 lets a split secret into a commit |

## Triage

Severity is the triage severity. It differs from the reviewer's on 5 (medium → low), 10 (low →
medium) and 15b (low → medium, because a redirect from an allowed `git` command can overwrite the
hook itself).

| # | Severity | Finding | Decision | Evidence and reason |
|---|----------|---------|----------|---------------------|
| 1 | High | Caps can be raised or disabled without asking | Accept | Probe: `budget_usd=nan` admitted a $1e9 reservation; the control refused $2 on a $1 cap. `Bash(make *)` is pre-approved, so `make record LLM_BUDGET_USD=…` raises no prompt. Fix: reject non-finite caps in code. **The `ask` rules are in `settings.json`, so only the user can add them** |
| 2 | Medium | `make test` and `make ci` do not pin replay | Accept | Read: neither target sets `LLM_MODE`; the plugin refuses non-replay only under `CI`. Decision 0001 says these targets run in replay. Fix: pin it in the Makefile |
| 3 | Medium | Unknown `LLM_MODE` goes live in the dev proxy | Accept | Read: `ProxyConfig` has no validation, and `proxy.py:256` sends anything other than replay or record upstream. Not run, because that would spend. The ledger's caps still apply. Fix: an allowlist |
| 4 | Medium | Failed upstream calls settle at $0 | Accept | Read: `_settle(reservation, {})` passes `{}`, not `None`, so `cost_usd` gives 0 rather than worst case. Small per call, but billed retries go uncounted. Fix: release at zero only for connect-phase errors |
| 5 | Low | Worst case is not an upper bound | Accept | Probe: `max_tokens=-100000` gave a reservation of -$0.9998 and took `spent_usd` from 0.90 to -0.10. Lowered to low: nothing here builds such a request, and URL sources and server tools are unused. Fix: refuse what the ledger cannot price |
| 6 | Medium | Scanner misses split secrets and non-16-digit grouping | Accept | Probe: a test card or key found whole gave 0 findings when split across two `text_delta` events; `3782 822463 10005` gave 0. Fix: also scan reassembled text, widen the grouped patterns |
| 7 | Medium | Raw bodies logged with no scan at write time | Accept | Read: `_log` writes the unscrubbed body in record and live modes. `.llm-proxy-log/` is gitignored, so this stays on the local disk. Fix: scan before forwarding and logging |
| 8 | Medium | `agent_options()` does not default to no tools | Accept | Read: with `tools=None` the SDK adds no `--tools` flag. Latent today, because callers pass `tools=[]`. Fix: make `tools` required |
| 9 | Low | `child_env(extra)` can overwrite the placeholder | Accept | Read: `env.update(extra)` runs after the filter, and no caller passes a key. Fix: apply the same filter to `extra` |
| 10 | Medium | Float costs trip the PAN check | Accept | Probe: 708 of 20,000 synthetic costs (3.5%) were flagged, such as `9.900000000000001e-05`. The sweep is not real traffic, so the live rate may differ. `put()` scans after the call is paid for. Raised to medium because of RECORD-ALL. Fix: store costs at fixed precision; leave the scanner alone |
| 11 | Low | The proxy is not the only reader of the key | Accept (docs) | Read: the scan CLI loads it for fragment matching, and under `make record` the proxy shares the pytest process. It never reaches `os.environ` and is never printed. Fix: correct the wording in decision 0001 |
| 12 | Low | `make up` needs a key file in linked worktrees | Accept | Tested: compose exits 1 with "couldn't find env file" when `--env-file` names a missing file, and `COMPOSE` hardcodes it. The obvious workaround is copying the key file, which decision 0001 forbids. Fix before CC-17 |
| 13 | Low | `make ci` skips the cassette scan GitHub CI runs | Accept | Read: `ci: lint test test-integration`, while `ci.yml` has a scan step. CLAUDE.md says they match. Fix: add `scan-cassettes` to `ci` |
| 14 | Low | "Never hand-edit a cassette" is not enforced | Reject | A checksum stored in the same file stops nothing, because whoever edits the file can recompute it. Git history and PR review are the control, and CLAUDE.md states the rule |
| 15a | – | Guard gaps in the c01cb2e version | Accept (fixed) | `.ENV`, `printenv <name>`, `git status; rm …` and exit-1 crashes were fixed in 7fc8e93, which reaches main with this PR. Pinned as blocked in `tests/hooks/test_guard_env.py` |
| 15b | Medium | Guard gaps still open | Accept (11 of 14 fixed, rest accepted) | The user installed the hook fix on `req/NFR-02-guard-fixes`: 11 of the 14 cases are now pinned as blocked. The other 3 stay `xfail(strict=True)`, and 4 more shell-text gaps are accepted as limits of a speed bump (list below). The real control is CC-27 sandboxed Bash, a follow-up after RAG-01. Review of this change found further bypasses, R1 to R4, still open |
| 15c | Low | `uv run` Python can read the key file under a constructed name | Reject | Correct, but no text match can stop it; the hook says it is a speed bump. A real fix means an OS sandbox, not a hook change |
| A | Low | The proxy client may honour `HTTPS_PROXY` | Accept | Confirmed: `trust_env` defaults to True in `httpx2`. Fix: set `trust_env=False` on the upstream client |
| B | Low | Dev proxy checks no `Host` header | Reject for now | Untested. It binds to loopback, needs live mode, and the caps still apply. Revisit in P6 hardening |

## Guard gaps (15b)

The original 14 cases, after the hook fix on `req/NFR-02-guard-fixes`:

| Group | Cases | Status |
|-------|-------|--------|
| Redirect or flag from an allowed `git` command | `git diff > .claude/settings.json`; `git show HEAD~1:.claude/hooks/guard_env.py > .claude/hooks/guard_env.py`; `git diff --output=.claude/settings.json` | Fixed, pinned as blocked |
| Protected path split or in another case | `cd .claude && rm settings.json`; `rm .CLAUDE/settings.json`; Edit of `/repo/.CLAUDE/settings.json` | Fixed, pinned as blocked |
| Environment dump not on its own | `env > dump.txt`; `set > dump.txt`; `echo $(env)`; `(env)`; `/usr/bin/env` | Fixed, pinned as blocked |
| Key read without shell expansion | `python3 -c` printing `os.environ["ANTHROPIC_API_KEY"]` | Accepted, strict xfail |
| Name hidden from the text match | `cat .en*`; `cat .e''nv` | Accepted, strict xfail |

Four more shell-text gaps, each confirmed by passing the command to the installed hook as data.
They are accepted but not pinned in the test suite:

| Case | Why it gets through |
|------|---------------------|
| `rm -rf .claude` | Removes the parent directory, so no protected path appears in the text |
| `git -C .claude checkout -- settings.json` | The bare name is only protected after `cd`; `git -C .claude/hooks …` is blocked |
| `env -0` | `env` with a flag reads as an ordinary command |
| `` echo `env` `` | Backticks are not parsed; `$(env)` is blocked, and `printenv` is blocked anywhere |

A text match cannot close these without parsing the shell, so the hook stays a speed bump. The
real control is CC-27 sandboxed Bash with writes denied to `.claude/settings*` and `.claude/hooks/`,
a follow-up after RAG-01. `env -0` and `` `env` `` read the environment rather than write, so that
deny list does not cover them.

`tests/hooks/test_settings.py` guards the other half: the hook only runs while `settings.json`
registers it, so a truncated or weakened file now fails CI.

## Review of the guard change (open, for the user to decide)

The `security-reviewer` subagent read the hook change and `test_settings.py`. It found no
regressions: every old block condition still blocks. It did find bypasses beyond the four above.
All passed the old hook too. Each example below was confirmed by passing it to the installed hook
as data, and none is covered by the acceptance of those four.

| Finding | Severity | Example that gets through | Why |
|---------|----------|---------------------------|-----|
| R1 | Medium | `git status $(rm .claude/settings.json)`; `` git log `cp /tmp/weak.json .claude/settings.json` ``; `git commit -m "$(rm .claude/hooks/guard_env.py)"`; `git diff > >(tee .claude/settings.json)` | An allowed `git` segment is reduced to its redirect targets, so a substitution inside it is not inspected |
| R2 | Medium | `git diff " -m '" > .claude/settings.json "'"`; `git log --grep=" -m 'x" > .claude/settings.json # '` | `GIT_MESSAGE` ignores quoting, so a real redirect is stripped as message text. New code; it would empty the file, as in the incident |
| R3 | Low | `git diff > .claude/./settings.json`; `rm .claude//hooks/guard_env.py`; Write to `/repo/.claude/./settings.json` | Protected paths are matched as literal strings |
| R4 | Low | `cd .claude/agents && rm ../settings.json`; `{ cd .claude; rm settings.json; }`; `if cd .claude; then rm settings.json; fi` | `cd` tracking needs `cd` at the start of a segment and a path ending in `.claude` |

R1 to R4 change the hook, so they are the user's call. CC-27 would stop all of them, because they
are writes. A speed bump may be enough until then, but R1 and R2 are the ones to look at: they
write to a protected file through a command the hook treats as safe.

Also from this review:

- Fixed: `test_settings.py` first matched the guard path as a substring, so `<guard> || true`
  passed. It now compares the whole hook entry, with weakened copies pinning it.
- Already open: `make -s record` and `make SUITE=x record` miss the `ask` rules. Finding 1 in
  PROGRESS, to decide before the first `make record` (#4). Read from the rule text, not run.
- Noted, not changed: `GUARD_HOOK_PATH` can point the guard suite at another copy of the hook.
  Nothing sets it, including CI. `Bash(git push*)` is not pinned by `test_settings.py`.
