# FOUND-2 spike: Agent SDK through the record/replay proxy (2026-11-04)

Time-box: 1 day (decision 0001 §4). Driver: `spike/run_spike.py`; egress logger: `spike/egress.py`
(spike-only). Pinned: `claude-agent-sdk==0.2.178` (bundled CLI **2.1.318**), `anthropic==2.4.0`,
`httpx2==2.16.0`, `python-dotenv==1.2.4`. Raw request bodies: `.llm-proxy-log/spike.jsonl`
(gitignored, scanned).

Setup for every run: the CLI is started through the factory's `env -i` launcher
(`northwind_common.llm`), so its environment is exactly the allowlist (`PATH HOME LANG TMPDIR`) plus
the FOUND-2 variables. Also: `--bare`, a fixed custom `--system-prompt`, `setting_sources=[]`, no
tools, `max_turns=1`, a fresh `CLAUDE_CONFIG_DIR` and work dir per run,
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, telemetry/error reporting/autoupdater off,
`HTTPS_PROXY`/`HTTP_PROXY` = the egress logger, and `NO_PROXY=127.0.0.1,localhost`.

Method: the CLI sends its request before it sees any response. So identical-request comparison
and the leak search ran first in **replay mode against an empty suite** (every request a miss,
no key loaded, $0). Live calls were made only to see the full request list and costs.

## 1. Does the CLI honour `ANTHROPIC_BASE_URL`? Yes

- Every request reached the proxy, including with a path prefix on the base URL (`/_t/<test-id>`).
- **Egress logger: 0 connections in all 10 Agent SDK runs** (14 spike runs in total). Nothing tried to reach any other host
  (telemetry, update check, OAuth).
- **Auth: the placeholder key every time** (`x-api-key`, no `Authorization`). The proxy rejects
  anything else (hard pass/fail). `--bare` also makes the CLI take auth strictly from
  `ANTHROPIC_API_KEY` and never read OAuth or the keychain.
- Finding: `anthropic` 2.4.0 sends an API key as `Authorization: Bearer <key>`, not `x-api-key`.
  The first direct-client calls were therefore refused by the original "no Authorization header"
  rule. The rule is now: exactly one credential, and it is the placeholder (`x-api-key: <p>` or
  `Authorization: Bearer <p>`). Anything else is still refused.

## 2. Every request one query makes

| Client | Requests per query | Method + path | stream | `max_tokens` sent |
|---|---|---|---|---|
| Agent SDK `query()` | **1** | `POST /v1/messages?beta=true` | yes (SSE) | **128000** by default (CLI default); `CLAUDE_CODE_MAX_OUTPUT_TOKENS` overrides it (1024 in the spike) |
| direct `anthropic` client | 1 | `POST /v1/messages` | no | as passed (16) |

No count_tokens, model list, quota probe, title or topic call, and nothing after the response.
The CLI request carries these betas:
`interleaved-thinking-2025-05-14, context-management-2025-06-27, claude-code-20250219,
per-turn-control-2026-07-01, thinking-token-count-2026-05-13, effort-2025-11-24,
thinking-display-updates-2026-08-18`. The body has `thinking: {type: adaptive, display: updates}`,
`output_config.effort: medium`, `context_management` (`clear_thinking`), a system block
`x-anthropic-billing-header: cc_version=2.1.318.f3f; cc_entrypoint=sdk-py;`, a fixed SDK
preamble, our system prompt, and `cache_control` on system and user blocks.

Replay caveat: on a 400 (a miss) the CLI **retries twice with fewer betas** (drops
`thinking-token-count-…`, then `thinking-display-updates-…` and `thinking.display`). So one miss
shows up as 3 misses; the first one is the real request. Live calls with a 200 show only 1 request.

## 3. Leak search over raw bodies (all runs): no hits

Searched for: `2026-11-04`, `11/4/2026`, `11/04/2026`, `November 4`, `Nov 4`, `Wednesday`,
`2026`, `darwin`, `Darwin`, `macOS`, `arm64`, `aarch64`, the OS release, the username, the home
dir, `northwind-shop`, `/var/folders`, `/private/tmp`, `nw-agent-`, `scratchpad`. **None appears in
any request body.** With `--bare` and a custom system prompt, the CLI adds no env/date/cwd/git
sections. (Platform does appear in non-matched headers such as `x-stainless-os`; those are not
stored in cassettes or hashed.)

## 4. Identical runs → identical scrubbed hashes? Yes

Raw diff of the first request across r1, r2, r3 (identical) and r4 (different runtime root, so a
different cwd and config dir):

| JSON path | changes | rule |
|---|---|---|
| `metadata.user_id` | yes: a JSON string with `device_id` (new per config dir), `account_uuid` (empty), `session_id` (UUID per run) | whole value → `<scrubbed:metadata.user_id>` |

Nothing else differed in the body or matched headers. The `cc_version=2.1.318.f3f` suffix stayed
the same with a different prompt (r5), so it is not content-derived and needs no rule. This is the
only rule in `scrub.SCRUB_RULES`.

After the rule: the recorded Haiku run, then **2 identical replay runs, both cassette hits with no
key loaded**, both returning `ready`.

**Linux.** The macOS-recorded cassettes replay on Linux x86_64. On day 1 this was shown in a
Debian container (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, `CI=true`, `LLM_MODE=replay`,
no key file; only git-tracked and new files copied in): all 4 `test_spike_replay` cases pass with
the Linux build of the bundled CLI, and the whole FOUND-2 suite passes (155 tests). GitHub CI on
the day-1 push stopped earlier, at `ruff format --check` on the newly added
`.claude/hooks/guard_env.py`, a file Claude may not edit. Once that was formatted, GitHub CI
confirmed it on ubuntu-latest at the PR #4 merge (c01cb2e6): 155 passed, including all 4
`test_spike_replay` cases, and 1 integration test.
https://github.com/Rapidonot/northwind-shop/actions/runs/41757235613

## 5. `max_tokens` and budget sizing

- The CLI sends **128000** unless told otherwise. At that value the worst-case reservation for one
  Sonnet 5.5 call is ≥ $1.28 (128000 × $10/MTok), more than the whole spike cap.
- The factory therefore always sets `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, default
  `LLM_MAX_OUTPUT_TOKENS=8192`.
- Worst case per Sonnet call at 8192 = 8192 × $10/MTok = **$0.082 output**, plus input at the
  dearest input rate ($4/MTok, 1h cache write) on the proxy's estimate (body bytes / 2). An
  assistant turn with ~15K real tokens of prompt (≈60 KB body → 30K estimated) adds $0.12. So
  ≈ **$0.20 per call**.
- `LLM_RUN_BUDGET_USD` default **5.00** = a 20-call slice at that worst case (20 × $0.20 = $4.00),
  rounded up. `make record` uses it; larger suites pass a higher value explicitly. Real spend
  settles far lower (the spike's Sonnet call cost $0.0002).

## 6. Spend (ledger, suite `spike`)

| Call | Model | Cost |
|---|---|---|
| Agent SDK, record | Haiku 5.5 | $0.000010 |
| Agent SDK, record | Sonnet 5.5 | $0.000200 |
| direct client, record | Haiku 5.5 | $0.0000073 |
| direct client, record | Sonnet 5.5 | $0.000146 |
| **Total** | | **$0.000363** (cap $1.00, proxy-enforced) |

The CLI's own `total_cost_usd` agreed with the ledger for both agent runs. The spike suite is
excluded from the 20-call checkpoint.

## Gate (decision 0001 §4): proxy path

Requests are stable on macOS and Linux, so every LLM call (Agent SDK and direct clients) goes
through the proxy. Message-stream recording is not needed. Spike time: well inside day 1.
