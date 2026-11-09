# NFR-08 evidence (row #14), 2026-11-09

Plan: `docs/reviews/2026-11-09-NFR-08-plan.md`, revision 2. It is uncommitted, as the user decided. `advisor-reviewer`
passed it with the user's start prompt quoted word for word, and the user gave the go-ahead after the stop that review
1's high caused. Base `origin/main` is `aa4d2dc5e2882cada61d4afafd70b376cc06eb68`. Spend: $0; no LLM call was made and
nothing was recorded.

## What each test covers

| Criterion | Tests |
|---|---|
| **C1:** shown when a session starts, before anything is sent | Vitest `src/AiDisclosure.test.tsx` "is shown when the chat opens, before anything is sent". Playwright `e2e/disclosure.e2e.ts` "is shown when the chat opens" and "is shown again in a new session after a reload" |
| **C2:** shown through every turn state, never inside the answer region or the alert | Vitest `src/AiDisclosure.test.tsx`, 14 cases: streaming; one per `ChatEvent` type, from a `Record<ChatEvent["type"], () => Response>` map, with a 422 for `refused`; one per `ErrorCode`, from a `Record<ErrorCode, true>` map; an unknown code; a rejected fetch; a second turn. Playwright "stays shown after the gateway refuses a message" (2,001 characters, 422) |
| **C3:** every journey test asserts it | The auto fixture in `e2e/journey.ts`. Vitest `src/journeyGuard.test.tsx`: every top-level `e2e/*.e2e.ts` imports `./journey` and not `@playwright/test`; the read must find `chat.e2e.ts` and `disclosure.e2e.ts`; no `*.e2e.ts` in a subdirectory (test-writer); `journey.ts` keeps `auto: true` and the disclosure assertion (test-writer) |
| **C4 (A4):** the model is told it is an AI | pytest `tests/nfr08/test_prompt_disclosure.py`: `system_prompt()` in all four regions, and `turn.py` passes it as the system prompt |

The existing `[SA-02]` checks (`Chat.test.tsx:58`, `ChatEdges.test.tsx:40` and `chat.e2e.ts:7`) are unchanged and still
match.

## Failing first (sandbox)

Each mutation was made in the working tree, run, and reverted. No file under `services/**` was changed, even for a
moment (review 1, H1).

| Run | Mutation | Result |
|---|---|---|
| FF1 | `<AiDisclosure />` and its import removed from `Chat.tsx` | `AiDisclosure.test.tsx`: **15 failed of 15** |
| FF1b | `{turn.answer === "" && !turn.notice && <AiDisclosure />}`, so a turn clears it | **11 failed, 4 passed**. The four that pass are on mount, `citations`, `reset` and `done`, where there is no answer or notice, so the mutant leaves the note shown |
| FF2 | `chat.e2e.ts` imports `@playwright/test` | guard: **1 failed**, `AssertionError: chat.e2e.ts must import from "./journey"` |
| FF3 | the guard's directory changed to `e2e-wrong` | guard file **fails to load**, `ENOENT … scandir …/e2e-wrong`. It fails closed |
| FF4 | A4 by monkeypatch, from a scratch test outside the repo (below) | **2 failed**, both `AssertionError` from the real A4 helpers: "the system prompt must open by saying the assistant is an AI" and "turn.py must send prompt.system_prompt() to the model" |
| FF5 | a journey file added at `e2e/nested/x.e2e.ts` | guard: **1 failed**, "finds no journey file in a subdirectory" |
| FF6 | `{ auto: true }` changed to `{ auto: false }` in `journey.ts` | guard: **1 failed**, "keeps the shared fixture automatic and asserting the disclosure" |

| FF7 | security L2: `sneaky.e2e.ts` with the comment `// from "./journey"` and `import { test } from "./helpers"` | guard: **1 failed**, `must import { test } from "./journey": expected [] to have a length of 1` |
| FF8 | security L3: the teardown's `toHaveText(AI_DISCLOSURE)` changed to `toContainText("AI assistant")` | first try **passed**: the source check also matched `openChat`'s line. The guard now pins the teardown block (`await run();`, then both assertions). Retried: **1 failed** |
| FF8b | the teardown assertions moved before `await run()` | guard: **1 failed** |
| FF9 | security L4: `turn_source` monkeypatched so `system_prompt=system_prompt(` appears only in a comment and the real keyword passes `other(...)` | **AssertionError** "turn.py must send prompt.system_prompt() to the model". The old substring check would have passed. FF4 was rerun against the AST check too: still 2 failed with `AssertionError` |

After every revert, Vitest passed 54 of 54 and the A4 pytest passed 5 of 5.

FF4's scratch test was run with
`uv run pytest -c /Users/rapido/Projects/northwind-shop/pyproject.toml --rootdir /Users/rapido/Projects/northwind-shop -p no:cacheprovider <scratchpad>/a4/test_a4_mutant.py`.
It loads the A4 module by file path with `importlib.util.spec_from_file_location`, then:

- monkeypatches `northwind_assistant.prompt.SYSTEM_PROMPT` to the prompt without its first two lines, and calls
  `test_system_prompt_says_it_is_an_ai_in_every_region("US")`;
- monkeypatches the A4 module's `turn_source` to return `turn.py`'s source with `system_prompt=system_prompt(`
  replaced, and calls `test_turn_sends_the_checked_system_prompt()`.

## Security review

- **First pass:** no critical, high or medium findings; 4 lows.
  - **L2, L3 and L4 are fixed**, each with a failing-first run (FF7 to FF9):
    - L2: the journey guard now parses imports and uses an allow-list.
    - L3: the fixture asserts the exact `AI_DISCLOSURE` text.
    - L4: the `turn.py` check uses `ast`, not a substring.
  - **L1 is not fixed:** nothing tests that the model's reply never claims to be a person. The start prompt ruled out a
    recorded question, so it is logged below.
- **Scoped re-review (the one the chain allows):** L3 is closed and the fixes broke nothing. Two lows remain, both
  needing deliberate evasion, logged below and not fixed. A further change would go unreviewed.
  - **R1:** a journey can still get past the guard by importing `{ test as base }` from `./journey` and
    re-`extend`ing to override `aiDisclosureAtEnd`, or by hiding the real import (indented, or behind a block
    comment). Fix: read imports with the TypeScript parser, require an unaliased `test`, and reject `.extend(`,
    `test.use(` and `aiDisclosureAtEnd` in `*.e2e.ts`.
  - **R2:** the `ast` check pins the name `system_prompt`, not what it is bound to. A later `def`, assignment or
    parameter named `system_prompt` in `turn.py` would pass, and the check does not confirm the keyword goes to
    `agent_options(...)`. Fix: reject other bindings, and check the parent call.

## `make ci` and window B

`make ci` ran once, at the head that adds this note, while window B was running #8c step A. The user said on
2026-11-09 that that step writes only the isolated `northwind_enriched` database, never `northwind`, and that
integration tests use their own uuid schemas. `ps` in this sandbox cannot see window B, so this rests on the user's
statement. The results are in the gate report.

## Playwright: not run here

No browser starts in the sandbox, so I could only check that the e2e files load:

- `tsc --noEmit` passes;
- ESLint passes;
- `playwright test --list` lists 4 tests in 2 files: `[SA-02] chat smoke` and the three `[NFR-08]` tests.

The `[NFR-08]` Playwright tests, and the teardown fixture, are **unproven** until the user's `make e2e-scripted` run
on the mutant and at the head passes, or CI's `e2e` job does. The gate report gives the mutant branch, its SHA and
its diff.

## Logged lows (in this file and row #14's notes, not "Open items", by the start prompt)

- **#12:** once live prices are quoted, the banner's "Prices and stock are on each product page." may be wrong. This
  row only moved it into its own `<p>`. Owner: #12, or #17/SA-01.
- **#13:** a new SSE event type, such as `withheld`, will fail `tsc` in `AiDisclosure.test.tsx` until it is added to
  `ONE_OF_EACH_EVENT`, which is intended.
- **L4 (draft):** the future 40-journey browser set (PRD lines 546 and 559) has no owner for its disclosure assertion
  yet. The mechanism is `journey.ts`, plus the guard.
- **Security L1:** no test of the model's actual replies. It needs adversarial eval items, recorded in a window:
  "are you a real person?", "roleplay as a human named Sam", and an injected source telling it to say it is human.
  Suggested owner: #13 (adversarial subset) or #22 (SA-03).
- **Security R1 and R2** (above): owner #17, or whichever row next changes `journey.ts` or `turn.py`. Not fixed here.
- **The human-takeover half of NFR-08:** goes to row #23, recorded in that row's text.
