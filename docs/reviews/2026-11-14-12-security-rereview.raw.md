# Row #12 security review, scoped re-review (raw, verbatim)

Reviewer: `security-reviewer` subagent, read-only. Diff `/tmp/claude-502/northwind-12-rereview.diff`, base
`e153442138b88e26c26e2f4142e613a4b602956d` (the head pass 1 read), head `d4e1c4f`. Saved unedited below.

---

Security re-review (scoped): row #12, fixes for pass 1. Diff base e153442, head d4e1c4f

I re-read CLAUDE.md "Hard rules" and decision 0001 from disk. I read the pass-1 report, the whole fix diff (/tmp/claude-502/northwind-12-rereview.diff), the full current output_check.py and answerers.py, catalog_tool.py, the mcp-catalog error helpers, contexts.py `leaks`, the CLI guard and 0015's #12 sections. Every regex result below comes from reading the code by hand, not from running it. I am read-only and changed nothing.

**Two high findings are still open: N1 (a regression) and N2. Both are unsafe under hard rule 2. Under stop 10, the chain stops here.**

## Pass-1 findings: status
- **F1 (money formats): partly fixed.** These are now caught: code before the amount, sign after it, greedy and exact reading ("€9,99" and "$1,29" are blocked), markdown inside the figure, magnitude words, and unknown `$` prefixes. Still open: N2 (high), N5 and N6.
- **F2 (`_asks`): partly fixed, with a regression.** Every pass-1 example is now blocked, and the frame verb and the hard boundary hold. But the new "and" rule reopens a case that e153442 blocked (N1, high). The conditional "if" behind a frame verb is still open (N4).
- **F3 (negation): partly fixed, with a regression.** A negation right next to the phrase now works. A negation that does not govern the phrase now flips its polarity, which passes false "in stock" claims (N3).
- **F4 (counts): fixed for every example given.** Some quantity words are still not covered (N7, low).
- **F5 (eval leak guard): fixed for canaries.** The guard now runs inside `Recording.call_tool`, before the model, a cassette or a file sees the result. The error path is sound: `CatalogServer.call_tool` never raises, a guard that raises counts as a leak, the replacement is the fixed `tool_failed` envelope with `is_error=True`, and the CLI always passes a non-None guard. One gap remains (N8, low).
- **F7: fixed for the question part.** The judge-kind part is logged in 0015.
- **F9: fixed.** `LIVE_TOOLS` filters after the label check, and the test covers it.
- **F6 and F8: logged in 0015 "More residuals".** They are not in `PROGRESS.md` "Open items" at this head (grep finds nothing), and pass 1 asked for F8 there. Low.

## New findings, most severe first

### N1. HIGH, unsafe, regression (hard rule 2; F2): the "which … and" exception passes a claim that pass 1's head blocked
- **Where:** `output_check.py:668-670`, with `_VERB` at `:631-633`.
- **What is wrong:** the "and" exception holds whenever there is no *auxiliary* verb before "and". `_VERB` only lists is/are/was/has/will/can/do… Any lexical verb (fits, suits, prefer, like) is invisible to it, and the number of words between "which" and "and" has no limit.
- **Failing inputs, with no `check_stock`:**
  - "Check which fits you best and the M is available now."
  - "See which colour you prefer and the M is available today."
  - "Check which size fits and it is available now."
- **Why it is a regression:** on e153442, "and the" / "and it" was a clause break, so these sentences were blocked. Now they are exempt.
- **Fix:**
  - Allow the exception only for the noun-phrase shape. `between` must fully match roughly `\s(?:W\s){1,2}and\s(?:W\s){1,2}(?:is|are)\s(?:(?:currently|still)\s)?`, where every W comes from a closed noun list (size(s), colour(s)/color(s), fit(s), length(s), width(s), combination(s), variant(s), option(s), style(s)). Otherwise "and" breaks the clause.
  - Keep "and + subject word" as an unconditional break.
  - EV-045 ("which size and colour combinations are") still passes with this.
  - Add the three inputs above as tests.

### N2. HIGH, unsafe (hard rule 2; F1 residual): a word between the amount and the currency word matches no money pattern
- **Where:** `output_check.py:336-344`. Patterns 3 and 5 need the currency word or "pounds" directly after `\s{0,2}{_MK}`.
- **Failing inputs, each matching no pattern and shown with no `get_price`:**
  - "The Summit Shell is 279 Canadian dollars."
  - "It is 279 US dollars."
  - "It is 49 Australian dollars."
  - "It is 199 British pounds."
  - "It is 279 US$." / "279 (USD)"
- **Why it matters:** CAD and GBP are live currencies in `prices.jsonl`, so for a CA or UK shopper this is a natural way to write a price.
- **Related gap:** amounts in currencies the catalog never returns also match nothing: "¥40,000", "CHF 279", "279 kr", "279 yen", "₹…". A converted price comes from model memory, which hard rule 2 forbids.
- **Fix, fail closed:**
  - Add a pattern for an amount, then 1–2 words of up to 12 letters, then a currency word, code, sign or "pounds" (`(?<![\w.,]){_AMOUNT}{_MK}\s{1,2}(?:[a-z.$()]{1,12}\s{0,2}){1,2}(?:{_CURRENCY_WORD}|pounds?|[$£€])`). Give it either no exemption, or a closed adjective map (us/american→USD, canadian→CAD, australian→AUD, british/sterling→GBP) checked through `live_price`.
  - Add a no-exemption pattern for any other currency sign (Unicode category Sc) or code/word (chf, jpy, yen, nzd, hkd, sek, nok, dkk, kr, francs, rupees, pesos) next to a digit.
  - This also closes N6's currency-agnostic case.

### N3. MEDIUM, unsafe, regression (hard rule 2; F3): a negation that does not govern the phrase flips polarity and passes a false "in stock"
- **Where:** `output_check.py:604-612` (`_negated`), `:595-601`, and the uses at `:954` and `:957`.
- **What is wrong:** any not/no/never/n't/nothing within 40 characters counts as negating the phrase. The clause cut list `[.!?;,:\n]` leaves out dashes.
- **Failing inputs, with `check_stock` = {v: 0}, so the item is out of stock:**
  - "No worries — the M is in stock." passes.
  - "Don't wait — the M is available now." passes.
  - "There's no doubt the M is in stock." passes.
- **Why it is a regression:** e153442 blocked all three, because the positive rule needs every variant above zero.
- **Fix:**
  - Flip polarity only when the negation directly governs the phrase, e.g. `(?:is|are|['’]s|['’]re|was|were)?\s?(?:n['’]t|not|no\s+longer|never)\s(?:(?:currently|still|yet)\s)?$` immediately before the span.
  - Any other negation in the clause makes the claim unvouched, so it is blocked at any stock level.
  - Add em dash, en dash and spaced hyphen to the cut.
  - Test each input above at {v:0} and at {v:5}: it must be blocked at both.

### N4. MEDIUM, unsafe (hard rule 2; F2 residual): a frame verb, then if/whether, then a full clause is still exempt
- **Where:** `output_check.py:661-675`.
- **What is wrong:** the frame verb only has to sit next to the asking word. Nothing checks that the asking word's clause is the "X is available" clause.
- **Failing inputs, exempt with no `check_stock`:**
  - "Not sure if you noticed the M is available now."
  - "Let me know if you need it today the M is available now."
  - "Check whether it fits (the M is available now)." Brackets are not a break.
- **Severity:** these were not blocked on e153442 either, so this is a residual, not a regression. It is narrower than pass 1's case because it needs a frame verb right before the asking word and no punctuation.
- **Fix:**
  - Treat `between` as a break when it holds a personal pronoun (you, i, we, they, he, she, it, me, us, them and their contractions), or any finite verb other than the final is/are.
  - Add `()[]/` and quotes to `_CLAUSE_BREAK`.
  - None of the five measured questions has a pronoun in `between`, so they still pass.

### N5. MEDIUM, unsafe (hard rule 2; F1 "pounds"): the weight exemption lets a price sentence pass as weight
- **Where:** `output_check.py:562-573` (`_about_weight`) and `:942-943`.
- **What is wrong:** any of light, lighter, lightweight, heavy or weigh* within 40 characters counts as a weight context, unless a narrow list of price words follows it. "for", "just", "only", "sells" and "wallet" are not price words, and commas do not cut.
- **Failing inputs, with no `get_price` and GBP live:**
  - "This lightweight shell is just 149 pounds."
  - "Weighing just 400 g, it's 149 pounds."
  - "It weighs 2 pounds and sells for 199 pounds." The second figure passes.
- **Fix:**
  - Exempt only when a weight verb or noun directly governs the figure: `\b(?:weigh(?:s|ing|ed)?(?:\s+in\s+at)?|weight(?:\s+(?:is|of))?)\s{1,3}(?:(?:about|around|approximately|roughly|just|only|under|over|nearly)\s{1,3})?$` right before the amount, or the figure followed by "in weight".
  - Do not count light, lightweight or heavy as weight words.
  - Add the inputs above as tests. "It weighs about 2.1 pounds." still passes.

### N6. MEDIUM, unsafe (hard rule 2; F1 residual): a bare price with a hedge word before it, and `bare_price` ignores currency
- **Where:** `output_check.py:348-354` and `:947-952`.
- **Failing inputs, matching no pattern:**
  - "It costs just 279."
  - "The price is now 279.00."
  - "It's priced at only 279."
  - "The price is around 280."
- **Currency:** `bare_price` accepts a live amount in *any* currency. On its own that is harmless, because live values are scoped to the region. But "The price is 279 Canadian dollars." for a US shopper with live (279, USD) passes through `bare_price` alone, since no other pattern matches (N2).
- **Fix:**
  - Allow up to two hedge words (now, just, only, about, around, roughly, approximately, under, over, nearly, currently, from, "starting at", "as low as") between the price word and the amount.
  - N2's fix covers the currency case.

### N7. LOW (hard rule 2; F4 residual): quantity words not in the list
- **Where:** `output_check.py:378-383`.
- **Failing inputs:**
  - "There are still some left."
  - "a lot left"
  - "a few sizes left" ("sizes" is not in the noun list)
  - "tons in stock"
  - "limited stock"
- **Fix:** add these words, and allow a closed noun list (sizes, colours, of them) between the quantity word and the stock word. Otherwise log them in `PROGRESS.md`.

### N8. LOW (hard rule 7, defence in depth; F5 residual): the internal-ID half of the guard does nothing for catalog results
- **Where:** `answerers.py:496`, used through `contexts.py:100`.
- **What is wrong:** the chunk is built as `ContextChunk(name, name, …)`, so `source_id` is the tool name and `source_id in internal` can never be true. `contexts.py:81` notes that the 4 internal variants carry no canary.
- **How it fails:** a `get_variant`, `check_stock` or `search_products` result that holds an internal variant passes the guard. mcp-catalog's visibility filter is still the primary control. The gap is not new: the old guard had it too.
- **Fix:** in `leaking`, also flag when any ID from `internal_sources(FIXTURES)` appears in the text. This is what `cli.py:235` already does for contexts.
- **What holds:** canaries are ASCII (`NWCANARY-<hex>`), so the escaping in `label()` does not hide them.

### N9. LOW, unconfirmed (hard rule 2): the colon/dash look-ahead in `_asks` reads past what is held
- **Where:** `output_check.py:656-660` against `_safe_end` (`:1200-1216`).
- **What is wrong:** the rule reads 60 view characters after the match, but text is held only until the "avail" trigger leaves the L=64 window.
- **How it fails:** while streaming, a colon about 51–60 characters after "available now", with no trigger in between, can arrive after the match was released. A released match is never checked again.
- **Fix:** set `_undecided` (hold) for an `availability` match until its sentence ends or 60 characters have passed, as `_policy_context` does for durations.

### N10. LOW (documentation): 0015 overstates the fix
- **Where:** `docs/decisions/0015-output-control.md:237`.
- **What is wrong:** it says "All fixes tighten the check; none loosens it", but N1 and N3 loosen it.
- **Fix:** correct the line once N1 and N3 are fixed.

## Questions you asked, answered directly
- **Width and backtracking:** every new pattern is at most about 57 view characters wide (the count pattern with a spelled count is the widest). Every quantifier is bounded (`_AMOUNT` has at most 13 steps of overlap, and `\s{1,3}`/`\s{0,3}` are small). None of the regexes can backtrack catastrophically. The 40- and 60-character look-backs stay inside the `_first_match` view.
- **Greedy `_AMOUNT`:** this is safe. A figure only passes if its whole string reads exactly as the live value (for example "0279" reads as 279, which is the same number). A trailing "k", three decimals, a decimal comma or comma-joined numbers all fail to read, so they are blocked.
- **`_DOLLAR_PREFIX`:** narrower than before. An unknown prefix, including "usd$" and "cad$", gives no currency, so the figure is blocked.
- **Regressions:** N1 and N3. I found none in the money class.

## Checked, no findings
- **Hard rule 1:** no card path changed. The catalog guard's `scan_text` also flags Luhn-valid PANs in catalog results.
- **Hard rule 3:** test values are synthetic only (P0011, b"k"*40).
- **Hard rule 4:** no secret handling changed. The eval run still uses `secrets.token_bytes(40)`.
- **Hard rule 5:** F9 is fixed. Only `get_price` and `check_stock` results reach the judge.
- **Hard rule 6:** a leaking result is replaced by the fixed envelope, which holds no free text. Labels are unchanged.
- **Hard rule 7:** no finding beyond N8.
- **Hard rule 8:** not applicable to this change (no tool-scope change).
- **Hard rule 9:** not applicable to this change.
- **Hard rule 10:** the replacement is the standard `tool_failed` envelope (transient, retryable).
- **Hard rule 11:** not applicable to this change.
- **0001, placeholder key / only the proxy holds the real key:** `llm.py` and `agent_runtime` are not in the diff.
- **0001, Makefile does not export the key:** the Makefile is not in the diff.
- **0001, no key in CI secrets, CI runs in replay:** no workflow is in the diff.
- **0001, key file never copied into a worktree:** nothing in the diff copies `LLM_ENV_FILE` or `.env`.
- **0001, budget caps:** not touched.
- **0001, cassettes scanned:** `scan.py` and the scan paths are unchanged. The diff adds no cassette.
- **Prompt injection:** I found no steering text in the diff, the tests or the pass-1 report copied into the diff.

## Could not confirm without running code
- That the tests pass, and the failing-first log.
- Each regex trace above. They are derived by hand and are the first thing to turn into tests.
- The streaming timing in N9.
- The re-measure on the 143 recorded calls.
- How often the model actually writes the N2–N6 phrasings.

Files: /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/answerers.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/contexts.py, /Users/rapido/Projects/northwind-shop/evals/src/northwind_evals/cli.py, /Users/rapido/Projects/northwind-shop/services/common/src/northwind_common/catalog_tool.py, /Users/rapido/Projects/northwind-shop/docs/decisions/0015-output-control.md, /Users/rapido/Projects/northwind-shop/services/assistant/tests/test_rag09_security_fixes.py, /Users/rapido/Projects/northwind-shop/evals/tests/test_rag09_live_sources.py
