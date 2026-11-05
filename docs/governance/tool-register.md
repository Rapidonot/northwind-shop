# Tool register (NFR-09)

Every tool and state-changing command, today and planned, with its risk tier, limit, approver and where
that is enforced. PRD §8 NFR-09 requires monetary tools to have a deterministic limit and a confirmation
or approval.

**Baseline:** main at `f50f7c8`, 2026-11-05. **Tiers:** `read-only`, `reversible write`, `irreversible or
monetary`. "The user" is the person at their own terminal. A session cannot push, use `gh` or run Docker
(decision 0004).

**The only tool a product agent holds today is `retrieve_context`.** No product agent can reach a payment,
refund or order tool on main. Claude Code sessions are different: a session can run `make record` and
`make eval-live`, `make llm-proxy` too, and any sandboxed command can claim a window the user opened
(decision 0005, "Limits"). The file tools can reach both Edit rows at a prompt, and a sandboxed `git commit`
changed the committed baseline in rig run 2 (decision 0006, "Open limits"). So a monetary tier here does not
mean a session cannot reach the row. Read each row's Enforced column.

## Agent tools today

| Tool | Tier | Limit | Approver | Enforced at |
|---|---|---|---|---|
| `retrieve_context` | `read-only` | 3 searches a turn including the server's own (`MAX_SEARCHES`), 4 turns (`MAX_TURNS`), 5 chunks, query only; audience and region are bound on the server | None needed: read-only | `services/assistant/src/northwind_assistant/tools.py`, `services/assistant/src/northwind_assistant/turn.py`; `services/assistant/tests/test_first_request.py::test_the_cap_is_three_searches`, `services/assistant/tests/test_tool_loop.py::test_a_tool_outside_allowed_tools_is_denied_not_just_absent`, `services/assistant/tests/test_first_request.py::test_region_and_audience_are_not_in_the_tool_schema` |

Built-in tools are off (`tools=[]`, `services/assistant/tests/test_tool_loop.py::test_a_built_in_tool_cannot_run`).
Evidence for the in-process server: `PROGRESS.md` row 5. It moves to `mcp-rag` at #10.

## Spending commands today

The window is the real gate. The `ask` rules are a courtesy, because other command forms reach the same
code without one (decision 0005).

| Command | Tier | Limit | Approver | Enforced at |
|---|---|---|---|---|
| `make llm-runner` | `irreversible or monetary` | `RUN_BUDGET` required, no default; `LLM_BUDGET_USD` defaults to 90 in total; defaults of 30 minutes and 10 idle; one suite; one claimed run; two models; 4 requests in flight. The defaults are set from the user's terminal and have no upper limit in code; an overrun is at most one call per request in flight (decision 0005, "Limits") | The user, by opening it from the runner clone | `services/llm-replay/src/northwind_llm_replay/runner.py`, `services/llm-replay/src/northwind_llm_replay/ledger.py`; `services/llm-replay/tests/test_runner_criteria.py::test_cli_runner_has_no_default_run_budget`, `services/llm-replay/tests/test_ledger.py::test_budget_breach_is_refused_before_any_call` |
| `make record` | `irreversible or monetary` | The open window's limits; one suite; `SUITE` checked | The user, through the window; plus an `ask` rule | `Makefile`, runner; `services/llm-replay/tests/test_runner.py::test_no_window_means_no_spend_and_the_key_is_never_loaded` |
| `make eval-live` | `irreversible or monetary` | The open window's limits; `EVAL_SLICE` required, digits only | The user, through the window; plus an `ask` rule | `Makefile`, runner; `services/llm-replay/tests/test_runner_criteria.py::test_eval_run_with_no_window_returns_2_spends_nothing_and_writes_no_candidate` |
| `make llm-proxy` | `irreversible or monetary` | Replay by default; otherwise the open window's limits | The user, through the window; plus an `ask` rule | `services/llm-replay/src/northwind_llm_replay/cli.py`; `services/llm-replay/tests/test_runner_criteria.py::test_serve_in_live_mode_with_no_window_exits_2_too` |

## Other state-changing commands today

| Command | Tier | Limit | Approver | Enforced at |
|---|---|---|---|---|
| `make eval-promote` | `irreversible or monetary` | **Nobody runs it until row 5b lands.** A regression needs a reason and `APPROVED_BY`; containment failures, dropped items and lost metrics cannot be overridden | The user, after 5b, from the runner clone | Policy until 5b: decision 0006, rule 5, and `docs/reviews/2026-11-04-5a-gate-hardening-evidence.md` second-pass finding 13. `ask` rules and `denyWrite` in `tools/settings.proposed.json` |
| `make ship` | `irreversible or monetary` | One PR; only `PROGRESS.md` and `COVERAGE.md` may change after the reviewed SHA, and their diff is not shown; all CI checks pass; labels for gated paths, not bound to a head; refused if a window is open when it starts, with no recheck before the merge. `SHA` comes from the session's gate report | The user: `y`, or the PR number when a listed control surface path changed. `.github/**` outside workflows, `pnpm-lock.yaml`, `pnpm-workspace.yaml` and `evals/contexts/` get a plain `y` until AUTO-2 | `tools/ship.sh`, `tools/refused-paths.sh`, `Makefile`; `tests/ship/test_ship.py`. No recorded run on real `gh` (decision 0008, "Limits") |
| `make runner-sync` | `irreversible or monetary` | Fast-forward to `origin/main` only; refused paths; refused while a window is open. The prompt does not drain type-ahead (AUTO-2) | The user, shown the diff of what loads with the key | `Makefile`, `tools/refused-paths.sh`, decision 0005 |
| `git push` | `irreversible or monetary` | No session credentials. Pre-push refuses `.github/**`, `GNUmakefile` and `makefile` variants, `.env*`, `.venv*`, ignored paths and non-ASCII paths unless `NW_ALLOW_CONTROL=1` or `--no-verify` is used. The rest of the control surface passes it, `Makefile` included | The user | Keychain denied (decision 0004), `tools/pre-push.proposed.sh`. A local check, not a boundary (decision 0008, "Limits") |
| Edit `evals/baselines/` | `irreversible or monetary` | Same effect as `make eval-promote`, so the same tier. Bash writes refused; file tools prompt. Committed content is not protected until 5b | The user; `baseline-change` label in `ship` | `tools/settings.proposed.json`, `tools/ship.sh`, decision 0006 "Open limits". CI check: 5b |
| Edit `evals/fixtures/known_failures.json` | `irreversible or monetary` | "Can only shrink" is policy, not a check: an added entry reading `approved_by: human` waives a containment failure, and `make eval-integrity` only prints it (decision 0006, rule 3). `approved_by` is a claim. Bash writes refused; file tools prompt | The user; `baseline-change` label in `ship` | `evals/tests/test_harness.py::test_an_unapproved_or_incomplete_known_failure_fails` shows only that a model-labelled entry waives nothing. Decisions 0003 and 0006. CI check: 5b |
| Mark an eval item `reviewed_by: human` | `reversible write` | Claude only ever writes `model`. Policy: labels can change under a `human` stamp (`PROGRESS.md` row 4, third-pass F9) | The user | `CLAUDE.md`, `PROGRESS.md` "Eval review rule" |
| `make ingest` | `reversible write` | Idempotent; aborts on a planted price or a missing visibility, with nothing written | None: local, rebuilt from fixtures | `services/rag/tests/test_index.py::test_planted_price_aborts_the_run_and_writes_nothing`, `services/rag/tests/test_index.py::test_missing_visibility_aborts_the_run_with_nothing_written` |
| `make data` | `reversible write` | Seeded and byte-identical; changed output needs a `DATA_VERSION` bump | None: reviewed in the PR | `data/tests/test_determinism.py`, `PROGRESS.md` row 1 |
| `make eval-snapshot`, `make eval-rescore`, `make eval-review-list` | `reversible write` | Local files only, never the baseline; `make eval-rescore` never spends | None: reviewed in the PR | `evals/src/northwind_evals/cli.py`, decision 0003 |
| `make eval-gate-stale-ok` | `reversible write` | Runs `make ingest`. Never waives a stale adversarial or unanswerable item; CI never runs it. The `ask` rule is a courtesy: other forms of the command match no rule | `unconfirmed`: the rule prompts the user, and no decision names an approver | Decision 0006, rules 1 and 6 |
| `make eval-gate`, `make e2e`, `make e2e-scripted` | `reversible write` | Run `make ingest` first; replay only | None: local | `Makefile`, decision 0001 |
| `make test-integration`, `make ci` | `reversible write` | Replay only; integration tests write to Postgres | None: local | `Makefile` |
| `make fmt`, `make setup`, `make review-diff` | `reversible write` | Working tree, dependencies, or one temp file. Run `make setup` sandboxed: it runs lifecycle scripts, so from a terminal it has the F1 shape | None: local | `Makefile`, decision 0005 |
| `POST /_runner/claim` | `irreversible or monetary` | The open window's limits. Any sandboxed command can claim first; the first claim gets the token | The user, by opening the window | `services/llm-replay/src/northwind_llm_replay/runner.py`, decision 0005 |
| `tools/rig-5a-ask-rules.sh` | `irreversible or monetary` | Run in the user's terminal, and a session can edit it. The advisor reads it from disk and the user checks the hash just before running | The user | `docs/reviews/2026-11-04-5a-gate-hardening-evidence.md`, second-pass finding 2 |
| `services/llm-replay/spike/run_spike.py` | `irreversible or monetary` | Not wired to any target. In the sandbox it can neither read the key nor reach the host | The user | Decision 0005, "Limits" |
| `make up`, `make down`, `make model` | `reversible write` | Run from the runner clone; `make down` keeps the data volume | The user; a session cannot reach Docker | `Makefile`, decisions 0004 and 0005 |
| `make help`, `make dev`, `make dev-gateway`, `make dev-portal`, `make lint`, `make test`, `make scan-cassettes`, `make llm-spend`, `make eval-integrity`, `make runner-check` | `read-only` | Write nothing of lasting note: temp files, caches, reports. `make test` pins replay | None needed: read-only | `Makefile` |

## In flight

| Tool | Row | Tier | Limit | Approver | Enforced at |
|---|---|---|---|---|---|
| `make enrich` | #7, `req/RAG-02-enrichment`, not on main | `irreversible or monetary` | The open window's limits, `SUITE=rag-enrichment`; chunks already cached make no call | The user, through the window. **No `ask` rule:** the branch does not change `tools/settings.proposed.json`. The window is still the gate | `req/RAG-02-enrichment:Makefile`, the enrichment decision on the branch |
| `make ingest-enriched`, `make eval-enrichment-report` | #7, not on main | `reversible write` | A separate database, allow-listed by name; no LLM | None: local | `req/RAG-02-enrichment:Makefile`, the enrichment decision on the branch |
| Promotion entry point in the runner clone; `baseline-change` CI check | 5b, `req/5b-promotion-control` at `b645576`, not on main | `irreversible or monetary` | As `make eval-promote`, plus the label | The user | `PROGRESS.md` row 5b, `req/5b-promotion-control:services/llm-replay/src/northwind_llm_replay/promotion.py`, `req/5b-promotion-control:.github/workflows/guard.yml` |

## Planned (PRD §7)

Tier is from the PRD where it states one, otherwise read from what the tool does. **Limit `unconfirmed`
means the PRD names a limit but gives no value yet.** Enforcement is the PRD's design; none of it exists.

| Tool | Row | Tier | Limit | Approver | Enforced at (planned) |
|---|---|---|---|---|---|
| `search_products`, `get_product`, `get_variant`, `check_stock`, `get_price` | #10, `mcp-catalog` | `read-only` | Internal rows filtered for shoppers | None needed: read-only | PRD §7, `PROGRESS.md` row 10 |
| `retrieve_context` on `mcp-rag`; index manifest resource | #10 | `read-only` | Keeps today's audience, region and card checks | None needed: read-only | `PROGRESS.md` row 10 |
| `get_order`, `list_orders` | #19, #27 | `read-only` | Own orders only, per-user token | None needed: read-only | PRD §7 |
| `evaluate_return_eligibility` | #19, #27 | `read-only` | Read-only policy check | None needed: read-only | PRD §7 |
| Cart tools | #16, CHK-01 | `reversible write` | Live stock check; idempotency key (hard rule 9) | The shopper | PRD CHK-01 |
| `create_order` | #21, CHK-05; confirmation is CHK-02 and limits CHK-03, both Window B | `irreversible or monetary` | Idempotency key required; spend and velocity limits, values `unconfirmed` | The shopper, through a portal control, never free text | PRD CHK-02, CHK-03, CHK-05 |
| `update_delivery_address` | No row names it; closest is #21, `unconfirmed` | `reversible write` | Idempotency key | `unconfirmed` | PRD §7 |
| `cancel_order` | No row names it; closest is #21, `unconfirmed` | `irreversible or monetary` | Idempotency key | `unconfirmed` | PRD §7 |
| Stock reservation | #25, BE-04 | `reversible write` | Must never oversell | None: deterministic | PRD BE-04 |
| `create_case`, `add_note` | #19, #26 | `reversible write` | Care agent only | Staff act on the case | PRD §7 |
| `assign_case` | #19, #26 | `reversible write` | Care agent only; never the assistant | Staff | PRD §7 |
| Catalog write tools; `northwind-rag` index refresh | #29, BE-03 | `reversible write` | Unreviewed changes never publish | Merchandiser | PRD BE-03 |
| Refund through the payment port | #28 on fakes; PAY-06, Window B | `irreversible or monetary` | Configured cap, value `unconfirmed`; a hook blocks anything above it | Human approval above the cap | PRD PAY-06 |
| Payment authorize, capture, void | #19 interface; PAY-01 to PAY-04, Window B | `irreversible or monetary` | Idempotency key; tokens only; sandbox keys | The shopper, through a portal control | PRD §7, PAY-01, PAY-05 |
| Payment Processing Agent (A2A) | Window B, A2A-04, PAY-03 | `irreversible or monetary` | As above; mutual TLS plus scoped token | The shopper; step-up when required | PRD §7 |
| Fraud Risk Agent (A2A) | Window B, A2A-02 | `read-only` | Scoped token | None needed: read-only | PRD §7 |
| Carrier Agent (A2A): quote, track | Window B, A2A-03 | `read-only` | Scoped token | None needed: read-only | PRD §7 |
| Carrier Agent (A2A): create label | #28 on fakes; Window B, A2A-03 | `reversible write` | Scoped token | Returns agent, on eligibility | PRD §7 |
| Partner order through the assistant (A2A server) | #15 prep; Window B, A2A-05 | `irreversible or monetary` | Scoped OAuth; spend ceiling and order ceiling, values `unconfirmed`. Scope checks, not the model. Hard rule 9 wants confirmation through a portal control, and a partner has no portal: `unconfirmed` how that is met | `unconfirmed` | Decision 0002, PRD §7 |
| Build-time MCP servers | CC-09 | `reversible write` | Repository, database and browser-automation servers; only the database credentials are read-only. No project MCP config exists yet | The user | PRD §7 |

## Open items

| Item | Owner |
|---|---|
| The PRD gives no value for the refund cap or the spend, velocity, partner and order ceilings | Rows above, when built |
| Kill switches by agent, tool and payment route do not exist (NFR-12) | Window B, P6 |
| No audit log exists yet, so no tool here writes one (NFR-10, BE-08) | #20 |
| `make -s record` and `make SUITE=x record` miss the `ask` rules. Read from the rule text, not run. The window still holds | FOUND-2 review; no change planned |
| `make eval-promote` still works if someone runs it; the block is policy until 5b. Its input, `CANDIDATE` in `evals/src/northwind_evals/cli.py`, is a local file a session can write | 5b |
| No run of `ship` on real `gh` is recorded: `unconfirmed` whether PR #22 merged through it | `PROGRESS.md` row A1, partial for this reason |
| 12 `ship` and pre-push refusals have no test | AUTO-2, `docs/reviews/2026-11-05-auto-1-evidence.md` |
| `tools/settings.proposed.json` has no `ask` rule for `make enrich` | #7, control surface, not changed here |
