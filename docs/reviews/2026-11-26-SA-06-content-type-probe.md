# SA-06: which request bodies the gateway parses (measured 2026-11-26, FastAPI 0.162.0, $0)

Why: the first security review's M1 said, unconfirmed, that FastAPI parses a body with no Content-Type as JSON. Then a cross-site page could post to `/api/handoff` and `/api/chat` without a preflight. Mutant m01 survived, which put that in doubt. These probes measure it with the app's own routes and a stub desk or stream.

## /api/handoff, with the JSON-only check (head) and without it (mutant m01)
```
content-type           with check   without check
(none)                 422          422
''                     422          422
text/plain             422          422
application/vnd.api+json   422      202 (reached the desk)
application/merge-patch+json 422    202 (reached the desk)
application/json       202          202
```

## /api/chat (unchanged by this row)
```
None 422 
'' 422 
'text/plain' 422 
'application/x-www-form-urlencoded' 422 
'multipart/form-data; boundary=x' 422 
'application/vnd.api+json' 200 MESSAGE PARSED
'application/json' 200 MESSAGE PARSED
```

## Reading
- FastAPI's body parsing refuses every CORS-simple body (none, text/plain, form, multipart) on both routes. Those are the only bodies a cross-site page can send without a preflight, so neither route is reachable that way. The gateway has no CORS policy, so a preflight fails.
- `/api/handoff`'s own check adds the refusal of `+json` subtypes, which FastAPI parses as JSON. That is defence in depth, not the cross-site control.
- The first review's M1 cross-site point, and the PR body's note that `/api/chat` accepts a body with no Content-Type, are corrected by this measurement. DNS rebinding (re-review N6) is a separate, same-origin path and is unchanged.

Probe scripts: the session's `$TMPDIR/probe_ct.py` and `probe_chat2.py`. Their logic is above: the app's TestClient, dependency overrides for the desk, cart and caller, and a recording `stream_answer` stub.
