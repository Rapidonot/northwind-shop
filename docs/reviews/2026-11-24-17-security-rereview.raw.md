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
