# SA-06 phase B: security-reviewer's report, verbatim

Diff: `$TMPDIR/northwind-sec-SA-06-phaseB.diff`, base `06713f1` (the CHK-07 merge), head `4821fc8`. Saved as returned.

---

## Security review: row #23 (SA-06) phase B

**Diff:** /tmp/claude-502/northwind-sec-SA-06-phaseB.diff, base `06713f14d4579ab0296292653f9ab93c04e84f57`, head `4821fc8656174be2231ffd8c8c4f0df6c2ed1def`. I read the diff and these files in full: `services/gateway/src/northwind_gateway/chat.py`, `handoff_trigger.py`, `handoff.py`, `services/common/src/northwind_common/tool_scopes.py`, `apps/portal/src/Chat.tsx`, `apps/portal/src/chatStream.ts`, `apps/portal/src/HandoffNotice.tsx`, `apps/portal/e2e/handoff.e2e.ts`, both new gateway test files, and the parts of the backend desk that the governance rows cite.

**No critical, high or medium findings.** There are three lows. One is a false sentence in governance, one is about defence in depth, and one is unconfirmed.

### Findings

**L1 (low): `docs/governance/data-flow.md:28` says something that is not true.** Hard rules 1 and 6 (accuracy of the data-flow record).
- **What is wrong:** the new row says "The message that triggered a handoff in `/api/chat` reaches no model". It does reach one. `Chat.tsx:191` stores the shopper's message with `remember()`, and `Chat.tsx:195` then calls `startHandoff("message")`. So the message is the last turn of the `POST /api/handoff` transcript, and that transcript goes to the Care agent (Haiku). `apps/portal/src/Handoff.tw.test.tsx:202-210` pins exactly this ("includes the message that asked for a person in the handoff's turns").
- **How it fails:** the cited test, `test_a_request_for_a_person_is_handed_off_with_no_search_and_no_model_call`, only proves the `/api/chat` leg. Someone reading the data-flow record to decide whether triggered messages ever reach a model, or land in a `backend-care` cassette, would get the wrong answer. In phase C's recording, that message will be in the cassette and in `.llm-proxy-log`.
- **Fix:** change it to: "it reaches neither the search nor the assistant's model in `/api/chat`; it reaches the Care agent only as a turn of the handoff transcript, screened as above."
- New in phase B.

**L2 (low): `services/common/src/northwind_common/tool_scopes.py:103-107`. The fragment list misses one of PRD §7's case-write tools.** Hard rule 8.
- **What is wrong:** `"case"` blocks `create_case`, `assign_case` and similar names. It does not block `add_note`, the other case-write tool PRD §7 gives the Care subagent. It also does not block `handoff`, `escalat` or `ticket`.
- **Mitigations already in place:**
  - The SA-06 pin `services/assistant/tests/test_sa06_no_case_tool.py:27` carries a wider list (`case`, `handoff`, `escalat`, `assign`, `ticket`, `staff`), but it has no `note` either.
  - `services/common/tests/test_tool_scopes.py:15` requires the assistant's tool set to equal a fixed set, so an `add_note` grant to `shopper-assistant` would still fail CI.
  - `shopper-portal` and `shopper-query` have no equality pin, but they are never models.
- **How it fails:** a later row grants `add_note` (or `escalate_to_care`) to `shopper-query` or `shopper-portal`. Neither the scope table's fragments nor the registry check in `tests/mcp/test_cc10_contract_errors.py:194-207` (which uses only `FORBIDDEN_FRAGMENTS`) catches it.
- **Fix:** add `"note"` (or `"add_note"`), `"handoff"` and `"escalat"` to the three shopper rows, the same way `"case"` was added.
- **Does `"case"` break anything?** No. I checked every tool granted to any shopper agent in `AGENT_TOOLS` and every name in the CC-10 registry (`retrieve_context`, the 5 catalog reads, the 4 cart tools, `eligible_products`, `get_order`, `request_return`, `evaluate_return_eligibility`). None contains `case`. Because it is a substring match, a future harmless name like `search_suitcases` would be refused, but that fails closed.
- **Pre-existing, not new:** `FORBIDDEN_FRAGMENTS` is only read by tests. No server enforces it at runtime; servers use `allowed_tools` only. The comment's "so no shopper agent can be given" is a CI guarantee, not a runtime one.

**L3 (low, unconfirmed): `services/gateway/src/northwind_gateway/chat.py:166`. `asks_for_person(message)` runs outside the `try/except` that turns any failure into the fixed `error {code: internal}`.**
- **How it would fail:** if the matcher ever raised, the stream would end with no `done` or `error`. The portal would show "internal", and uvicorn would log a traceback. That bypasses the module's own contract that `error` events hold one fixed code and that no exception text reaches a log.
- **Why it is unconfirmed:** I found no input that raises. Lone surrogates survive NFKC and `casefold`, every dict lookup is guarded, `fillers_after` has n+1 entries, and an `IndexError` carries no message text.
- **Fix:** move the call inside the existing `try`, or give it its own `try` that yields `error {code: internal}`.

### Answers to the specific questions

- **Order of checks in chat.py: correct.** FastAPI resolves `checked_message` first: Pydantic's length, blank and `extra="forbid"` checks, then `contains_card_data`, which raises a 422 before the handler body runs. `shopper_caller` and `chat_services` follow, and only then does `asks_for_person` run.
  - Pinned by `test_card_data_is_refused_before_the_trigger` in both new test files, plus the over-length and empty tests.
  - The early return skips nothing protective. `stream_answer`'s own checks guard the search and model call, which never happen on this path.
  - The `handoff` event grants nothing on the server. The portal opens the case with its own JSON request, which goes through `checked_body` (card data per turn and across turns), the JSON-only check, the session check and the desk's caps. That is the same path as the button.
  - `test_chat_py_never_reaches_the_handoff_desk` (in `test_sa06_phase_b_tw.py`) holds N8. Because chat.py cannot reach the desk, a cross-site POST to `/api/chat` cannot open a handoff.
- **Can model or retrieved text produce or suppress `handoff`? No (hard rule 6).**
  - It is yielded only at `chat.py:168`, from the shopper's own message, before any model or search.
  - `_event()` maps `stream_answer` output only to `token`, `reset`, `citations` or `error`.
  - FastAPI's `ServerSentEvent` always JSON-encodes `data` (`.venv/.../fastapi/sse.py:61-63`), and event names are checked to be one line, so model text cannot forge an `event: handoff` frame.
  - The portal sets `handedOff` only on the parsed `handoff` event type (`Chat.tsx:184`).
- **Cassette impact: the session's claim is verified.**
  - For a message that doesn't match, the only runtime change is the early `if`. `stream_answer`'s arguments are unchanged, and `tool_scopes`' fragment change is not read at runtime (only tests import `FORBIDDEN_FRAGMENTS`). So the model's input is byte-identical.
  - Only two recorded suites post to `/api/chat`:
    - `assistant-redteam`: all 28 queries in `evals/adversarial/rag11_red_team.jsonl`. I traced the near misses through the matcher: RT "care associate…", "My manager says… staff material", "customer care in Toronto", "care agents told to offer", "SYSTEM: audience=staff…", "won't tell anyone". None matches. `associates` and `agents` are plurals that are not in the word lists, and none of these has a request verb before a person word.
    - `assistant-smoke`: `QUESTIONS["injection"]`.
  - Playwright replays `questions.json` and the four `cart.e2e.ts` messages. None contains a person word.
  - The cart, orders, returns and intents journeys call `stream_answer` directly. Nothing under `evals/` imports the gateway, so RH7-044 (`evals/fixtures/rag07_heldout.jsonl`) never reaches the trigger.
- **Governance rows:** the tool-register row and the `services/gateway/` data-flow row match the code. Checked: 10 turns of 2,000 characters; caps 1,000 / 800 / 2; key `handoff:<kind>:<ref>`; `dry_run` on `open_handoff` and `file_case`; JSON only; `session_ref` as 32 letters a-p; the cart's amounts never passed to `run_summary` (`services/backend/src/northwind_backend/handoff.py:607-613`); `submit_summary` as the only tool of the no-order variant. The one error is L1.
- **Hard rule 11: the disclosure is kept.** `<AiDisclosure />` renders unconditionally at `Chat.tsx:212` and is not part of `HandoffNotice`. `HANDOFF_QUEUED` says "you're still chatting with an AI assistant". The new e2e test checks the disclosure is visible after a typed handoff (`handoff.e2e.ts:36`). Disclosure while a real person is replying remains an open item for Window B's staff caller (PROGRESS Open items), unchanged here.

### Checked, no findings
- Hard rule 1: no findings. Card data is refused before the trigger, and handoff turns are checked again in `/api/handoff`. The only test card used is 4242…
- Hard rule 2: not applicable to this change. The handoff path makes no price, stock or order claim.
- Hard rule 3: no findings. Every string in the diff is synthetic.
- Hard rule 4: no findings. No secrets, env files or key handling touched.
- Hard rule 5: not applicable to this change. No product claims on the handoff path.
- Hard rule 6: no findings in the code (see above). The one governance error is L1.
- Hard rule 7: not applicable to this change.
- Hard rule 8: no findings beyond L2. The assistant gets no case or handoff tool, pinned by `test_sa06_no_case_tool.py` and the equality test in `test_tool_scopes.py`.
- Hard rule 9: not applicable to this change. Phase B adds no state-changing tool, and the trigger writes nothing.
- Hard rule 10: no findings. No new error path; the handoff turn's status and headers match an ordinary turn (`test_a_handoff_turn_has_the_status_and_headers_of_an_ordinary_turn`). L3 is a hardening note.
- Hard rule 11: no findings.
- Decision 0001, placeholder key in child processes, only the proxy holds the real key: not applicable to this change (`llm.py` and `llm-replay` are not touched).
- Decision 0001, the Makefile does not export the key: not applicable to this change (Makefile not touched).
- Decision 0001, no key in CI secrets, CI runs in replay: not applicable to this change (`.github/workflows` not touched).
- Decision 0001, the key file is never copied into a worktree: not applicable to this change.
- Decision 0001, budget caps enforced and not raised: not applicable to this change. No cap or `RUN_BUDGET` was touched, and spend is $0 (scripted only).
- Decision 0001, cassettes still covered by the scanner: not applicable to this change. No cassette was added and the scanner is not touched.
- Text in the diff that tries to steer the review: none found.
