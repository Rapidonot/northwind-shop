# 0025: The human handoff: a deterministic trigger, no assistant tool, the Care agent in the gateway (row #23, SA-06)

Status: accepted by the user with the row #23 plan (`docs/reviews/2026-11-26-SA-06-plan.md`), including its four
design answers and the user's four changes at approval, 2026-11-26. 0024 is #27b's (returns and the policy server).

## Context

SA-06 asks for a handoff to a person "with a structured summary" (identity, intent, root cause, amounts, recommended
action), and that "an explicit request for a person is honored immediately". NFR-08 asks that the assistant "tells
shoppers when a human takes over". PRD §7 gives the Shopper Assistant no case-assignment tool, and hard rule 8 keeps
it that way. BE-07 (#26) built the Care agent and its case, but only for an order, and left `escalate_to_care`
unwired. `mcp-cases` belongs to #19. No staff side exists.

## Decision

1. **The trigger is deterministic and runs before any model call.** Two ways in:
   - a "Talk to a person" button, always shown in the chat;
   - a code-only phrase matcher on the chat message (`northwind_gateway/handoff_trigger.py`). It runs after the
     card-data refusal, so card data is still refused first. On a match `/api/chat` sends a `handoff` event and makes
     no search and no model call.

   Both go to one endpoint, `POST /api/handoff`. No model decides a handoff, and no retrieved text can trigger one.
   The matcher is tuned for precision. It is scored on a labelled set that a fresh subagent wrote before the matcher
   existed, pinned by sha256. The target is 0 false handoffs and at most 2 missed requests. A missed request goes to
   the assistant as normal.
2. **The assistant gets no tool for it.** Its prompt and tool list are unchanged. A test pins that no tool or server
   it is given names a case, a handoff, an escalation, an assignment, a ticket or staff, and that no assistant module
   imports the backend or the gateway's handoff.
3. **The transcript is the portal's.** The gateway keeps no conversation (SA-05 is not built). The portal sends at
   most 10 turns as the shopper saw them; turns the gateway refused are left out. The gateway treats them as
   untrusted: card data in a turn or across turns gets chat's fixed 422. The Care agent gets them screened and
   labelled as data, as BE-07 does.
4. **Identity comes from the server only.** It is the signed-in shopper's ID, or, for an anonymous session, an HMAC
   of the session ID under a key derived for handoffs (`northwind-handoff-v1`). The HMAC is written as 32 letters
   a–p, so it has no digits for a card check to misread. The raw session ID never enters a case, a log or a model
   input.
5. **One open handoff per identity.** The idempotency key is `handoff:<kind>:<ref>`. A second request gets the first
   reference and builds no second case (hard rule 9). Both queue writes take `dry_run`.
6. **Code fills in the case. The model writes only the summary.**
   - Intent: a closed set, from keyword rules over the shopper's turns.
   - Order: one the signed-in shopper owns. BE-07's `run_care` runs unchanged on it: its model input is byte-identical
     to before, which a test pins by sha256, so the recorded `backend-care` cassettes still replay. The handoff case
     points to BE-07's case.
   - No order: root cause `shopper_requested`, and an action from a code table per intent. The amounts are the live
     cart that the gateway read when the shopper asked (hard rule 2); an empty or unreadable cart gets `no_amounts`.
     A summary-only Care variant (`run_summary`, with only `submit_summary`) writes the summary.
   - If the Care run fails, the case is still queued, with `summary_missing`.
7. **The handoff is honored at once.** The endpoint answers 202 with the reference and fixed text. The Care run
   happens after, in a task the desk holds.
8. **"A human takes over" is a fixed notice at handoff.** It says a person from the care team will take over and
   that, until they reply, the shopper is still chatting with an AI. The AI disclosure stays on screen. A "person has
   joined" state exists behind a `HandoffStatus` interface, which only a test fake drives. NFR-08 stays `partial`
   until a staff side exists.

## Lab simplification

**The gateway runs the Care agent in-process** (the user's change 3). The handoff desk, the order store, the case
queue and the handoff queue are BE-07's deterministic fakes inside the gateway's process. The order store is seeded
read-only from `data/backend`. The queues and the audit sink are in memory, so a restart loses open handoffs. A real
deployment puts a queue between the portal and the backend crew (`mcp-cases`, #19, or the orchestrator), and the Care
agent runs there under its own credentials. This is acceptable for the lab because:
- the desk has no tool a model can call;
- Care's only write, the queue, is called by code;
- every model call goes through the record/replay proxy.

## Replay after the credit expires

Phase C records the summary-only Care variant, with one fixed demo handoff with an order and one without, in the
`backend-care` suite (the user's change 4). After about 2026-12-22, a transcript that was not recorded is a cassette
miss. The Care run then fails and the case is queued with `summary_missing`, **by design**. Staff still get identity,
intent, root cause, amounts and the recommended action.

## Consequences

- The control surface changes: `tool_scopes.py` (phase B: `case` joins the shopper agents' forbidden fragments),
  the gateway's `pyproject.toml` and `uv.lock` (the gateway depends on `northwind-backend`), `cassettes/backend-care/**`
  (phase C), and `docs/governance/data-flow.md` and `tool-register.md`.
- Open: the live "a person has joined" moment and the staff console (Window B's staff caller). When it lands, the AI
  disclosure must change once a person is replying, because "not a person" would then be false.
- Open: `mcp-cases` (#19) replaces the in-process queues. It must also be the source of truth for "already open":
  the desk's record of open handoffs lives in process memory, so after a restart a repeat would start a second build
  for an existing reference (security re-review N10).
- Open (security review L2, owner #19 or Window B's staff caller): a handoff never closes. A later request from
  the same shopper or session gets the first reference, and its transcript is not merged (the repeat is audited as
  `handoff.repeat`, once per reference). It needs a close or resolve operation. The demo sign-in has one fixed
  shopper, so every signed-in demo session shares one handoff. Until it lands, only a restart frees a place under
  the caps below (re-review N1), and turns the portal dropped after a refusal can never be sent again (L1).
- Open (security review L5, owner Window B's staff caller): references are sequential (`H000001`, ...). A real
  `HandoffStatus.watch` must check the caller's session or identity on the server, never the reference alone
  (hard rule 7).
- Limits (security review M1, re-review N1 and N2): one process holds at most `MAX_OPEN_HANDOFFS` (1000) open
  handoffs, of which at most `MAX_SESSION_HANDOFFS` (800) are anonymous sessions, so signed-in shoppers keep places.
  It runs at most `MAX_CARE_RUNS` (2) Care runs at once. Over the run cap the case is built by code at once
  (`care_busy`, `summary_missing`), still with its intent and the shopper's own order. The first refusal over a cap
  is audited once (`handoff.full`), and a repeat skips the live cart read. One agent runtime serves the desk, and
  open builds get a bounded wait at shutdown.
- `/api/handoff` takes `application/json` only, so a page on another origin cannot open a handoff without a
  preflight, and the gateway has no CORS policy. A page DNS-rebound to 127.0.0.1 is same-origin and is not stopped
  by this. A Host allowlist would stop it; that is a new control on the whole gateway, so it goes in the PR body
  (security re-review N6), as `/api/chat`'s JSON check does.
