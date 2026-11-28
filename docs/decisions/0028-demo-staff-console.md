# 0028: A read-only demo staff console, behind DEMO_STAFF_DASHBOARD (OPS-1, part 1)

Status: accepted by the user, 2026-11-27 (the OPS-1 plan, with the approval changes: the screen's patterns copied, not
imported; the full RECORD-ALL freeze list pinned while it ran, the pin deleted once RECORD-ALL shipped; no `make ci` and no
ship until RECORD-ALL had shipped; D1–D3; the look approvals, and "what runs today" for the order band). Amended
2026-11-28: the staff routes are registered GET only, and CHK-01's order-route pin reads `app.routes` with
`/api/staff/orders` on its approved list as GET only; every count adds up to the whole it sits under (§6).

## Context
For live demos on the hosted site, the user wants a staff console that shows how the shop's agents are wired and what they
did, over real data: the agent system, the orders and escalations, handoffs, agent activity, and stock and holds. It must
write nothing, have no staff sign-in, and exist only when switched on. OPS-1 has no PRD row; the user's request is its
criteria. RECORD-ALL (#18) runs in window A with the assistant, the agent runtime and agents, the tools, `llm.py`,
`tool_scopes.py`, `chat.py`, the orchestrator's planner, the substitutes and the eval answerers frozen.

## Decision
1. **Off unless DEMO_STAFF_DASHBOARD is exactly "1".** Off, every route under `/api/staff`, under every method, is the same
   404 as a path that does not exist, and no store is read first. On, only GET answers, never cached; any other method is that
   same 404. A later stream registers through the same guard. The portal's `#/staff/<view>` asks `/api/staff/overview` first
   and loads the console's chunk only on a 200; a 404 shows the shop's own not-found page.
2. **No sign-in, no session, no write.** The console opens no shopper session (`/api/staff` is not a session path) and calls
   no port write, cart write or confirmation. It reads the checkout's stores (placed orders, inventory, audit) through a
   provider `app.py` registers, never the checkout desk or module: SA-01's pin (only `app.py` imports `checkout.py`, so no
   other module can reach the confirmation issuer) stands unchanged. The handoff desk registers its stores the same way
   (`handoff.handoff_stores`).
3. **Visibility: the user's decision.** Full lifetime history for everyone, nothing anonymized or auto-deleted. Part 1 shows
   handoff summaries, labelled as model-written from an untrusted transcript; chat text waits for part 2, which ships its
   capture together with the public-demo chat notice (D2). Every string leaving `/api/staff` passes a screen: card data (its
   NFKC form too), key-shaped secrets by the cassette scanner's own patterns, and the answer key's internal canaries are
   withheld. The patterns are copied into `staff/screen.py`, not imported: `northwind_llm_replay` is not a gateway dependency
   and must not ship in the hosted server; a test pins the copy and proves every string `scan_text` flags is withheld.
   Internal-only products are left out of every view (hard rule 7).
4. **The agent map is generated, never hand-written.** MCP servers, their tools and the assistant's and query
   understanding's edges come from `tool_scopes.AGENT_TOOLS`; the catalog's cart writes collapse to "+ cart write tools",
   named from `catalog_tool.CART_TOOL_NAMES` less `get_cart`. Each AI agent's model is the `llm.py` constant its own module
   imports and uses, read from its source by AST, labelled from the constant's value. The module itself is not run, but
   finding its source imports its parent packages. The eval-only judge comes from `llm.JUDGE_MODEL`, and a test checks that
   `northwind_evals/judge.py` uses that constant (the judge's module is not a gateway dependency). Every other edge stands on an import
   that must be in the code. Positions are the portal's layout table (`apps/portal/src/staff/layout.json`, measured from the
   reference mockup); generated nodes and edges match its boxes and routes both ways, and the portal's snapshot
   (`system.json`) is pinned equal to what the gateway serves.
5. **The order band shows what runs today.** The reference mockup's order lane (order events → orchestrator → order agent →
   ports → escalation) matched no path in the code. The orchestrator serves a read-only staff request, "Staff request
   (command)"; an escalated order's case is "Escalated order (command)" → escalation → Care agent. Neither runs from the shop.
6. **Real numbers only.** Orders are the 1,000 seeded ones, seeded as northwind-orders seeds them, each with the shopper
   status northwind-orders shows (pinned equal, order by order) and its exception as the reason, plus Pay (demo) orders,
   an escalated one with the checkout's own escalation code as its reason. Every count adds up to the whole it sits under:
   total = escalated + other exceptions + processed, and only escalated orders are broken down by reason (an escalation
   with no code is "unrecorded"). The overview's tile shows the escalated count "of N orders with an exception". The handoff desk's escalation cases are shown
   as part of each handoff, not as a list of their own.
   Handoffs, agent activity and holds are this process's in-memory stores, shown "since this server started". A handoff has
   no time: the queue keeps none, so none is shown. The four sample traces are marked sample, carry no timings, and walk the
   map's lines from their entry point.
7. **`make demo-purge` (D3) is part 2.** Today a gateway restart empties every in-memory store. With durable handoff and
   audit stores (part 2), `make demo-purge` will empty them from the user's own terminal, refusing from the sandbox.

## Consequences
- Part 2, after RECORD-ALL: live trace events (no trace ID in anything a model sees; recorded requests byte-identical, pinned),
  durable handoff and audit stores, live highlighting, a demo-order replay command, the demo server's replay opening every
  suite a demo uses, chat text with the public-demo notice, and `make demo-purge`.
- A frozen-file pin (`test_ops1_frozen.py`) guarded the row while RECORD-ALL ran. On main it would fail any later row that
  rightly edits a frozen file, so it was deleted in the merge from main after RECORD-ALL shipped (the user, advisor-reviewer
  finding 4, option (b)); the gate report checks the diff's file list against the freeze list instead.
- The map's layout table must follow any change to the agents or their imports: the tests fail until it does.
