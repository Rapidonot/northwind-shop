# Row #31 — RAG-10 + RAG-07: plan draft (decision 0021 format, read-only, not approved)

Drafted 2026-11-25 in row #30's session, uncommitted. No branch exists, and nothing here is started. Numbers are from the
checkout at `55fd1b3`; recheck them at `/req-start 31`.

## Criteria
- **RAG-10:** "the assistant may re-query or refine filters when evidence is thin, within a fixed step budget." Acceptance:
  "No answer needs more than 3 retrieval rounds; step cap enforced by a test."
- **RAG-07:** "Intent router selects the index or indexes (specifications, reviews, policy)." Acceptance: "Routing accuracy
  at least 90% on labelled queries."
- **Inferred dependencies:** #11 (done). #30 and #27 first, because both change the prompt or the turn. Not routine:
  `services/rag/**`, `services/assistant/**` and control surface.

## What is already there
- **The cap exists and is tested:** `MAX_SEARCHES = 3`, held by mcp-rag's ledger per turn and by `tools.py`; the server's
  first search is round 1 (decision 0007). 9 test files carry `RAG-10` or the cap's tags.
- **Refinement is rare:** 19 of 308 eval items refine, once each: 10 unanswerable, 5 adversarial, 4 others. So refining
  mostly happens where there is nothing to find.
- **One index:** specifications, reviews and policy are `source_type` and `doc_type` values in one table, not separate
  indexes. "Selects the index" therefore means narrowing source types.

## Proposed scope
1. **RAG-10:** close it on evidence rather than build more.
   - A test that reads every recorded turn and asserts at most 3 searches (first search included), with a positive control.
   - One scripted test that "refine" works end to end: thin first result, refined query, answer cites the second search.
   - Nothing that makes the model refine more: each refinement costs a call, and the data above gives no reason.
2. **RAG-07:** a rules-only router, in `northwind_rag.query`, next to `rules_plan`.
   - It labels a message `specifications`, `reviews`, `policy`, or a set of them; unsure means all, which is today's search.
   - **Report-only first (recommended).** Accuracy is measured on labels; the router does not change the search. Narrowing
     hurt in #11's W5 (`variant` behaviour 0.844 → 0.594), and reviews carry both evidence and the planted text.
   - Only if the user wants it wired: it may narrow reviews out of policy questions, where a review can never be the answer.
     That changes first searches, saved contexts and the retriever version.

## Labels
As in #11 and #30: a fresh subagent drafts 60 queries (20 per index, with 10 or more multi-index) to a guide, before any
router exists; sha256 pin; the user marks about 20. Pass: at least 54 of 60 with the exact index set. Frozen before
measurement; a change is stop 5.

## Cost and control surface
| | Report-only | Wired |
|---|---|---|
| Spend | $0 (rules only) | about $0.60 to $1 if first searches change as in #11's W5 (48 items); more if the prompt changes |
| Stale suites | none | those whose first search changes, from a $0 first-search diff before any window |
| Promote | none | one, by the user |
| Control surface | `evals/fixtures/rag07_queries.jsonl` | also `evals/src`, `evals/contexts`, `cassettes`, baselines via promote |

## Tests (failing first)
`test_rag07_labels_frozen`, `test_rag07_router_accuracy` (≥54/60, per-index report), router units (injection text routes to
all; a region or visibility word changes nothing), `test_rag10_no_recorded_turn_exceeds_three`, the scripted refinement.

## Stops
The label sheet; accuracy under 54/60 after one fix round; any wish to wire the router; the usual CLAUDE.md stops.

## Questions for the user
1. **RAG-07 report-only, or wired?** I recommend report-only: it meets the criterion, costs nothing and cannot move the
   baseline. The cost is that it adds no visible behaviour, which decision 0021 asks for.
2. **Close RAG-10 on the existing cap plus the two tests?** I recommend yes.
3. **Is the row still wanted before RECORD-ALL?** It is a "Keep" in decision 0021, but report-only it is the smallest of
   the remaining LLM rows and could follow SA-06.
