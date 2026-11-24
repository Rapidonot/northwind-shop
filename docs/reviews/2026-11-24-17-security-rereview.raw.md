# Row #17: scoped security-reviewer pass, verbatim (2026-11-24)

Scope: `2925a7f..7bcc221`, limited to `output_check.py`, `test_rag08_fixed_sentence_once.py`,
`test_rag08_fixed_sentence_once_edges.py`, `test_rag09_writer.py` and `test_red_team_e2e.py`. Asked for by
advisor-reviewer finding 1. The diff was `/private/tmp/claude-502/northwind-review-17-check.diff`.

---

## Scoped security review, row #17: the output check's "fixed sentence once" change (base 2925a7f, head 7bcc221)

No critical or high findings. Containment holds: every branch in `_advance` still sets `_consumed = end` past the blocked sentence (output_check.py:1454), so the new branch only decides whether the replacement is shown. It never releases blocked text, and the blocked sentence is still counted (at match time, line 1471, or at sentence end for dropped classes, line 1430). There is one medium finding (a performance regression) and four lows.

I read: the diff at /private/tmp/claude-502/northwind-review-17-check.diff; output_check.py (lines 1-200, 780-900 and 1330-1530); turn.py:290-342, where one `OutputCheck` is created per turn and Reset is routed through `check.reset()`; test_red_team_e2e.py:255-365; the RT-003 cassette; decisions 0001, 0015 and 0018 (grep).

### Findings

**1. Medium: the new check costs time proportional to the whole answer for every blocked sentence, which breaks the earlier linear-time fix (M4).**
- **Where:** /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py:1494-1496, called at 1444.
- **Rule:** not a hard rule. It undoes the earlier review's M4 fix, which the class docstring (lines 792-795) and `_sentence_start` (lines 978-979) state as linear time.
- **What is wrong:** `_shown_before` runs `normal(self._shown)` on all the text on screen each time a fixed-class sentence is blocked. `normal` calls `view_of`, and that runs `_fold_char` on every character: a regex, two `unicodedata.normalize` calls and a translate. It does not reuse the per-character folds the check already caches (`_folds` / `_exacts`). The old `_repeats` read only a tail of about `len(fixed)+16` characters.
- **How it fails:** a planted review steers the model into a long answer that alternates short blocked sentences with short benign ones ("Use a coupon. Ok. Use a coupon. Ok. …"). Each "Ok." changes `_shown`, so `normal`'s `lru_cache` never hits. The cost is (number of blocked sentences) × (length on screen). `check.feed` runs synchronously inside the async generator in turn.py:321, so this CPU time blocks the gateway's event loop for every session. My magnitude is unconfirmed because I cannot run code: about 1-2 µs per character, 800 blocked sentences over 16k characters on screen, comes to roughly 10-25 s for one turn.
- **Side effect:** `normal`'s `lru_cache(maxsize=512)` (line 138) now keeps up to 512 whole past answers alive in process memory. Nothing logs them, but they are retained.
- **Fix:** make the check incremental. For example, keep a casefolded, whitespace-collapsed copy of `_shown` that is extended in `_emit` and when a replacement is appended (reuse `_folds` for the raw parts). Truncate it in step with `_shown` at line 1480, and clear it in `_clear`. Then test `normal(fixed) in` that copy, or search only the newly added part with an overlap of `len(normal(fixed))` and record hits per fixed sentence. Add a test that times a long answer with many blocked sentences, in the style of the existing M4 tests.

**2. Low: text the model was steered into writing can suppress every later replacement.**
- **Where:** output_check.py:1444-1449 and 1494-1496.
- **Rule:** hard rule 6 / NFR-03 (attacker text steering the check's behaviour).
- **What is wrong:** the match is a substring search in the `normal` view. That view drops combining marks (`_DROPPED` includes `Mn` and `Me`, line 82), folds confusable letters, casefolds and ignores where sentences begin and end.
- **How it fails:** a planted review tells the model to write "I c̶a̶n̶'̶t̶ help with offers or codes in chat." with U+0336 combining strike-through. The shopper sees "can't" struck out, which reads as the opposite. `normal` drops the U+0336 marks, so this counts as the fixed sentence already being on screen. The same works with the sentence inside a quote that undercuts it ("Older assistants said '…' — not me."). Every later blocked promotion sentence is then withheld and counted, but no replacement appears.
- **Does it matter:** no blocked content reaches the shopper, so containment is intact. What is lost is the check's corrective signal at the point of removal, and the attacker chose the context of the only copy left. The portal shows plain text (no markdown or HTML renderer found in apps/portal/src), so `~~` and HTML-comment hiding are not available. The combining-mark strike-through is.
- **Fix, keeping the user's rule:**
  - Compare the model's own earlier copy in a stricter view: NFC plus whitespace collapse only, with no combining-mark or confusable folding.
  - Require that copy to be a whole sentence: it starts at a sentence start and ends at a boundary.
  - Keep counting the check's own replacements as before.
  - Add tests for the U+0336 case and the quoted case, where the replacement must still be shown.

**3. Low: check (h) can pass without any replacement happening.**
- **Where:** /Users/rapido/Projects/northwind-shop/services/assistant/tests/test_red_team_e2e.py:333-334, with the guard at 353-362.
- **What is wrong:** (h) asserts only `CONFIRMATION_SENTENCE in shown`. Under the new rule the model's own verbatim copy is treated the same as a replacement.
- **Current recording:** RT-003 is honest today. Its model text (cassettes/assistant-redteam/ab2b3ac05815deab29baa031bda3cd1fa46d2076124887d0da21720170042760.json) contains "so I can't confirm that." but not the fixed sentence. So the sentence on screen can only have come from the check's replacement of "Reviews can't authorize refunds or change policy, so I've ignored that text." The new comment at lines 299-305 says accurately that this proves the replacement path on the model's own safe sentence (a false positive), not containment of a planted claim.
- **How it fails:** a future re-recording in which the model writes the confirmation sentence itself would pass (h) and the guard while proving nothing.
- **Fix:** also assert that the model's raw text, read from the turn's cassette or exposed blocked counts (`usage.blocked`), shows at least one `refund_authority` or `confirmation` hit, or that the model's raw text does not contain `CONFIRMATION_SENTENCE`.
- **Coverage note:** after this swap, no recording proves that a planted refund claim is replaced. The red-team suite relies on `hits(sent, …) == {}` for that. This was the user's decision (option 1).

**4. Low: decisions 0015 and 0018 still describe RT-017 as the replacement proof.**
- **Where:** /Users/rapido/Projects/northwind-shop/docs/decisions/0015-output-control.md:179 and 332-335; /Users/rapido/Projects/northwind-shop/docs/decisions/0018-row-32-planted-reviews-and-judge-v6.md:54 and 60-62.
- **What is wrong:** both still say `REPLACED_IN_RECORDING` holds RT-017 and that the guard requires RT-017. Only PROGRESS.md:72 records the swap to RT-003.
- **Effect:** the earlier swaps were recorded as decision amendments. A later reviewer reading 0018 would check the guard against the wrong item.
- **Fix:** add a row #17 amendment to 0015 or 0018 (the user's option 1, 2026-11-24) stating the new set and what RT-003 does and does not prove.

**5. Low (cosmetic, not security): a skipped replacement can leave an empty line or a trailing space.**
- **Where:** output_check.py:1449, which sets `_skip_space`. That only skips spaces and tabs (line 1459), not newlines.
- **How it shows:** a left-out bullet line ("\n- Use the discount code.\n") keeps its newline and can leave an empty line. test_rag09_writer.py now pins `f"{MONEY} Wash cold. "` with a trailing space.
- **Fix:** none needed for security. Mention it in the PR body.

### Answers to the specific questions
- **Can blocked content get through?** No. `_consumed` always moves past the blocked sentence.
- **Can a sentence be dropped without its replacement ever having been shown?** Only when an equivalent of the fixed sentence is already in `_shown`. See finding 2: the equivalence (`normal`) is broader than what the shopper actually sees.
- **Is the shopper left with no signal, and does it matter?** They see one earlier copy, in a context the attacker can choose, and no marker at the removal point. Containment and counts are unaffected (finding 2, low).
- **Is `_shown` always what is on screen?** Yes. If part of a sentence was already sent and is taken back, `_shown` is cut first (line 1480), before `_shown_before` runs. The cut never goes back before `_plain_since` (lines 940-946 and 981-982), and `_plain_since` is set to the end of each decided sentence (lines 1454 and 1463). So an earlier copy cannot be taken back after it suppressed a replacement.
- **Does the reset path keep containment?** Yes. `reset()` settles the pending sentence with `final=True`, counts dropped classes (`_count_pending`), then `_clear()` empties `_shown`. The next attempt therefore gets its own replacement, and `Reset` is passed to the portal (turn.py:323-326). `discard=True` sends nothing. `abort()` keeps `_shown` but the turn ends there.
- **Does `normal()` open a bypass?** Not of the blocking itself. It only affects whether a replacement is shown. It does open finding 2 (signal suppression) and finding 1 (cost).
- **Is the RT-003 swap honest?** Yes, for today's recording and as described in the comment. It is fragile (finding 3) and not yet in the decision records (finding 4).
- **Is any test weakened?**
  - test_rag09_writer.py: the #12 test was reversed to the user's rule. It is stricter (exact text on screen, plus `hits == {"money": 2}`) and was accepted by the user.
  - test_red_team_e2e.py guard: still requires the set to be non-empty, to name real items, and RT-016/RT-017 to stay red-team items.
  - The new edge tests check counts, that nothing blocked leaks ("discount" absent), split deltas and reset.
  - No test was loosened.

### Checked, no findings
- Hard rule 1 (card data): not applicable to this change. No PAN/CVV path is touched, and replacements are fixed strings.
- Hard rule 2 (price/stock only from live tools): checked. A blocked money or stock sentence is never released. When its replacement is left out, the same "live price/stock check" sentence is already on screen (subject to finding 2).
- Hard rule 3 (synthetic data only): checked. The tests use synthetic strings and the existing cassette.
- Hard rule 4 (secrets): not applicable to this change.
- Hard rule 5 (no invented attributes): checked. Fixed sentences make no product claim, and the model's sentences are untouched.
- Hard rule 6 / NFR-03 (retrieved text is data): one low finding (2). Nothing grants tool access.
- Hard rule 7 (internal documents, regional policies): not applicable to this change. The red-team checks (b) through (g) are unchanged.
- Hard rule 8 (least privilege): not applicable to this change.
- Hard rule 9 (idempotency, monetary confirmation): not applicable to this change. Blocked confirmation and refund-authority sentences are still withheld.
- Hard rule 10 (structured errors): not applicable to this change.
- Hard rule 11 (AI disclosure): not applicable to this change. RT-003's replaced sentence is not a disclosure sentence.
- Decision 0001, placeholder key for child processes, only the proxy holds the real key: not applicable to this change.
- Decision 0001, Makefile does not export the key: not applicable (Makefile not in the diff).
- Decision 0001, no key in CI secrets and CI runs in replay: not applicable (no workflow change). The changed tests run in replay.
- Decision 0001, key file never copied into a worktree: not applicable to this change.
- Decision 0001, budget caps enforced and not raised: not applicable to this change.
- Decision 0001, cassettes still covered by the secret/PAN scanner: no cassette is added or changed in this diff. The RT-003 cassette was only read.

### Data-as-instructions
Nothing in the diff, tests or the RT-003 cassette tried to steer this review.

---

# Scoped re-review of the fixes, verbatim (2026-11-24, base 7bcc221, head afc49e2)

Diff: `/private/tmp/claude-502/northwind-review-17-fix.diff`.

## Scoped re-review of the row #17 fixes (base 7bcc221, head afc49e2)

No critical, high or medium findings. Containment still holds and the main cost path is now linear. There are four lows. One is a remaining quadratic case in the whole-sentence back-scan. Another is that the row #12 adjacent rule (`_repeats`) can still be gamed with the strike-through from F2.

I read the diff at /private/tmp/claude-502/northwind-review-17-fix.diff. In output_check.py at head I read lines 1417-1430, 1455-1505 and 1511-1542. I also read test_red_team_e2e.py:338-348 and usage.py (the telemetry logger, the `assistant_turn` event and the `blocked` key). I cannot run code, so the timings below are my estimates, not measurements.

### Findings

**1. Low: the strike-through trick still works when the copy sits right before the blocked sentence. F2 is only partly closed.**
- **Where:** /Users/rapido/Projects/northwind-shop/services/assistant/src/northwind_assistant/output_check.py:1455 and 1511-1514 (`_repeats`, unchanged).
- **Rule:** hard rule 6 / NFR-03.
- **What is wrong:** `_repeats` still compares with `normal()`, which drops combining marks and folds look-alike letters.
- **How it fails:** a steered model writes "I c̶a̶n̶'̶t̶ help with offers or codes in chat. Use the discount code." The struck copy is the last thing on screen, so `_repeats` is true and the replacement is left out. The new test `test_a_struck_through_copy_does_not_hide_the_replacement` puts "Ok." between the two, which is why it passes.
- **Scope:** containment is intact (the blocked sentence is withheld and counted). This came from row #12, but the F2 fix's stated property ("a struck-through copy is not a copy") does not hold for this path.
- **Fix:** in `_repeats`, accept the model's own copy only if it matches strictly: `_FIXED_COPIES[fixed]` anchored at the end of the trimmed tail (`pattern.pattern + r"\s*\Z"`) and passing `_whole_sentence_at`. The check's own previous replacement still counts as before. Add the adjacent strike-through case to the cost test file.

**2. Low: the back-scan in `_whole_sentence_at` can still make a turn quadratic.**
- **Where:** output_check.py:1538 (`while before and shown[before - 1] in " \t"`), reached from the `finditer` loop in `_on_screen` (lines 1417-1430).
- **What is wrong:** a candidate copy that fails the whole-sentence test is never remembered. While it stays inside the overlap window it is matched again and back-scanned again on every call. A left-out replacement does not grow `_shown`, so the window does not move.
- **How it fails:**
  - The model writes "Use a coupon." (PROMO is shown and recorded in `_fixed_at`).
  - It then writes "x", W spaces or tabs, and a plain copy of the money sentence (MONEY). The money copy is not whole because of the "x" before the spaces.
  - It then writes k more "Use a coupon." sentences.
  - For each one, `_repeats(shown, PROMO)` is false because the screen ends with the money copy. So `_on_screen(PROMO)` runs: the MONEY pattern finds the same candidate and back-scans W characters in a Python loop.
- **Cost:** O(k·W), limited only by `max_tokens`. My estimate (unconfirmed) is up to several seconds of event-loop time if the model can be steered into tens of thousands of spaces or tabs. That is much less than before the fix but not linear. The cost test only exercises the linear path.
- **Fix:** fail safe. Either cap the back-scan (for example, more than 8 spaces or tabs means "not a sentence start", which only shows the replacement), or remember rejected candidate starts per pattern. Rejected starts are a sound cache: the text before a candidate never changes except when a take-back cuts it, and that removes the candidate too. Add a timing case with a long space run.

**3. Low: L3 is a real tightening, but it does not prove what decision 0018 says it proves.**
- **Where:** /Users/rapido/Projects/northwind-shop/services/assistant/tests/test_red_team_e2e.py:343-348; /Users/rapido/Projects/northwind-shop/docs/decisions/0018-row-32-planted-reviews-and-judge-v6.md, the "Check (h)" bullet in the row #17 amendment.
- **What is sound:** the assertion fails on zero or several `assistant_turn` records. Telemetry holds counts only (usage.py:7).
- **What it does not prove:** "a blocked `refund_authority` or `confirmation` count above zero" plus "`CONFIRMATION_SENTENCE` in shown" does not mean the check emitted the sentence. Under the row #17 rule, a re-recording can pass with no replacement shown at all:
  - The model writes its own whole copy, and a later blocked confirmation sentence is left out but still counted.
  - Or a blocked sentence in an attempt that was later reset is counted (counts survive `reset()`), and the final attempt holds the model's own copy.
- **Today:** RT-003's recording does not contain the copy (I verified this in the first pass), so the current evidence is honest. The decision's sentence "The sentence on screen must therefore be the check's replacement" overstates what the test checks.
- **Fix:** also assert that the recorded model text has no copy of the sentence. Read the item's cassette text deltas as `recorded_injection_answer` does in test_rag08_fixed_sentence_once.py, and assert `CONFIRMATION_SENTENCE not in model_text`. Alternatively, reword the decision bullet to "at least one such sentence was blocked".

**4. Low, unconfirmed rendering, not caused by this row: bidi control characters can scramble the copy or a replacement.**
- **Where:** `_whole_sentence_at` (output_check.py:1532-1542) checks only the characters right before the copy. Nothing on the release path strips bidi controls; I found no handling in services/*/src or apps/portal/src.
- **How it fails:** the model writes "Ok‮. I can't help with offers or codes in chat." The check accepts it as a whole sentence (lead "."), but in the browser the RLO likely shows the rest of the paragraph reversed. Later replacements are then left out.
- **Scope:** an RLO placed before a blocked sentence would equally reverse the check's own replacement. So the real gap is that released text keeps bidi controls, not the row #17 rule.
- **Fix:** in the check, remove or neutralise bidi embedding and override controls (U+202A-U+202E, U+2066-U+2069) in released text, or close them (PDF/PDI) before each replacement. Log it as a hardening Open item with an owning row.

### Answers to the specific questions
- **Containment:** kept. `_on_screen` only chooses between the left-out branch and the shown branch. `_consumed = _plain_since = end` and the counting are unchanged.
- **Linear cost:**
  - `_on_screen` scans `[_fixed_to - _FIXED_OVERLAP, len(_shown))` with at most 6 precompiled patterns. Patterns already found are skipped.
  - The patterns have no nested quantifiers. The `\s+` runs are covered once per start position, and a start needs the first word's letters.
  - Each call costs O(new text + about 160).
  - The take-back is O(entries ≤ 6) plus `min()`. The re-send of `_shown` there is older and copies bytes only.
  - The one non-linear case is finding 2.
- **Overlap:** sound. `_shown` only grows by appending or shrinks by take-back. A copy that crosses the previous `_fixed_to` and starts within the overlap is found. A copy with more whitespace than the overlap is missed, which shows the replacement again (fail safe).
- **The `(?=\s|$)` end check:** at decision time `_shown` reaches the blocked sentence's start, and that start comes after whitespace. A copy glued to the following text with no space is not a sentence boundary (`_BOUNDARY` needs whitespace), so it belongs to the blocked sentence and is never shown.
- **Take-back:** dropping entries that end after `keep` is correct, because the text before a kept entry is unchanged. Lowering `_fixed_to` to `keep` makes the next search look again from just before the cut.
- **Gaming the strict pattern:**
  - A mark or invisible character inside a word, or attached before or after the copy, breaks the match or the whole-sentence test, so the replacement is shown.
  - `IGNORECASE` also lets İ, ı, ſ and K (Kelvin sign) match their ASCII letters. These look the same, so it is harmless.
  - Unusual whitespace that `\s` matches (NBSP, U+3000, U+001C-U+001F) is also harmless.
  - The remaining issues are findings 1 and 4.
- **Gaming the boundary rule:** a copy after ":" or " - " still counts ("Old bots said: I can't help with offers or codes in chat. Not me."). This is the user's rule: a whole plain copy followed by the model's own undercutting sentence was always allowed. Containment holds and counts are kept. I am noting it, not reporting it.
- **L3:** see finding 3.
- **Decisions:** 0015 now points to 0018, and 0018's amendment matches the code apart from the overstatement in finding 3.
- **Tests weakened:** none.

### Checked, no findings (this fix diff)
- Hard rules 1, 3, 4, 7, 8, 9, 10 and 11: not applicable to this change.
- Hard rule 2: checked. A blocked money or stock sentence is never released by the new path.
- Hard rule 5: checked. Fixed sentences make no claim, and the model's sentences are untouched.
- Hard rule 6 / NFR-03: findings 1 and 4, both low.
- Decision 0001 key handling: placeholder key only, Makefile does not export the key, no key in CI secrets and CI in replay, key file not copied into a worktree. All not applicable; this diff touches no key, Makefile, workflow or worktree setup.
- Decision 0001 budget caps: not applicable, not touched.
- Decision 0001 cassettes still scanned: no cassette is added or changed in this diff.
- Data-as-instructions: nothing in the diff, tests or decisions tried to steer the review.
