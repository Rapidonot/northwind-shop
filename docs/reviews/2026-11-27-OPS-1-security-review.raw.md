# OPS-1 security-reviewer report, verbatim (base 3b0e7bf, head 0488b9d; diff /tmp/claude-502/northwind-security-OPS-1.diff)

## Security review: OPS-1 demo staff console (base 3b0e7bf, head 0488b9d)

I reviewed the diff at /tmp/claude-502/northwind-security-OPS-1.diff with the PNGs skipped. I re-read the CLAUDE.md hard rules and decision 0001 for this review, and read the context the diff depends on in full: checkout.py, session.py, app.py, backend audit.py, fakes/handoffs.py, backend handoff.py, care.py `check_summary`, cards.py, northwind_llm_replay/scan.py, inventory/memory.py, and the installed FastAPI 0.162 and Starlette 1.10 routing.

There are no critical, high or medium findings. No card data, payment token, session ID or cookie, signing key, API key or internal-only product reaches any view. There are 5 lows, 4 of them confirmed by reading the code, plus 4 info notes. Nothing in the diff, fixtures, plan or PROGRESS text tries to steer the review.

### Findings (most severe first)

**L1 (low): with the flag off, the console still shows it exists, through a trailing-slash redirect and a 405 for unlisted methods. Decision 0028 §1 (hard rule 4 spirit). Confirmed by reading the code.**
- Where: /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/routes.py:23 (`ALL_METHODS`) and :41-46 (`api_route`).
- What is wrong: the routes are registered whatever the flag says, and only the `guard` dependency hides them.
  - Redirect: FastAPI's router (`.venv/.../fastapi/routing.py:2828-2857`) redirects a slash-toggled path to any route that matches. So with the flag off, `GET /api/staff/overview/` returns `307 Location: /api/staff/overview`, while `/api/no-such-path/` returns 404.
  - 405: a method outside `ALL_METHODS` (TRACE, CONNECT, PROPFIND, any token uvicorn accepts) is a partial match (`fastapi/routing.py:1580-1582`). It is answered with `405 Method Not Allowed` and `Allow: GET, HEAD, POST, …`, never 404.
- Why the tests pass: `test_ops1_tw_gate.py` lists `/api/staff/overview/` in `ODD_PATHS`, but Starlette's TestClient follows redirects by default, so the test sees the final 404. Its `METHODS` covers only the 7 listed methods.
- How it fails: anyone can probe `/api/staff/overview/` or `TRACE /api/staff/orders` and learn that the hosted server has the console, even with it switched off. No data is served. The repo has treated this kind of leak as a finding before (session.py:166-177, the "no `Allow` header" fix N2).
- Fix: decide at the ASGI layer, before routing, as session.py does for `SIGNIN_PATH`. Every path equal to `/api/staff` or starting with `/api/staff/`, under any method, gets the exact unknown-path 404 unless the flag is on, the method is GET and the path is exactly a known view. Add tests with `follow_redirects=False`, TRACE and an arbitrary method token.

**L2 (low): two worker threads can race to build the checkout desk, which can lose Pay's idempotency record. Hard rule 9. The code path is confirmed; I did not reproduce the race.**
- Where: /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/app.py:79-82, with checkout.py:414-416 (`@cache def checkout_desk()`).
- What is wrong: the staff handler `read` and the `sources` dependency are sync, so they run in the threadpool. The first staff GET can therefore call `checkout.checkout_desk()` on a worker thread while the first `pay` calls it on the event loop.
  - `functools.cache` does not stop two threads both missing and both building a desk. Construction reads shoppers.jsonl and stock.jsonl, which releases the GIL and widens the window.
  - The cache then keeps the second desk. Before this change, `checkout_desk()` was only ever called from the event-loop thread, so this race could not happen.
- How it fails: one pay places O900001 on desk A, whose `paid`, `unemptied`, inventory holds and order counter are then orphaned. Every later pay uses desk B. Replaying the same idempotency key, or a second order, places another O900001 and another (fake) charge.
- Fix: build the desk once at startup, by calling `checkout.checkout_desk()` in app.py at import or in `lifespan`. Alternatively, have `_checkout_stores` read stores captured at startup rather than call the factory per request.

**L3 (low, fails closed but breaks the view): the screen withholds most audit timestamps in Agent activity as card data. Hard rule 1 screen. Confirmed by reading the code.**
- Where: /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/views.py:324 (`"time": e.at`), with screen.py:57.
- What is wrong: `e.at` is `datetime.now(UTC).isoformat()` (audit.py:393-394), for example `2026-11-27T10:15:30.123456+00:00`.
  - `contains_pan`'s `_DIGIT_GROUP` (cards.py:26) joins digits across single `:`, `.` and `+` characters, so the time part becomes the 16-digit run `1015301234560000`.
  - That gives 10 windows of 13 to 16 digits, each passing Luhn about 1 time in 10. So roughly half or more of event times come back as `[withheld: card data]`.
- Fix: format the time in the view as `_iso(datetime.fromisoformat(e.at))`, which is at most 12 digits. That keeps STARTED and the hold times safe too. Do not change the screen. Add a test with real clock values.

**L4 (low): an anonymous caller can make the server do heavy work on every request, with no limit. Availability. The code is confirmed; how slow it gets is unconfirmed.**
- Where: views.py:215 (`orders_view`), :316-332 (`activity_events`) and :412 (`overview_view`).
- What is wrong:
  - Every request to activity or overview deep-copies the whole checkout and handoff audit logs while holding each sink's `threading.Lock`. `pay` calls `append` on the event-loop thread, so a long copy stalls the loop.
  - Every orders request screens more than 11,000 strings (1,000+ rows × 11 fields). Each screen runs `contains_card_data` twice (NFKC), 4 key regexes and a canary loop.
  - `overview` also rebuilds stock and orders, and `agent_map.generate()` walks the cached ASTs on every call.
  - There is no pagination, no cap on events and no rate limit.
- How it fails: a loop of anonymous GETs on a public URL ties up the threadpool and the GIL, which slows chat SSE and Pay for real visitors.
- Fix (keep every screen):
  - Screen `store_orders()` once at load; it is immutable and already cached.
  - Return only the latest N events, and add a bounded "since seq" read to the sink instead of copying everything.
  - Paginate orders.
  - Rate-limit `/api/staff/*` at the edge.

**L5 (low): the canary screen fails open when the answer key is missing. Hard rule 7 (last line of defence).**
- Where: /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/screen.py:46-53.
- What is wrong: if `_answer_key.json` is missing (its path comes from `__file__` parents, which changes with hosted packaging), `canaries()` silently returns an empty set. The repo's own scans use `load_canaries(..., required=True)` so they never report clean with this check off.
- How it fails: orders, handoffs, activity and system do not read the catalog, so they would still serve, with no canary screening. `catalog()` would 500 for stock and overview.
- Fix: while the flag is on, fail closed. Answer 503 for every staff view if the answer key cannot be read. This is stricter, not looser.

### Info
- **I1, the screen's coverage.**
  - The key patterns are byte-identical to `scan._KEY_PATTERNS`. `contains_card_data`, which applies NFKC and joins on any separator, covers everything `scan_text` flags as a PAN: plain runs, grouped and wide-grouped forms, and NBSP or thin-space separators.
  - The scanner's key-fragment check does not apply, because the gateway holds no API key, correctly.
  - Gaps: key patterns and canaries are matched on the raw text only (no NFKC, case-sensitive), and strings are never joined across fields (unlike `holds_card_data`).
  - Screening each string on its own is enough for this data. The only free text is the handoff `summary`, which `check_summary` (care.py:213-235) already NFKC-normalizes and refuses if it holds Cf/Cc characters, card data, emails, links or money. Every other string is matched against a pattern by the audit (`audit._ref`) or set by code.
  - Integers pass the screen unchecked, but all are code-made counts or amounts.
  - Splitting a PAN across two visitors' summaries is possible only for a visitor posting their own number.
- **I2, agent_map.py:88-99.** The docstring says "nothing executed", but `importlib.util.find_spec("northwind_backend.orchestrator.__main__")` imports the parent package. That loads `orchestrator.run`, `plan` and `aggregate` into the gateway on the first `/system` read. I found no module-level side effects (only regexes, a logger and a RetryPolicy). Paths come from constant module names, never from the request. Results are cached per module, and only derived booleans and constant names are output, never paths or source. The public map does disclose tool names, which servers need sign-in, and model families. That fits the user's visibility decision.
- **I3, least privilege on references.** `Sources` holds the handoff `CaseState` (unused) and the live `InMemoryAuditSink` objects, which still have `append`, plus the live `OrderState` and `InventoryState`. Nothing calls a write and no request input reaches them, but passing read-only callables (`read_all`, snapshot functions) would make "cannot write" structural.
- **I4, user decision noted.** Model-written summaries of anonymous visitors' chats go to every visitor. They are labelled "written by the Care agent (an AI model) from an untrusted transcript" and rendered as React text. This makes the page a channel for one visitor's words, filtered as in I1, to reach everyone. It is a data-handling point for part 2's public-demo notice, not a code defect.

### Verified safe (the seven areas)
1. **Exposure.**
   - Store orders give `order_id`, `placed_on`, `shopper_id`, `region`, `postal_code`, counts, status and `exception` (a code value). `payment_token` is never read into a row.
   - Demo rows hold the same fields. The `SANDBOX_APPROVED` token on desk orders is not read.
   - Audit `_ref` reads only `_REFS` names whose value is a string. The audit already matches these against patterns, and its order view excludes the token and address. `correlation_id` is `checkout-O…` or `gateway`, never a session.
   - Handoffs show no identity, session ref or correlation ID.
   - The console reads no signing key or cookie.
   - Internal-only products are filtered fail-closed in `catalog()` (`visibility == "public"`, variants need a public product) and in holds. Orders show only line and unit counts. The traces' P0001 is public.
2. **The screen.** Covered in I1. Keys, nesting and lists are handled.
3. **The gate.**
   - Route `dependencies` are inserted before the endpoint's `Src` dependency and solved in order, so `guard` raises before `sources()` and no store is read.
   - At import, only `use_checkout` is registered; nothing is read.
   - `/api/staff` is not in `SESSION_PATHS` (exact match), so no cookie is set and no session is opened.
   - Responses are `no-store`, and there is no server cache of responses.
   - Gaps: only L1.
4. **Hard rule 9 and the SA-01 pin.** views.py does not import checkout. `test_sa01_pr2_checkout_pins.py` also catches `from northwind_gateway import checkout`, and app.py is still the only importer. The provider exposes no `PaymentState` or confirmation book, port, cart writer or handoff queue. The views write nothing:
   - `everyone()` and `read_all()` hand out copies.
   - Stock rows are copied under `state.lock`.
   - `HandoffCase` is frozen, and `store_orders` builds its own `OrderState`.
   - Risk: only L2.
5. **Hard rules 6 and 8.** `tool_scopes.allowed_tools` returns frozensets, which are only read. Nothing registers tools or changes scopes. The summary is labelled as untrusted.
6. **Hard rules 2 and 5.** Stock and status come from the live in-process stores and go only to the HTTP response. No assistant module or model context is imported or touched. The traces are marked `sample` and carry no counts.
7. **Portal.** All rendering is React text. There is no `dangerouslySetInnerHTML` or innerHTML, and staff.test.tsx pins that. The only `href`s are static `#/staff/<view>`. `StaffConsole` is lazy-loaded only after a 200 from `/overview`. fixtures.ts and system.json are imported only by tests.

### Checked, no findings
- Hard rule 1: no finding beyond L3 (a fail-closed false positive). Sandbox tokens are not exposed.
- Hard rule 2: no finding.
- Hard rule 3: no finding. No new data, and everything shown is synthetic (I4 noted).
- Hard rule 4: no finding. No secret is read or shown; the key-shaped screen is in place.
- Hard rule 5: no finding. No model claims are touched.
- Hard rule 6: no finding.
- Hard rule 7: no finding beyond L5.
- Hard rule 8: no finding (I3 noted).
- Hard rule 9: no finding beyond L2.
- Hard rule 10: not applicable. The staff API is not a tool, and the 404 deliberately copies the unknown path's.
- Hard rule 11: not applicable to this change.
- Decision 0001, placeholder key for child processes and only the proxy holds the real key: not applicable; nothing in the diff touches the LLM factory or the proxy.
- Decision 0001, the Makefile does not export the key: not applicable; the Makefile is not in the diff.
- Decision 0001, no key in CI secrets and CI runs in replay: not applicable; .github is not in the diff.
- Decision 0001, the key file is never copied into a worktree: not applicable.
- Decision 0001, budget caps enforced and not raised: not applicable; no cap is touched.
- Decision 0001, cassettes still covered by the secret/PAN scanner: not applicable; the scanner and cassettes are unchanged, and screen.py only copies the patterns.

Files: /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/staff/{routes.py,views.py,screen.py,agent_map.py}, /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/app.py, /Users/rapido/Projects/northwind-shop-b/services/gateway/src/northwind_gateway/checkout.py, /Users/rapido/Projects/northwind-shop-b/services/gateway/tests/test_ops1_tw_gate.py
