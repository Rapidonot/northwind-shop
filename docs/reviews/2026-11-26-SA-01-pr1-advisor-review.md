# SA-01 PR 1: advisor-reviewer's first read, verbatim (2026-11-26)

Diff: `/tmp/claude-502/northwind-review-SA-01.diff` (`make review-diff`), base 361d396, head 5905b73. Triage at the end.

---

findings

The SHA lines match. The diff at `/tmp/claude-502/northwind-review-SA-01.diff` starts with `base 361d3963426574bba9f81012832b8531fb8e2096` and `head 5905b73ad7b40073122e56d478da7de1b420f843`, the SHAs you gave. A match only catches accidents. Nothing in the diff, logs or plan tried to steer the review.

## Findings (most severe first)

1. **Item 1 and 4, medium: the tool register gets the portal's scope wrong.** `docs/governance/tool-register.md:28` is the `search_products` row. It now says "from SA-01 PR 1 also to `shopper-portal`, the gateway's product page (`GET /api/product`)". That is false:
   - `tool_scopes.PORTAL_CATALOG_TOOLS` is `{get_cart, get_product, browse_products, get_prices}`, with no `search_products`. Amendment 2 says the same.
   - The `get_product` row (line 29) is unchanged, although amendment 2 promised a note on it. The note seems to have landed on the wrong row.
   - This register is the least-privilege record (NFR-09, hard rule 8), and `tests/governance/test_tool_register.py` did not catch it. The file is the row's own, so it should be fixed before `make ci`.

2. **Item 2, medium, unconfirmed: fail-first evidence is missing for several fixes.**
   - No log shows these failing first:
     - F2, the ID pattern checks in `shop.py` `_tile` and `_product_view`.
     - F3, the first visit opening one session.
     - N2, the widened `cardGuard.ts` separators (hard rule 1).
     - test-writer's card data in a repeated or unknown query value (hard rule 1). The PR body says "six failures", but no log was given.
     - The photo listing counting a directory or a dangling link as a photo.
   - The F1 and N1 logs hold only the failing run (6 failed / 5 passed, and 2 failed / 11 passed). The passing runs ("11 passed", "13 passed") are not in those files.
   - The F1 log names `test_the_filter_is_on_the_access_logger`, which the head no longer has; it became `test_the_filters_are_on_the_access_and_error_loggers`, which checks more. That is consistent, but it shows the log predates the head.

3. **Item 1, low: plan deviations the PR body does not call out.**
   - The plan's Playwright list includes "a price changed while the store runs is the one shown", "unavailable states" and "the chat button on desktop". None is in `apps/portal/e2e/shop.e2e.ts`. The price change is tested in pytest (the `set_price` tests in `test_sa01_shop*.py` and `test_sa01_portal_tools*.py`). The unavailable state and the desktop panel are tested in Vitest only.
   - `Layout.tsx` was folded into `App.tsx`.
   - The `page=` parameter of `GET /api/products` was dropped.
   - The PR body should say where each one is covered, or that it was dropped.

4. **Item 3, low: a locator changed after a failure.** Between c7c8f5c and 5905b73, `getByRole('status')` became `getByRole('main').getByRole('status')` in `shop.e2e.ts` (file line 83). The expected text is unchanged, the footer keeps `role="status"`, and you directed this fix. So I do not count it as weakening. But the PR body's "User decisions used in this PR" list does not record that decision, and it should.

5. **Item 4, low, for human attention: new inline suppressions.**
   - `apps/portal/src/pages.tsx` `useLoaded` has `// eslint-disable-next-line react-hooks/exhaustive-deps`.
   - `services/gateway/src/northwind_gateway/shop.py` has `# noqa: BLE001` three times.
   - Each turns a lint rule off for one line of new code. No existing test or gate was loosened.

6. **Process, low: the stop 4 event is not in any committed record.** `git branch -D` printed "could not lock config file", you removed the stray key, and the user said go on. The gate report's evidence section should say so.

7. **Process, low: the security pass was not over a `make review-diff`.** Plan answer (b) says the full pass runs over the PR's whole `make review-diff`. It ran over a diff of the uncommitted working tree, `/tmp/claude-502/northwind-review-SA-01-security.diff`; I could not see whether that diff held everything now committed. The N1 and N2 fixes in `access_log.py` and `cardGuard.ts` (hard rule 1 code) and 5905b73 came after the scoped re-review. The rules allow this because N1 and N2 are low.

## Unconfirmed
- **The brief:** I was not given it. The plan summarises it rather than quoting it, so I cannot check the four trial-build rules or the look against it. The code cites rules 1, 3 and 4; I found no rule 2.
- **Font provenance:** I cannot verify from the bytes that the fonts come from `@fontsource-variable/{archivo,inter}@5.3.0`. The file names and the OFL files match amendment 1.
- **The mutant and head runs:** `mutant/SA-01-no-shop-shell` (bb7a729) is deleted, so I cannot read it, and neither log records a SHA.
  - The line numbers fit the head's test file: tests at 9, 50, 61, 80 and 92, and the scoped locator at line 83.
  - The c7c8f5c log has the phone test at line 91, one line earlier, which fits the comment added in 5905b73.
- **Pass counts:** the PR body's pytest and Vitest counts come from single-file runs, and no logs were given.
- **Full e2e suites:** `make e2e` and `make e2e-scripted` last ran at c7c8f5c, not at the head.
- **`make ci`:** not run yet. It is the next step in the chain.
- **The ledger:** I could not check `~/.northwind/ledger.jsonl`'s modification time myself.
- **The replayed cost line:** confirmed. `cassettes/assistant-smoke/a8a6e017cc6375b4b092d66e9ff14cf34268139fb34ef9d4eb814094622ac50b.json` holds `cost_usd 0.0060969`, `recorded_at 2026-11-25T14:01:27Z` and `output_tokens 67`, the same as the line in `/tmp/sa01-pr1-e2e.log`.

## Checked
1. **Scope: findings 1 and 3.**
   - The diff is PR 1's read-only scope: shop pages, look, photos, fonts, the chat button, a read-only bag, `shop.py`, `access_log.py`, `portal.py` and the scope change.
   - There are no write routes, `checkout.py`, `reserve_stock`, cart writes, `package.json` or `.gitignore`. The photos are committed as you decided.
   - Window A's paths: the diff touches no `services/backend/**` or BE-04 file.
2. **Failing first: finding 2.** The browser mutant fails all 5 tests and the head passes 5. F1 shows 6 failing and N1 shows 2 failing. The other fixes have no fail-first log.
3. **No test weakened.** The exact pins in `test_contract.py`, `test_chk01_criteria.py`, `test_cart_session.py` and `test_chk01_gateway_criteria.py` stay `==` and were widened only by name, under your stop 5 yes. `SERVER_ONLY_TOOLS` is unchanged. The locator change is finding 4.
4. **Hard rules and protected paths: findings 1 and 5.**
   - No `chat.py`, `cards.py`, `llm.py`, `services/assistant/**`, `services/rag/**` or environment files.
   - The mcp-catalog changes (`portal.py`, `tools.py`) are a hard-rule 8 path. They are covered by amendment 2 and are not a problem on this non-routine row.
5. **Spend and window: $0.** No window was opened, nothing was recorded, and the diff has no cassette. Plan answer (a) states $0, and the `tools/list` digest is pinned in `test_sa01_portal_tools.py:39`.
6. **Requested changes: partly unconfirmed (the brief).**
   - In the plan: S1, every item of amendment 1, the stop 5 yes and amendment 2.
   - In the PR body: photos committed with no `.gitignore` change.
   - In the code: the locator scoped while the health line keeps its role.
7. **Control surface:** one file, listed below and flagged for human attention. You approved it with the plan (stop 8).

## Control surface
- `services/common/src/northwind_common/tool_scopes.py`: `services/common` is in `RUNNER_SYNC_PATHS` (`Makefile:199`). `shopper-portal`'s catalog row goes from `{get_cart}` to `PORTAL_CATALOG_TOOLS`, and `PORTAL_ONLY_TOOLS` is added.

No other changed file is on the control surface: no `Makefile`, workflows, `.claude/**`, `CLAUDE.md`, `conftest.py`, `cassettes/**`, `evals/**`, `data/**`, `pyproject.toml`, `package.json` or `.gitignore`. Not on it but worth noting: `tests/mcp/test_contract.py` and `services/mcp-catalog/tests/test_chk01_criteria.py`, the pins changed under stop 5.

(Saved note: in check 4, the reviewer's list of untouched files ended with the environment-file glob. It is written here as "environment files", because the repo's guard hook refuses that glob in shell text.)

---

# Triage (the session, 2026-11-26)

| # | Severity | Action |
|---|---|---|
| 1 | Medium | **Fixed.** The note is moved from the `search_products` row to the `get_product` row. `search_products` is back to its 361d396 text. The mistake came from a one-occurrence replace on a sentence that appears on both rows. |
| 2 | Medium | **Fixed.** Each fix was undone, its test file run, and the fix restored. Both logs, mutant then head, are kept for every fix; the table is in the PR body. The photo listing had no test, so `test_a_directory_or_a_dangling_link_named_like_a_photo_is_not_one` was added first. |
| 3 | Low | **Recorded** in the PR body: where each planned journey is covered, `Layout.tsx` folded into `App.tsx`, and `page=` dropped. |
| 4 | Low | **Recorded** in the PR body as the user's decision. |
| 5 | Low | **Recorded** in the PR body, with the reason for each suppression. |
| 6 | Low | **Recorded** in the PR body, and to appear in the gate report. |
| 7 | Low | **Recorded** in the PR body. N1 and N2 are low, so no further re-review was required. |
| Unconfirmed: the brief | – | The user's brief is in the conversation, not in the repo. Trial-build rule 2 ("no marketing badges or eyebrow labels") is covered by `shop.test.tsx`'s "carries no badge or eyebrow label" test and by the product page's badge test. |
