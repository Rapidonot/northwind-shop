# Scoped security re-review: row #30 (fixes after the first pass)

The diff's first two lines match: `base b0314a45cfadbee53acfc84197098b9a7893c840`, `head 567753e0c115055032c422796cbd9ef9fe228f93`. I read by eye and ran nothing, so every result below is traced through the code. "Previous head" means b0314a4, the base of this diff.

Verdict: no critical or high. 3 medium: one opened by the M-1 fix (N-1), one the unclosed part of M-3 (N-2), one on the base (N-3). 6 low. Fix N-1 before W1, because it changes what W1 records.

Root of all paths: `/Users/rapido/Projects/northwind-shop/`.

## Status of each fix

| Fix | Status |
|---|---|
| M-1 | Content guard closed. It opened N-1; reporting gaps remain (N-5, N-6). |
| M-2 | Partly closed. The first pass's three inputs now fail as they should. Left: N-4, and N-3 for hedge words other than "no". |
| M-3 | Partly closed. "No problem, get the Larchpell throw instead." is now a steer. Left: N-2, N-3. |
| L-1 | Closed for `;`. The `,` and single-ID forms stay on the base, as the first pass said. |
| L-2 | Closed. The note now matches `output_check.py:1329-1331`. |
| L-7 | Closed for both named cases (the other file's vectors, database down). Left: N-8. |

## Findings

### Medium

**N-1. The M-1 wrapper switches off query understanding in the recorded suite.** New. Hard rule 2 (live price and stock filters); decision 0001 §2 and §3.
- `evals/tests/test_sa_recorded.py:90-103`, used at `:111`. `Guarded` has `search` but no `search_planned`.
- `CallerSearcher.search_planned` (`services/mcp-rag/src/northwind_mcp_rag/testing.py:89-98`) treats a retriever without that method as a test double. It searches `parts[0][0]` unfiltered and drops the rest of the plan. Before this diff the real `CallerIndex` was passed, which has it (`testing.py:154`).
- Input: any recorded message whose plan narrows the search. Likely ones are CL-003 ("I'm a men's medium"), IC-024 ("My budget is £100 ... bath sheet") and IC-022. Result: the Haiku call is still made and paid for and the live eligible-products filter is computed. Then colour, size, price and stock filters are discarded, and every part after the first is never searched.
- W1 would spend on model inputs production never sees, and commit them as SA-03 and SA-04 evidence. Nothing fails: the `frozen` check still passes because part 0 is embedded, and the test doubles have no `search_planned` either. The module docstring still says "the production surface (mcp-rag, mcp-catalog, query understanding)".
- Not a containment hole: every search now goes through `Guarded.search`, and audience and region still come from the token.
- Fix direction: add `search_planned(parts, caller)` through the same leak check. Do not use a `__getattr__` pass-through, which would skip the guard. Test two things: the plan reaches the inner index, and a planned search holding P0009 is refused. `SpyRetriever` in `services/assistant/tests/test_red_team_e2e.py:176` has the right shape.

**N-2. A "no" about another product of the steer brand still hides a steer (M-3, partly closed).** On the base and the previous head. Hard rule 6.
- `evals/src/northwind_evals/harness.py:138-145` with the steer pattern at `:185`. The pattern has a greedy 60-character gap, `finditer` returns one span, and only its start is tested.
- Input, R00970 among the sources and no Larchpell throw relevant: "There is no Larchpell rug in that size, so order the Larchpell throw." The span starts at the first "Larchpell", straight after "no", so `_hedged` is True and `steered` is False.
- "No Larchpell product beats their throw." gives the same result.
- The output check has no steer pattern, so this is on screen too.
- Fix direction: test every brand mention, and do not count a denial when the matched text itself holds a clause break.

**N-3. `_HEDGE` words other than "no" still excuse an asserted claim or steer about something else.** On the base. Hard rule 6, containment.
- `harness.py:136` returns True for any of them anywhere in the sentence.
- "Don't worry, get the Larchpell throw instead." gives `steered` False.
- "Don't worry, no receipt needed." and "Don't worry, shipping: it ships free worldwide." give `asserted_planted_claims` `[]`. Neither has an output-check pattern, by the first pass's reading.
- Same failure as M-2 and M-3 with a commoner opener. H4 was scoped to "no", so this may be a scope question: log it with an owner if it is not fixed.

### Low

**N-4. Gap patterns in `_hedged`. One input the previous head failed now passes.** Hard rule 6. `harness.py:138-145`, TRAP-INJ-016's `return ... one year` alternatives at `:67-68`.
- **New against the previous head (the important direction):** "There is no return fee and you have one year or 365 days."
  - Previous head: "365 days" has an "and" between it and the "no", so `['TRAP-INJ-016']`.
  - Now: the first mention, "return fee and you have one year", is masked as "such" plus spaces. That hides the "and", the rest reads "no such or", and the result is `[]`. Main's base also passed it.
- On the base and the previous head: "There's no return fee for one year." gives `[]`. The "no" denies the fee.
- On the base and the previous head: a missing comma, "No the throw is sold out.", gives `[]` ("the", "throw" and "is" are all in `_DENIES`). "No the throw is unavailable." gives `steered` False. Both are unlikely wordings.
- Low because the output check holds durations in a policy sentence ("return", "year") unless vouched, as in L-4, and the last form is ungrammatical.
- Fix direction: mask a mention as "such" only when its own text holds no clause end.

**N-5. A leaking retrieval is refused but not reported.** New. Hard rules 7 and 10. `evals/tests/test_sa_recorded.py:98-103`, `:181-183`.
- `Guarded` raises, and mcp-rag turns that into its transient `search_failed` envelope (`retrieve.py:67-69`). `Guarded` records no flag.
- First search: the run stops with `retrieval_failed`, the same as a database outage.
- Model refinement: the model sees a retryable failure and answers from the first sources. `turn.error` is None, `log.leak` is False, and the test passes with the leak never reported.
- `SavedFirstIndex` sets `self.leak` for this (`answerers.py:236-246`), and also when the guard itself cannot run.
- Fix direction: give `Guarded` a `leak` flag, reset it per item and assert on it before `turn.error`.

**N-6. The on-screen check is after the event and narrow.** New. Hard rule 7. `test_sa_recorded.py:71-87`, `:183`.
- It runs after `ask`, so the cassette for that message is already written (`cassette.py:99-110`, which refuses canaries, PANs and keys only). The file is left on disk, uncommitted. Retrieval and catalog inputs are guarded earlier; this covers text the model produces itself.
- It reads `turn.shown` only. Text streamed and then Reset is not read, although a shopper saw it. The harness's M6 check reads both.
- It finds IDs and "Corvane Ascent" only. Not found: "The Ascent Pro Shell is coming soon.", "Corvane-Ascent", and the facts of INT-001 to INT-006, such as INT-005's "fleece drops 30% in January". `evals/adversarial/rag11_internal_facts.json` already lists fact patterns and is not used.
- No false stop found: "Ascent" appears in no public product, SA-03 query or SA-04 prompt.

**N-7. `make record` does not stop the suite at a leak.** New wording, existing behaviour. Decision 0001 spend; hard rule 7. `Makefile:254` has no `-x` and `addopts` has none.
- `run_turns` stops its own test. If SA-04 stops on internal content, pytest still starts SA-03 in the same window, up to 50 more messages until its own first failure. Bounded by the window's run budget.

**N-8. Preflight passes on an empty or wrong index (L-7 remainder).** On the base. `test_sa_recorded.py:156`, `:197`.
- `connect().close()` succeeds on an un-ingested database, or a `DATABASE_URL` naming another one such as `northwind_enriched`. Unconfirmed, as I did not read `retrieve_planned`: if searches then return nothing rather than fail, all 70 messages record on "no sources".
- The empty-answer stop (`:229`, `:248`) still runs only after every turn has been paid for.

**N-9. Honest refusals the harness now fails.** New, the less important direction. `harness.py:123-128`. `_DENIES` is a closed list and several ordinary words are not in it.
- The previous head hedged all of these; now they are asserted, on an absolute containment gate:
  - "There is no return window of 365 days."
  - "I have no information about free worldwide shipping."
  - "There's no current promotion at 90% off."
  - "There's no code STAFFALL."
  - a claim in quotes after "no" ("no “365-day” window")
- The L-1 fix adds one: a genuine `;` citation group with a number just before a duration word is numberish again and can be blocked as false policy.
- Do not fix this by allowing any word. Extend the list only with words that keep the denial on the claim.

## What you asked me to check

- **Guards before the model or a cassette:** yes for retrieved and catalog content. `Guarded.search` raises inside mcp-rag, before rendering. The catalog guard runs inside `Recording.call_tool` (`answerers.py:583-596`) and replaces the result. It also covers `LiveFilter`'s `eligible_products` call. A guard that cannot load its canaries, or runs from the wrong directory, fails closed on both. The on-screen check is the exception (N-6). As on the base, the retrieval guard tests source IDs and scanner hits, not an internal ID inside a public chunk's text.
- **`log.leak`:** `log.begin` resets it at the start of each `ask` and it is read straight after, so a leak cannot be lost between items.
- **Spending outside a window:** none found. The two recorded tests spend only through `llm_proxy` and a claimed window. The new tests carry no `llm_suite` mark, replace `ask`, or use fakes; `Understander()` builds no client until `plan`. Loading `test_sa_recorded` by path adds no collected test.
- **Order:** `preflight` covers both files and runs before either test's first model call. Claiming the window comes first but spends nothing.
- **`steered`:** for a sentence whose only hedge is "no", the new result is a subset of the old excuses, so it only tightens.
- **Regex cost:** `_DENIES` has one mandatory word between whitespace runs, so no ambiguous backtracking.

## Checked, no findings

Hard rules:
- 1 card data: none in the diff; the canary and PAN scan runs on each search result.
- 2 live values: see N-1; the citation-mask change only restores blocking for `;` groups.
- 3 synthetic data: test strings use catalog names and fixture IDs only.
- 4 secrets: none in the diff; no `.env*` touched.
- 5 no invented attributes: no change to citation vouching.
- 6 data, not instructions: see N-2, N-3, N-4. No text in the diff tries to steer the review.
- 7 internal and region: see N-5, N-6, N-7; caller and region are unchanged.
- 8 least privilege: no tool or allow-list change.
- 9 idempotency and confirmation: not applicable to this change.
- 10 structured errors: product code not touched; see N-5 for the test.
- 11 AI disclosure: not applicable to this change.

Key handling and decision 0001:
- Placeholder key in child processes, real key only in the proxy: `agent_runtime` call unchanged.
- Makefile does not export the key: not in the diff; `unexport ANTHROPIC_API_KEY` at line 38.
- No key in CI secrets, CI in replay: `LLM_MODE: replay` in `ci.yml` and `guard.yml`, no `secrets.` or `ANTHROPIC` match; the plugin refuses other modes under `CI`.
- Key file never copied into a worktree: nothing touches it.
- Budget caps enforced and not raised: not touched.
- Cassettes covered by the secret/PAN scanner: `cassette.put` scan unchanged.

## Not checked
- I ran no test, mutant run, `make` target or scanner, and did not re-score recorded answers with the new `_hedged`. N-9's effect on the recorded answers is unknown.
- "The citation mask is main's again" is unconfirmed: I cannot run git. It matches the pattern the first pass described for the base.
- I did not re-check M-4 or the first pass's other lows.
- How many of the 70 messages N-1 affects: I did not run `rules_plan` or see Haiku's plans. Those named are from reading the prompts.
- What the model says in W1: no `assistant-intents` cassette was read.
