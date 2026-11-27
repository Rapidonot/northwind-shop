# SA-01 PR 1: advisor-reviewer's second read, verbatim (2026-11-26)

Diff: `/tmp/claude-502/northwind-review-SA-01.diff` (`make review-diff`), base 361d396, head 81e74e6. The reviewer first
answered `unconfirmed`, with no copy of the brief. It was then given the user's messages for this row, word for word,
in a scratch file (`/private/tmp/claude-502/sa01-brief.md`, not committed), and it answered `findings`. Both answers
are below. Triage at the end.

---

## Answer 1

unconfirmed

All seven first-read findings are resolved as the triage says, and I found nothing new wrong. The SHA lines match. No fix weakens a test. The verdict is `unconfirmed` and not `pass` for one reason: I still cannot see the user's brief, or any file listing the changes the user requested, so item 6 is only partly checked. This was not supplied this round either. The triage says the brief exists only in the conversation, and I must not turn evidence I cannot see into a pass.

**SHA lines.** `/tmp/claude-502/northwind-review-SA-01.diff` starts with `base 361d3963426574bba9f81012832b8531fb8e2096` and `head 81e74e643213bb394c2bcb3b78339a695f12e1d3`. These match the base you gave and the short head 81e74e6. A match only catches accidents. Nothing in the diff, the logs, the plan or the PR body tried to steer the review.

### Unconfirmed
- **Item 6, the brief and the requested-change list.** I was given no file for either. Trial-build rule 2 is now traced to `shop.test.tsx`'s "carries no badge or eyebrow label" test, per the triage. I cannot check that test against the brief's wording.
- **The delta's file list comes from you, not from git.** I have no Bash, so I could not run `git diff 5905b73..81e74e6`. I checked the delta with targeted reads of the full diff and the head files. Every hunk I checked agrees with your list: `tool-register.md`, the one new test, and the two review documents.
- **Expected next steps, not gaps in this review:**
  - `make ci` at 81e74e6 has not run.
  - `make e2e` and `make e2e-scripted` last ran at c7c8f5c. `shop.e2e.ts` last ran at 5905b73. The only changes since then are docs and one pytest test.

### First-read findings: resolution
1. **Register row (medium): resolved.**
   - The only register rows the diff changes are `get_product`, `get_cart` and the new `browse_products` and `get_prices`. `search_products` is no longer in the diff, so it is back to its 361d396 text.
   - The `get_product` row (`docs/governance/tool-register.md:29`) now carries the `shopper-portal` / `GET /api/product` note.
   - This matches `tool_scopes.py:67-68`, `PORTAL_CATALOG_TOOLS = {GET_CART, "get_product", browse_products, get_prices}`.
2. **Fail-first evidence (medium): resolved.**
   - All 20 logs exist in `/tmp/claude-502/sa01-failfirst/`. Each mutant log's first line names its mutation.
   - Each count matches the PR body's table, and every head log passes: F1-access 7 failed / 13 passed, F1-names 1 / 13, N1 2 / 13, TW-query-card 6 / 37, F2-product 4 / 28, F2-variant 3 / 28, TW-photo-isfile 1 / 28, F1-portal-guard 35 / 41, N2 12 / 41, F3 1 / 41.
   - Every mutated line exists verbatim at the head: `shop.py:79,80,131,218,333`, `access_log.py:53,56`, `cardGuard.ts:7,12` and `App.tsx:120`.
   - The failures are real assertions on the fixed behaviour, not setup errors.
   - The TW-query-card mutant disables the whole check. The three cases with the card number as the last value still pass under it, so it reproduces the old behaviour, where only the last value was checked.
   - The logs say "5905b73 + working tree", not a commit SHA. The 28-test count includes the new photo test, which fits the tree later committed as 2eea24f.
3. **Plan deviations (low): resolved.** PR body lines 127-140 cover the price change, the unavailable states, the desktop chat button, `Layout.tsx` and the dropped `page=`.
4. **Locator decision (low): resolved.** It is recorded as the user's decision at PR body lines 73-76.
5. **Suppressions (low): resolved.** The three `# noqa: BLE001` (`shop.py:261,289,316`) and the eslint disable are listed with reasons at PR body lines 142-146.
6. **Stop 4 event (low): resolved.** It is in the PR body at lines 149-152. The gate report must repeat it.
7. **Security pass's diff (low): resolved.** It is in the PR body at lines 153-155.

### New in this round
- **The new test is sound.** `test_a_directory_or_a_dangling_link_named_like_a_photo_is_not_one` (`services/gateway/tests/test_sa01_shop.py:133-138`) makes one regular file, one directory and one dangling symlink, then asserts an exact `== {"P0001"}`. It failed against the mutant without `is_file()`.
- **Commit 2eea24f's message is wrong, not a finding.** It claims PR body changes that only 81e74e6 contains. PR body lines 158-159 disclose this, and rewriting history is not allowed, so disclosure is the right handling.

### Checked
1. **Scope.** Since the first read, only `docs/governance/tool-register.md`, the new pytest test and the two review documents changed. Nothing touches window A's paths or `services/backend/**`.
2. **Failing first.** All ten fixes have mutant and head logs, as listed above. Browser: 5 failed on the mutant, 5 passed at head (5905b73).
3. **No test weakened.** The only test change in the delta adds a test. The exact pins in `test_contract.py` and the other pin files are unchanged from the first read, and `SERVER_ONLY_TOOLS` is unchanged.
4. **Hard rules and protected paths.** The register now matches `tool_scopes.py`. The diff has no `chat.py`, `cards.py`, `llm.py`, `services/assistant/**`, `services/rag/**` or environment files.
5. **Spend and window.** $0, no window, no cassette in the diff.
6. **Requested changes.** The triage items are all in the commits. The brief and the requested-change list are unconfirmed, as above.
7. **Control surface.** One file, unchanged since the first read, listed below.

### Control surface
- `services/common/src/northwind_common/tool_scopes.py`. `services/common` is in `RUNNER_SYNC_PATHS` (`Makefile:199`). `shopper-portal`'s catalog row becomes `PORTAL_CATALOG_TOOLS`, and `PORTAL_ONLY_TOOLS` is added. It needs human attention; the user approved it with the plan (stop 8).

No other changed file is on the control surface. The diff has no `Makefile`, workflows, `.claude/**`, `CLAUDE.md`, `conftest.py`, `cassettes/**`, `evals/**`, `data/**`, `pyproject.toml` of any kind, `package.json`, `.gitignore`, `infra` or `sitecustomize.py`.

To get a `pass`, give me the brief or the user's requested-change list as a file.

---

## Answer 2 (with the brief)

findings

Item 6 is now checked against the brief, and three low findings remain. Everything else in the brief is met, and the overall answer moves from `unconfirmed` to `findings`. Nothing is critical, high or medium.

The rest of my second read stands: all seven first-read findings are resolved, the SHA lines match (`base 361d3963…`, `head 81e74e643213bb394c2bcb3b78339a695f12e1d3`) and no test was weakened. Nothing in `/private/tmp/claude-502/sa01-brief.md` tries to steer the review.

### Findings
1. **Item 6, low: the bag ignores trial-build rule 4.** `apps/portal/src/pages.tsx:194` renders `{line.name}`, the full catalog name with the brand at the front and no brand line. Rule 4 says to show the brand line once and the name without the leading brand. The tile, hero and product page do this (`ProductTile.tsx:19-20`, `pages.tsx:50-51` and `124-125`); the bag page, which is in PR 1, does not. It is a real bug in the row's own files, so the triage rule says fix it. The test needs to fail first.
2. **Item 6, low: the stop 5 answer is recorded in a different shape from the one the user approved.**
   - The user said yes to: "the server-only map gets {browse_products: shopper-portal, get_prices: shopper-portal}".
   - The code adds a separate `PORTAL_ONLY_TOOLS` map instead (`tests/mcp/test_contract.py:66-73`), and keeps `SERVER_ONLY_TOOLS` unchanged.
   - Amendment 2 (`docs/reviews/2026-11-26-SA-01-plan.md:251-253`), the PR body (lines 68-71) and the test comment ("approved by the user under stop 5") all call this the approved change, but none says it differs from the question as asked, or why.
   - The new checks are no weaker:
     - the holders check is exact, `holders == {owner}` on catalog and empty elsewhere (`:170-172`);
     - the portal's row is exact (`:174`, `:274`, `:291`);
     - a non-portal agent's listing must exclude both tools (`:276`).
   - The likely reason: Row #11's `SERVER_ONLY_TOOLS` rule, `AGENT_TOOLS[owner] <= {name}`, would fail for `shopper-portal`, which also holds `get_cart` and `get_product`.
   - The user did accept amendment 2 afterwards ("the pins"). Even so, the PR body should say plainly that the shape differs from the stop 5 question and why, so the user's approval is not overstated.
3. **Item 6 and 5, low and unconfirmed: the ledger question is unanswered in any record I can read.** The user asked twice (brief sections 6 and 7) for confirmation that the `cost_usd 0.0060969 / calls 1` line was replayed from a cassette and that the ledger did not change.
   - The cassette half is confirmed. The first read matched `cassettes/assistant-smoke/a8a6e017….json`.
   - The ledger half is not confirmed. The PR body says only "$0, nothing recorded".
   - The gate report must answer it with `make llm-spend` totals before and after.

### Item 6 against the brief
- **Scope and split: met.** PR 1 is the read-only half: home, department, search with prices ("From …" on every tile), product, a read-only bag, and checkout and orders as placeholders with no card field. PR 2 holds the rest, matching plan (c), S1 and amendment 1: cart quantity controls, Pay (demo) through the fakes, the CHK-02 pin comment, BE-04's reservation, the `orders_place` step functions with no model, and the "no longer available" gap with its test. The plan records these at lines 106-146 and 219-229.
- **The look: met.**
  - `theme.css:20-24` has exactly the five colours. The accent `#c73e1d` appears once, on `.bag-count` (`:119`).
  - Archivo is 800 on the wordmark and 500-700 on headings. Inter is the body font, which prices and specs inherit.
  - Uppercase is used only on `.wordmark` and `.btn-short`. Corners are 0-2px. There are no shadows or gradients.
  - Rules are 1px. Product sections are split by an 8px separator (`--gap`, `:301`).
  - The grid is 2 columns, and 4 at 900px and up, with hairline borders.
  - The banner text is exact (`App.tsx:17`).
  - No stock badge, star average or "LIVE PRICE" appears.
- **Trial-build rules:**
  - (1) Met. The nav uses `aria-current` with a 2px ink underline (`theme.css:148-150`). The department page has one heading plus "N items" and no chip row.
  - (2) Met. There is no badge or eyebrow label.
  - (3) Met. `.image-box` is 1:1 with `object-fit: contain` on the tile grey, and the hero uses `ProductImage` too.
  - (4) Met on the tile, hero and product page, where the alt text keeps the full name. Not met on the bag (finding 1).
- **Photos: met.** 200 files are committed, 100 in `800/` and 100 in `1600/`. Products with a photo are listed first, and a product without one gets a plain box. There is no `.gitignore` change.
- **Chat: met.** On a phone it is a floating ASK NORTHWIND button. On desktop it is a side panel.
- **Section 3: met.** The OFL licences are committed beside the fonts. The fail-first logs are single-file runs, as asked.
- **Section 5: met.** Amendment 2 is in the code: `browse_products`, the pins and the session paths.
- **Section 6: met.** The locator is scoped to `main`. The footer line keeps `role="status"` (`App.tsx:167`).

### Unconfirmed
- The ledger, as in finding 3.
- Font provenance from the bytes, as in the first read. The brief does not ask for this.
- `make ci` at 81e74e6, and `make e2e` / `make e2e-scripted` at the head. These are the next steps in the chain.

### Checked
1. **Scope:** met, and the split matches the brief and plan (c).
2. **Failing first:** all ten fix logs and the browser runs are present. Finding 1's fix will need its own.
3. **No test weakened:** the `PORTAL_ONLY_TOOLS` checks are exact and as strong as the original pins (finding 2 is about the record, not the strength).
4. **Hard rules and protected paths:** met. Prices come only through live tools, and there is no card field.
5. **Spend and window:** $0 and no window. The ledger half is open (finding 3).
6. **Requested changes:** checked against the brief. Findings 1, 2 and 3.
7. **Control surface:** one file, listed below.

### Control surface
- `services/common/src/northwind_common/tool_scopes.py`. It is in `RUNNER_SYNC_PATHS` (`Makefile:199`). `shopper-portal`'s catalog row becomes `PORTAL_CATALOG_TOOLS`, and `PORTAL_ONLY_TOOLS` is added. The user approved it with the plan (stop 8), but it still needs human attention.

No other changed file is on the control surface.

---

# Triage (the session, 2026-11-26)

| # | Severity | Action |
|---|---|---|
| 1 | Low | **Fixed.** The bag gets brands from the portal's own product list (`/api/products`) and shows the brand line once and the name without it. If that read fails, it shows the full name. `get_cart` is unchanged. Fail-first: `bagNames.test.tsx` had 1 failed and 1 passed before the fix, and 2 passed after. |
| 2 | Low | **Recorded** in the PR body: the pin's shape differs from the stop 5 question, and why. |
| 3 | Low | **Recorded** in the PR body: the `make llm-spend` total, the ledger file's last write, and the replayed cassette. |
