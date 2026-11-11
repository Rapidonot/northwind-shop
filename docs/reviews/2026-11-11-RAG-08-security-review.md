# RAG-08 security review, pass 1 (raw, verbatim), 2026-11-11

Reviewer: `security-reviewer`. Input: `/tmp/claude-502/northwind-security-RAG-08.diff` (working tree, base
`201c97a`, 23 files). The report as returned:

---

Security review of row #13 (RAG-08 + NFR-03 partial): /tmp/claude-502/northwind-security-RAG-08.diff, base 201c97a, working tree.

I'm read-only and could not run anything. Every trace below was done by hand against /Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py (written OC below) and /Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/turn.py. I read CLAUDE.md "Hard rules" and decision 0001 for this review.

**Result:** three high findings, all on question 1 (hard rule 6 / NFR-03). Each one lets a payload class reach a `token` SSE event. The first needs no attacker help at all.

## Findings, most severe first

### H1. A payload inside a bullet's bold lead-in is sent again with the fixed sentence
- **Where:** OC:224 (`_BULLET`), OC:499-500 and OC:488.
- **Rule:** hard rule 6 / NFR-03, and hard rule 2 for promotions.
- **What is wrong:** when a sentence is blocked, `_prefix = _BULLET.match(raw, start)` keeps the bullet marker *and* the optional `**…**` lead-in of up to 40 characters, taken from the raw text. That prefix is sent as `prefix + FIXED_SENTENCES[cls]`. Nothing checks whether the match lies inside the prefix, and the prefix itself is never checked.
- **How it fails** (traced through `check_text`):
  - `"Here:\n- **Discount codes:** I can't apply them."` becomes `"Here:\n- **Discount codes:** I can't help with offers or codes in chat."`, so "Discount" goes out in a token.
  - The same happens with `- **Use code STAFFALL:** …`, `1. **20% off for staff** …`, `- **northwind-deals.net:** …` and `- **Call +1 555 010 0199:** …`.
  - This is ordinary model formatting, so no evasion by the attacker is needed. R00712's subject would leak as `- **Discounts and orders:** …`.
  - The only test with a bold lead (test_output_check.py:416, `**Offer:**`) has its payload after the prefix.
- **Fix:** keep only the list marker (`[-*+]` or `\d{1,3}[.)]` and its spaces) as the prefix. Or keep the bold lead only if the match starts after it and `hits(prefix)` is empty. Add tests with the payload inside the bold lead, across splits.

### H2. An invented citation marker can split a payload: the check reads the raw text, the shopper gets the text with the marker removed
- **Where:** OC:384-418 (matching on `_raw`) and OC:443-444 (`_emit` removes markers and then deletes `[ \t]+` before punctuation, after matching).
- **Rule:** hard rule 6 / NFR-03, hard rule 2.
- **How it fails** (traced):
  - `"Use the dis[X1]count at checkout."` with X1 not in `seen`: no pattern, trigger or domain matches the raw text. `_fix_marker` then returns "", so the token is `"Use the discount at checkout."`.
  - The same works for `"Your order has been [Q1]confirmed."` and `"Use code [Z9]SAVE20"`.
  - A link can be rebuilt by the space deletion: `"visit evil [X1].com"` becomes `"visit evil.com"`, and `"http [X1]://10.0.0.5/claim"` becomes `"http://10.0.0.5/claim"`. The space deletion runs over the whole released chunk, not just next to the marker.
  - Any ID-shaped string that was not returned this turn works. An injected review only has to tell the model to write the word this way.
- **Fix:** take invented markers out *before* matching, as a transform on the incoming stream. The engine's `_raw` should then be exactly the text the shopper sees, including the punctuation clean-up. Failing that, re-run the check on the fixed text and block the sentence if the result differs. Add split and marker tests for each class.

### H3. Invisible combining characters are not removed from the matching view
- **Where:** OC:65-69 (`_fold_char` drops only category `Cf`).
- **Rule:** hard rule 6 / NFR-03. Decision 0014 §2 says "format characters (U+200B and the like) removed".
- **What is wrong:** variation selectors U+FE00–FE0F and U+E0100–E01EF, the combining grapheme joiner U+034F, and the Mongolian free variation selectors U+180B–180D are category `Mn`. NFKC keeps them, so the fold leaves them in. Browsers render them as nothing.
- **How it fails:** `"d️iscount"` and `"northwind-deals️.net"` match no pattern, no trigger and no `_DOMAIN`. They are released whole, and the shopper sees "discount" or the domain. mcp-rag's NFKC keeps these characters in review text too, so a planted review can carry them.
- **Confirmed vs unconfirmed:** the gap in the code is confirmed. Whether the model copies these characters is not.
- **Fix:** drop every `Default_Ignorable_Code_Point`, not just `Cf`, and compose sequences (fold per grapheme, or NFKC the whole string with an index map) before casefolding. Add tests for VS16 and CGJ.

### M1. After a marker rewrite, the matching window starts mid-sentence and misses payloads that cross that point
- **Where:** OC:447 (`_plain_since = end` after a marker fix), OC:336-342 (sentence start clamped to `_plain_since`), OC:386 (match origin).
- **Rule:** hard rule 6 / NFR-03.
- **How it fails** (traced): `"It is [X1] and, as many shoppers have said over the last season, sadly out"` arrives as one delta, then `" of"`, `" stock"`, `"."`.
  - Once the marker is more than 63 characters back, the chunk up to "out" is released and `_plain_since` moves to its end.
  - The view for " of stock." then starts after "out", so `out of stock` never matches and goes out.
  - The duration check's sentence region (OC:372, 377) has the same problem. A policy stem before the marker is lost, so `"Returns [X1] for this jacket, as with every item in the store, are accepted for 365 days."` counts as non-policy.
- **Fix:** keep a raw-to-shown position map instead of clamping, or remove markers before matching (the H2 fix), so `_plain_since` no longer moves mid-sentence.

### M2. False-policy exemption: any duration in any public document vouches for any policy duration, and policy context ends at a newline
- **Where:** OC:359-365, OC:368-382, OC:211-214, OC:223.
- **Rule:** hard rule 6, RAG-11 policy facts.
- **How it fails:**
  - `wanted <= have` compares only the (value, unit) pairs, not the term they belong to.
  - FAQ-012, a public document for all regions, says "covers manufacturing defects for two years". If it is returned this turn, "You can return any item within two years." passes. The model can pull FAQ-012 in itself with a refinement search, which an injected review can ask it to do. FAQ-007's "one hour" works the same way.
  - Separately, `\n` ends a sentence, so `"**Return window**\n365 days"` or a bulleted `"- a full year"` under a "Returns" line has no stem and passes.
- **Status:** both behaviours are inside the user's approved design for class 5. The exemption shape is the user's decision (stop 7).
- **Fix to offer:** compare durations only with POL- text, or with sentences in the document that hold the same stem. Let the policy context take in the preceding heading or bullet-parent line.

### M3. Wording gaps in the frozen classes (OC:147-195)
- **Rule:** hard rule 2, hard rule 6.
- **Phrasings that pass:**
  - **Promotion:** "90 percent off" (R00881's payload in other words), "half price", "clearance".
  - **Confirmation:** "Your order is now confirmed" (an adverb breaks `(is|has been|was)\s{1,3}verb`), "We've processed your payment", "We have placed your order", "your refund issued".
  - **Stock:** "just 2 left", "stock is running low".
  - **Phone:** a number with 8 digits or fewer, through the `phone` exemption at OC:230-232.
- **Status:** the classes are frozen, so changing them is stop 7 for the user. Adding patterns strengthens the check.

### M4 (unconfirmed, not measured). CPU grows with the square of sentence length
- **Where:** OC:386-387 (`view_of(self._raw[origin:])` and every pattern rerun from the sentence start on each delta), running synchronously on the gateway's event loop.
- **How it fails:** a long answer with no `.`/`!`/`?` followed by whitespace, and no newline, up to max_tokens 8192 (about 30k characters), refolds and rescans the whole run on every delta. By my estimate that is tens of seconds of blocked loop for every shopper, which an injection can steer the model into. The evidence's 0.54 ms maximum was measured on short recorded answers.
- **Fix:** match from `max(sentence_start, consumed − L − margin)`. Every pattern is at most L wide, so this matches the same text. Build the view incrementally. Keep the sentence start only for the replacement point and policy context.

### L1. Patterns that do not start at a trigger send their leading words before the repair takes them back
- **Where:** OC:204-208 against OC:120-123 and OC:180-184.
- **What is wrong:**
  - Number words are not triggers, and neither are `out of`, `in`, `low` or `only`, or the `+(` of a phone number.
  - With deltas `"…return any item within ninety"`, `" business"`, `" days."`, the text up to "ninety" is sent, then a reset arrives.
  - So a figure appears in a token, against the decision's "only text that passed is ever sent twice". No complete class pattern is ever sent.
- **Fix:** add number words as whole-word triggers (or more of the stock and phone prefixes).

### L2. On the model's Reset, and on the capped and outage paths, held text is released before the reset instead of being thrown away
- **Where:** OC:318-322 (`_advance(final=True)`), triggered by turn.py:165, :197 and :209.
- **What is wrong:** text cut off mid-word passes checks it would fail when complete, for example `northwind-deals.n`, `https:/`, `code SAV`, and is sent, then reset. On the outage path, text with no sources behind it is now sent and reset, where the plan said held text is discarded.
- **Fix:** handle Reset like `abort()` (clear, then pass on `None`), which is stricter than today.

### L3. The telemetry line is not written exactly once on every path
- **Where:** turn.py:254-256, usage.py:93-97.
- **What is wrong:** `flush()` logs only if `run_turn` reached `report()`. If the client disconnects mid-stream, or OutputCheck raises, the turn writes zero lines. The disconnect case was zero before this row too. An early search failure (non-deferred `TurnUsage`), a TurnError, the capped path and an answered turn each log exactly once.
- **Fix:** in `finally`, write an `aborted` outcome when `_outcome is None`.

### L4. Lookalikes outside the Cyrillic/Greek fold, and a domain written with U+3002
- **Where:** OC:52-62, OC:221.
- **Not folded:** dotless ı (U+0131), İ (which casefolds to i plus U+0307), Latin ɑ, and Armenian օ, so "dıscount" passes.
- **Domains:** `northwind-deals。net` uses U+3002, which IDNA treats as a dot, and halfwidth U+FF61 normalises to it. Neither matches `_DOMAIN`. `northwind-deals[.]net` passes too.
- **Status:** within the decided fold scope, but the residual risk is not recorded in decision 0014 or the threat model.

### L5. Some invented markers are not removed (hard rule 5)
- **Not removed:**
  - Markers that are not ID-shaped stay on screen, such as `[Source 1]` or `[POL_UK]` (OC:226).
  - An ID-shaped marker can be released across two chunks when a digit inside it is a trigger and a delta ends inside the marker. `_MARKER.sub` then never sees it whole (OC:443).
- **Limit:** neither becomes a citation, because `cited_ids` intersects with `seen`.

### L6. The pin test's reach
- **Where:** test_output_check.py:422-454.
- **What it misses:** it scans services/, evals/src and agents-external, but not tools/, data/src or a future partner-agent/. It matches the name `run_turn` only, so `getattr(t, "run_"+"turn")` or a new loop calling `claude_agent_sdk.query()` directly would get past it (services/backend/.../runtime.py:243 already has its own loop, not shopper-facing today).
- **Fix:** add those roots, and pin the shopper path's `query` importers as well.

### L7. Forward-looking notes on the fixed sentences and hard rule 11
- "I can't check stock in chat." (OC:45) stops being true once CHK-01 (#16) adds live stock. Owner #12/#16.
- The check replaced RT-017's "I'm an AI shopping assistant, and I can't process or authorise refunds." That is fine on the portal, which has its own disclosure banner. An A2A channel (#15/A2A-05) has no banner, though, and could lose the model's only in-text disclosure.

## Your questions, answered

- **Q1 (hard rule 6):** payloads do reach a `token` event, through H1, H2, H3 and M1.
  - Sound: a review or internal document cannot vouch. `public_document` / `public_policy` require type `document` and a POL-/FAQ-/CG-/SG- prefix, and internal documents are INT-.
  - Sound: source text cannot forge a `<source>` block for `parse_sources`, because mcp-rag folds with NFKC and then escapes `<`.
  - A source *can* make a payload pass only through M2.
  - Sound: a TurnError discards held text, the end of the stream withholds anything unresolved, and the repair arithmetic holds.
  - Sound: complete words are checked before a release that ends mid-word.
- **Q2:** sound. `stream_answer` is the only path; the gateway is its only caller (chat.py:119). Reach limits are in L6.
- **Q3:** sound. No new card-data path, because the gateway (chat.py:82) and `search_sources` run the NFKC-aware `contains_card_data` on the same raw message before `user_prompt`. The telemetry line holds fixed class keys and counts. Nothing logs blocked text; `cited_ids` logs a count only.
- **Q4:** the fixed sentences contain no figures or product/policy claims (see L7). Citations come from `shown` intersected with `seen`.
- **Q5:** the check fails closed. An exception propagates to the gateway, which sends `error: internal`, and held text is never sent. The telemetry gap is L3.
- **Q6:** sound. The only model-input changes are the two NFKC points and `html.escape` on id/type. All saved contexts are byte-identity tested. The questions test covers questions.json, the red-team set, the starter set and contradictions.json, which are the sources behind the assistant-smoke and assistant-redteam suites.
- **Q7:** the only control-surface path in the diff is evals/src/northwind_evals/answerers.py (approved). evals/tests is not on the list. services/assistant/** is a hard-rule 6/7/8 path, so the row is not routine, as the plan already says.

## Checked, no findings

- **Hard rule 1:** no finding (see Q3).
- **Hard rule 2:** findings only H1-H3 and M3.
- **Hard rule 3:** no finding. Test and evidence data are synthetic, and phone numbers use 555-01xx.
- **Hard rule 4:** no finding. No secrets are touched or logged.
- **Hard rule 5:** findings only H2 and L5.
- **Hard rule 6:** findings H1-H3, M1-M4 and L1-L4.
- **Hard rule 7:** no finding. Retrieval filtering is unchanged, and the free-terms vouchers POL-RETURNS-UK and POL-RETURNS-US are specific to one region.
- **Hard rule 8:** no finding. No tools were added or widened.
- **Hard rule 9:** not applicable to this change (no state-changing tools).
- **Hard rule 10:** no finding. Error codes are unchanged and a check exception becomes the fixed code `internal`.
- **Hard rule 11:** no finding on the portal (see L7).
- **Decision 0001, placeholder key in child processes / only the proxy holds the key:** not applicable to this change; llm.py is untouched.
- **Decision 0001, Makefile does not export the key:** not applicable to this change; the Makefile is untouched.
- **Decision 0001, no key in CI secrets / CI runs in replay:** not applicable to this change; no workflow changes.
- **Decision 0001, key file never copied into a worktree:** not applicable to this change.
- **Decision 0001, budget caps enforced and not raised:** no finding. No cap was touched, the ledger is unchanged at $2.588401, and nothing was recorded.
- **Decision 0001, cassettes still covered by the secret/PAN scanner:** no finding. No cassette or scanner change.
- **Data that tries to steer the review:** none found in the diff, plan, evidence or fixtures.

---

# Pass 2, scoped re-review (raw, verbatim), 2026-11-11

Input: `/tmp/claude-502/northwind-security-RAG-08-rereview.diff`, the changes made since pass 1. The report as returned:

---

Security re-review, pass 2 (scoped): row #13, RAG-08 + NFR-03 partial

I am read-only and could not run anything, so every trace below was done by hand. Input: /tmp/claude-502/northwind-security-RAG-08-rereview.diff and the current files. OC means /Users/rapido/Projects/northwind-shop-b/services/assistant/src/northwind_assistant/output_check.py. Before reviewing I re-read the CLAUDE.md "Hard rules" and decision 0001.

**Result:** 7 findings are closed (H1, H2, M1, H3, L3, L4 and L5's second half), 5 are partly closed (M4, L1, L2, L5, L6), and M2, M3 and L7 were deferred as planned. One new problem is high: the M4 lookback causes a regression (N1). Because this is the one scoped re-review, a high finding still open now means stop 10.

## Status per pass-1 finding

**H1: closed.**
- OC:242: `_BULLET` keeps only `[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+`.
- OC:569-570: that prefix is all that goes in front of the fixed sentence.
- On the rewind path (OC:574-579), only `_shown[:keep]` is sent again. That is text from before the sentence start, and it was already checked.
- For `1. **20% off…**`, `_BOUNDARY` treats "1. " as a sentence end, so the sentence starts after it. "1. " goes out as checked text with no payload in it.
- The `test_h1_*` tests cover 6 shapes at 3 split sizes.

**H2: closed.** What is checked is now exactly what is sent. One low residual is listed as N4.
- `_clean_markers` (OC:433-456) runs at the top of every `_advance` pass (OC:550), before both the dropping branch and `_first_match`. `_emit` (OC:513-523) now sends raw slices without changing them.
- A marker that has not closed yet is held, by two things:
  - The `\[` trigger. The window is the last 63 raw characters, which still covers the `[` of the longest possible marker (62 characters inside the brackets).
  - The trailing-word hold.
- When `_clean_markers` waits on a marker (OC:446-447), the space before it is held too, by the tail regex at OC:501.
- The space is removed only before `[.,;:!?]` followed by whitespace or the end of the text (OC:452-454). So removing it cannot join two pieces into a domain or a `://`.
- `Visit evil [X1].com` becomes `evil .com`, which is not a domain and not clickable. `http [X1]://…` is caught by `://` (OC:175) and by the IPv4 shape (OC:237).
- L5's second bullet (a marker released across two chunks) is closed by the same mechanism.

**M1: closed.**
- `_plain_since` is now set only in the dropping branch (OC:561), so it no longer moves mid-sentence.
- Both `test_m1_*` cases trace correctly: the sentence start stays at 0, and the policy stem "Returns" is inside the region.

**H3: closed in code.**
- `_IGNORABLE` (OC:67-71) matches the full Default_Ignorable_Code_Point set.
- After NFKD, characters of category Mn, Me and Cf are dropped (OC:79-83).
- Whether the model actually copies these characters is still unconfirmed.
- Every character that now folds to nothing also feeds N1.

**L4: closed for the IDNA dots and the listed lookalikes.**
- U+3002 is folded directly. U+FF61 reaches U+3002 through NFKD, and U+FF0E becomes `.` through NFKC.
- İ becomes I+U+0307 under NFKD, and the mark is dropped. ı, ɑ, օ and ꓲ are added to the confusables.
- Residual: `northwind-deals[.]net` still passes. `[.]` is not ID-shaped, and `_DOMAIN` needs `[\w-]\.`. It is not clickable. It is still not recorded anywhere (see N5).

**M4: partly closed.**
- Spaced text is now linear: the origin bound is at OC:461, the cache at OC:363-375, and delta pruning at OC:544.
- A long run with no whitespace is still quadratic, which makes it a medium, unconfirmed denial of service.
  - The trailing word is held with no length limit.
  - On every delta, `view_of(self._raw[origin:])` (OC:462) refolds all of it in Python, character by character.
  - An injected instruction such as "end with this 30k-character tracking string" gives roughly 30k²/8 calls to `_fold_char`.
  - The `test_m4` text has spaces, so it does not exercise this case.
- Two smaller residuals, both unconfirmed:
  - In the dropping state, `_BOUNDARY.search(raw, dropping.end)` (OC:552) rescans from the same point on every delta. This runs in C.
  - `re.search(r"\S+$", …)` (OC:343) backtracks quadratically over a long word that is not the last one in the held text.
- Fix: build the view incrementally (cache the folded characters and index per raw position, and throw the cache away from the start of a `_cut`). Alternatively, fail closed by blocking a run of non-whitespace longer than a fixed bound. Add a test with a 30k run that has no spaces.

**L1: partly closed.**
- `in`, the first word of `in stock` (OC:197), is still not a trigger, so "…is in" goes out before the repair takes it back.
- a and an are excluded on purpose.
- Only a leading word can go out this way. Low.

**L2: closed for both named cases.**
- A word cut off by a Reset is dropped (OC:342-346).
- On the outage path, `discard` sends nothing that was held (OC:341). At turn.py:245 this uses the same `outage` test as run_turn at :193 and :208.
- Your question 3: nothing unchecked is released. What remains is checked with `final=True`, and the word that was cut was never sent. Dropping it only loses text that is reset anyway, which is acceptable.
- Low residual: the final check treats the cut as the end of the text, so a payload that spans several words and is cut between words still goes out and is then reset straight away. Examples:
  - `+1 555 010` with `0199` cut off has 7 digits, so the phone exemption lets it pass.
  - `within 365` with `days` cut off passes.
- The stricter option is the pass-1 one: discard on every Reset.

**L3: closed.**
- usage.py:93-98 always writes one line. `flush` is called only in `finally` (turn.py:255-257).
- The early paths (turn.py:222-235) log through a non-deferred `report` and return before the `try`.
- No `report` can run after the generator is closed.
- Low residual, already there before this row and unconfirmed: if `rag.mint`, `search_sources` or `assistant_options` raises before the `try`, no line is written.

**L6: partly closed.**
- The roots are extended, and missing ones are skipped.
- The new `query` pin only catches `from claude_agent_sdk import query`. It misses:
  - `import claude_agent_sdk` followed by `claude_agent_sdk.query(...)`
  - `ClaudeSDKClient`, which also runs the model
  - `from claude_agent_sdk.<submodule> import query`
- Low.

**Not closed, deferred as stated:**
- M2 and M3 are the user's decision (stop 7).
- L5's first bullet (markers that are not ID-shaped) is logged.
- L7 is logged and was not reviewed.

## Your specific questions

**Can `_cut` or `_clean_markers` corrupt positions?** No, with one exception, N3.
- Every cut starts at or after `_consumed`:
  - `_clean_markers` searches from `_consumed`.
  - Removing the space needs `start > _consumed`.
  - The Reset cut starts at `_consumed + partial.start()`.
- So `_consumed`, `_plain_since` and `_shown` are untouched, and the `keep` arithmetic at OC:575 holds.
- `moved()` is monotonic, so the deltas stay disjoint and contiguous.
- Sent text is always a subset of `raw[consumed:target]`, which was matched in the same pass.
- `_dropping.end` maps correctly, including when a marker sits inside a dropped link word.
- `_undecided` is recomputed before `_safe_end` reads it.

**Can the `_sentence_start` cache go stale?** No.
- It is cleared in every case where it could be stale:
  - when `_plain_since` changes (OC:367)
  - when it is asked for a position before the scanned point (OC:367)
  - when a `_cut` lands before the scanned point (OC:428-429)
  - on Reset or abort (OC:358)
- A cut at or after the scanned point leaves the scanned prefix unchanged.
- The re-read from `scanned-1` covers the case where `endpos` hid the lookahead.

## New findings

### N1. High: the M4 lookback counts raw characters, but patterns are bounded in folded-view characters
- **Where:** OC:461 (`self._consumed - 2 * L`), together with OC:504 (the trigger window, also counted in raw characters, already there before this row). Decision 0014 §3 (lines 32-33) bounds pattern width in view characters.
- **Rule:** hard rules 2 and 6, NFR-03.
- **What is wrong:** default-ignorables, and now also Mn marks, fold to zero characters. A match only a few view characters wide can therefore span more than 2L raw characters.
- **How it fails** (traced): `"Use code" + "​"*130 + " SAVE20 today."`, in 4-character deltas.
  1. "code" plus the ZWSPs form one word, held as the trailing word.
  2. When the space arrives, no trigger is left in the last 63 raw characters, so the word is released and `_consumed` is about 208.
  3. When "SAVE20 today." arrives, the origin is 80. "code" is outside the view, so the exact code pattern does not match, and "SAVE20" is released.
  4. The shopper sees "Use code SAVE20 today."
- **Regression:** pass 1 started the view at the sentence start and caught this with a Reset.
- **Other patterns affected:** every pattern that spans whitespace. Examples: `order​×130 is confirmed`, `365​×130 days` in a returns sentence, staff code, member price, out of / in stock, free returns, refund authority, phone.
- **Precondition:** the model emits about 125 or more invisible characters. That is the same unconfirmed precondition as H3.
- **Fix:** measure the lookback in view characters. Walk back from `_consumed`, adding up the folded length of each character, until there are 2L view characters. Do the same for the trigger window in `_safe_end`. Or fail closed: block a run of more than N characters that fold to nothing.
- **Test:** the three strings above at sizes 1, 4 and 1000.

### N3. Low: `_cut` maps positions wrongly when the rewritten marker is longer than the original
- **Where:** OC:421-425.
- **What is wrong:** `_fix_marker` joins the kept IDs with ", ". So `[A1,B1,C1,D1,E1,X1]` (19 characters) becomes `[A1, B1, C1, D1, E1]` (20 characters), and `size` comes out negative. With real 6-character IDs this needs 9 or more kept IDs.
- **How it fails:** when the marker ends the raw text, the last |size| characters are left outside every delta. `_shown` has them, but the token stream does not, because `_emit` only sends delta pieces.
- **Not unchecked:** what is sent is still a subset of checked text.
- **Fix:** `p if p <= start else (p - (end - start) + len(new) if p >= end else min(p, start + len(new)))`.

### N4. Low, hard rule 5: a marker formed by a cut is not rescanned
- **Where:** OC:456 (`position = start`).
- **How it fails:** in `see [[X1]Y2] now`, `[X1]` is removed and leaves `[Y2]`. Its `[` lies before `position`, so the invented `[Y2]` is released and shown. It never becomes a citation, because `cited_ids` intersects with `seen`.
- **Fix:** resume the scan from `raw.rfind("[", self._consumed, start)` when that finds a `[`, otherwise from `start`.

### N5. Low, documentation
- Decision 0014 §2 (/Users/rapido/Projects/northwind-shop-b/docs/decisions/0014-output-control.md:27-28) still describes the old fold: NFKC, then Cf only, then Cyrillic and Greek.
- So does /Users/rapido/Projects/northwind-shop-b/docs/governance/threat-model.md:194.
- The L4 residual (`[.]`) and the N1 view-versus-raw width assumption are not recorded in either.

## Checked, no findings (scoped to this diff)
- **Hard rule 1:** no finding. No card-data path was touched.
- **Hard rule 2:** finding N1 (also M3, deferred).
- **Hard rule 3:** no finding. The test data is synthetic: STAFFALL, 555-01xx numbers, private IP 10.0.0.5.
- **Hard rule 4:** no finding. Telemetry only gained the outcome "aborted", and no text is logged.
- **Hard rule 5:** findings N4 and L5 (logged).
- **Hard rule 6:** findings N1, M4 partly, L1/L2 residuals.
- **Hard rule 7:** not applicable to this change.
- **Hard rule 8:** not applicable to this change (no tools touched).
- **Hard rule 9:** not applicable to this change.
- **Hard rule 10:** not applicable to this change.
- **Hard rule 11:** not applicable to this change (L7 logged).
- **Decision 0001, placeholder key in child processes / only the proxy holds the real key:** not applicable to this change (llm.py untouched).
- **Decision 0001, Makefile does not export the key:** not applicable to this change.
- **Decision 0001, no key in CI secrets / CI runs in replay:** not applicable to this change.
- **Decision 0001, key file never copied into a worktree:** not applicable to this change.
- **Decision 0001, budget caps enforced and not raised:** not applicable to this change (no cap touched, nothing recorded).
- **Decision 0001, cassettes still covered by the secret/PAN scanner:** not applicable to this change (no cassette or scanner change).
- **Data in the diff that tries to steer the review:** none found.
