# Row 18a: security-reviewer's report, verbatim (diff base 81683c2, head 28e07ef)

Saved as returned. The session's notes are below the report.

---

## Security review of row 18a (diff base 81683c2, head 28e07ef)

I read `/tmp/claude-502/northwind-18a-security.diff`, CLAUDE.md "Hard rules", decision 0001, and the changed files on disk. I could not run anything, so every finding below is traced through the code by hand, not executed.

**Process warning first.** `services/assistant/src/northwind_assistant/output_check.py` on disk is not the diff head, and it changed while I was reading it:
- My first read matched the diff: `_SENTENCE_END` at line 835, `NEGATION_REACH = 2 * L`.
- A later read showed `_SENTENCE_END` at line 842, `NEGATION_REACH = 4 * L`, and `negated_in_sentence(before, *, whole=...)` with a fail-closed `started` flag.

That uncommitted edit is not in the diff, and I did not review it. It needs a scoped re-review before it is committed. Line numbers below are at the diff head. M1, L1 and L2 still hold on the on-disk version: a restart sets `started=True`, and a sentence end still sets it too.

### Findings

**Critical:** none.

**High:** none.

**Medium**

**M1. A dash-bounded aside hides the negation.**
- Where: `output_check.py:843-848` (`_RESTART`, `_RESTART_SPOILED`), read in `negated_in_sentence` at 854-866 and the `offered_range` exemption at 1195-1197.
- Rule: hard rule 2, the same M-B class this row is meant to close. Protected path (`services/assistant/**`).
- Cause: `_RESTART` treats `—`, `–` and spaced ` - ` as the start of a new clause. `_RESTART_SPOILED` is only `[,()\n]`, so the dash that closes an aside is never spoiled. `_negated` also cuts at dashes (`_NEGATION_CUT`), so both checks return False.
- Inputs and outcomes:
  - "Neither colour — Sage or Black — is available in sizes M and L." with no stock check: `negated is False`, so `offered_range` returns True and the sentence is shown. This is M-B's worst case, rewritten with dashes.
  - "None of the Slate towels — Bath or Hand — is in stock." with a=5: read as positive and vouched, so it is shown although stock is on hand.
  - "The towel is not — as far as I can tell from this morning's check — in stock." with a=5: the aside is over 30 characters, so `_NEGATED_ASIDE` misses it and the sentence is shown.
- Base shows these sentences too, so this is not a regression. But Claude writes em-dash asides often, and RECORD-ALL relies on this check.
- Fix (only blocks more): do not count a dash as a restart when an earlier dash is in the same sentence, or drop dashes from `_RESTART`. Add the three sentences as failing-first cases and re-run step 0.

**Low**

**L1. Abbreviations end the sentence.**
- Where: `output_check.py:835` (`_SENTENCE_END`). Hard rule 2.
- Cause: `[.!?](?=\s)` ends a sentence at "e.g. ", "i.e. ", "approx. ", "incl. " and "vs. ".
- Input: "None of the towels (e.g. Bath or Hand) is in stock." with a=5. The sentence is read from " Bath or Hand) is", finds no negation, and is shown.
- Fix: skip ends that `_ABBREVIATION` (line 541) matches.

**L2. The negated-adverb strip removes real negations.**
- Where: `output_check.py:850` (`_NEGATED_ADVERB`). Hard rule 2.
- Cause: it strips "not/no plus any one word" between commas, including words that keep the negation.
- Inputs: "It is, not yet, in stock." and "These are, no longer, available." with a=5 are both shown.
- Fix: allow only attitude adverbs such as "surprisingly" and "unexpectedly", or exclude yet, currently, longer, now and always.

**L3. Gaps in the M-A wordings.**
- Where: `output_check.py:466-487`. Hard rule 2. Pre-existing, not regressions.
- Unmatched: "There's nothing left in size M.", "We've run out of the M." and "They're all gone." match no pattern, so they are shown with nothing checked.
- Low-stock wording: "There are hardly any left." (also barely, scarcely) matches the new `any … left` pattern as a positive claim and is vouched by any units on hand. That is a low-stock claim, which `stock_vouched` says must never pass. Base showed it unconditionally.

**L4. The carry subtraction can drop a card split across two requests.**
- Where: `scan.py:305-309` (`ahead`), plus `carried` at 318-320. Hard rules 1 and 4. Protected path (runner-sync).
- Cause: `ahead` holds the kinds found by scanning the 256-character carry on its own. The carry is a truncation, so the cut can create a finding the earlier cassette never had:
  - a digit run cut at the boundary;
  - "task-…" cut to "sk-…".
- Concrete case:
  - Call 0's text holds "14242424242424242" placed so the 256-character cut falls after the "1". As a 17-digit run it fails Luhn (sum 81), so call 0 scans clean.
  - Call 0 then ends "…ref 42424242" and call 1 starts "42424242 ok".
  - The carry starts with the Luhn-valid "4242424242424242", so `luhn-valid-pan` lands in `ahead`.
  - The real join is then dropped, in both `put` and `scan_paths`.
- The alignment is contrived and this widening is new, so it is not a regression.
- Fix (keeps the scan as strict): start the carry just after a non-alphanumeric character, so the carry alone cannot produce a finding. Simply removing `ahead` would conflict with `test_a_secret_in_one_earlier_request_is_reported_for_that_request_only`.

**L5. The punctuated-CVV form covers one spacing only.**
- Where: `cards.py:37-40`. Hard rule 1. Shared by the gateway chat check and every MCP server.
- Not caught by `contains_cvv`:
  - "CVV 1 , 2 , 3"
  - "CVV 1 - 2 - 3"
  - "CVV 7–3–7" (en dash)
  - "CVV 1:2:3"
- These are near variants of the BE-07 F3 form.
- Fix: allow `\s{0,2}[,.;:/‐-―-]\s{0,2}` between single digits, keep the existing guards, and re-run the 7,185/15,163 over-blocking measure.

**L6. `narrative_agrees` lists denials, so it fails open.**
- Where: `evals/tests/test_a2a05_partner.py:150-154` (`ORDER_DENIAL`). Touches hard rule 9 and decision 0025 clause 4, which says this check "fails closed".
- Served beside the artifact, although each one denies the action:
  - "I couldn't add the shell to your cart [P0003-SLT-REL-M]."
  - "I didn't add…"
  - "I wasn't able to add…"
  - "I can not place…" (two words)
- Quantities are not compared, but decision 0025 says only variant IDs are read, so that part is by decision.
- The suite is stale and skipped until #18, so nothing serves this yet.
- Fix: cover could/did/was/were/have + n't/not, "failed to" and `can\s+not`, or require a positive confirmation phrase instead.

**L7. A doubly escaped x-api-key header is missed in raw-only roots (unconfirmed).**
- Where: `scan.py:56`.
- Cause: the escaped alternative accepts 1 or 2 backslashes. A header escaped twice (3 backslashes, `\\\"x-api-key\\\": \\\"v`) does not match the raw text.
- Coverage: in cassettes, the decoded walk and the reassembled blocks catch it. The ledger, the proxy log and the eval roots are scanned raw only.
- Real keys are still caught by `anthropic-api-key` and the key fragments. I could not confirm that any writer produces this shape.

**Open from before this row, not new.** Row #27 PR 2's L4: `holds_card_data({"order_id": "O411111", "idempotency_key": "1111111111"})` is still False. It is pinned as a strict xfail in `services/common/tests/test_18a_cards.py` and waits on D2. D2 option (b) is a tightening, not a loosening.

### Checked, no findings
- **Hard rule 1:**
  - Every old finding is kept. `scan_cassette` runs the old raw and reassembled scans first, and nothing else in them changed except splitting on CR.
  - The `_new_kinds` dedupe only drops a kind already found in the same cassette, which is refused anyway. Only the cross-cassette `ahead` subtraction can drop one (L4).
  - Unknown delta types now fail closed.
  - The CVV change only adds matches (apart from L5).
- **Hard rule 2, part 4:** apart from M1 and L1-L3, I found no false stock claim through part 4.
  - The frame is anchored and needs the sentence's close.
  - In the stream, the trigger hold keeps the span unreleased, so the exemption is decided again until 40 characters after it are in. I found no case where the stream and the whole text disagree. For example, "I can't say it's in stock... but it is." is blocked both ways.
  - Adding `_NEGATIVE` changes no existing span's polarity.
  - The test that every pattern of the base commit is still there holds.
- **Hard rule 3:** only sandbox test card numbers and synthetic canaries.
- **Hard rule 4:** no secrets, no .env access, no key printed.
- **Hard rule 5:** not applicable, apart from `narrative_agrees` requiring a citation.
- **Hard rule 6:** no tool access comes from content; the partner's tools are fixed in code.
- **Hard rule 7:** the partner Caller has audience shopper and region US. `cmd_scan` still loads the canaries with `required=True`.
- **Hard rule 8:** the partner gets mcp-rag plus mcp-catalog (`CATALOG_READ_TOOLS | CART_TOOLS`), and no orders, policy or understander, as decision 0025 says. Each turn gets a random session. `offered_order_tools` now needs the exact cart tools, offered and used.
- **Hard rules 9, 10, 11:** not applicable to this change.
- **Placeholder key only:** `run_partner_turn` and the BE-01 test build runtimes through `northwind_common.llm.agent_runtime`. `llm.py` is untouched.
- **Makefile does not export the key:** the Makefile is not changed.
- **No key in CI secrets, CI in replay:** every job keeps `LLM_MODE: replay`, and there are no `secrets.` references. Only the scan step changed.
- **Key file never copied into a worktree:** nothing touches `LLM_ENV_FILE` or worktree setup.
- **Budget caps:** no ledger or budget change, and no spend.
- **Cassettes still scanned:** yes, and more widely. The default CI scan list covers every path the old CI step named, plus data/enrichment, data/embeddings, the ledger and the proxy log. The ledger and the log are absent in CI, so they are skipped.
- **Part 3:**
  - Changes are in tests only. `serve_retriever`'s `clock` defaults to `time.time` and is used only by tests and evals.
  - Small doc note: the clock also feeds `SearchLedger`, not only the token gate. A still clock never loosens the 3-search cap.
  - The BE-01 change in `services/backend/**` touches the test file only.
- **Prompt injection:** none in the diff, the docs or the fixtures.

On D1: I found no false stock claim through part 4's exemptions.

---

## Session notes (not part of the report)
- The "uncommitted edit" the reviewer saw is commit `94048d9` (test-writer's gap fixes), made while the review ran, after the
  diff was cut at `28e07ef`. It goes to the scoped re-review with the fixes below.
- Triage (decision 0021): M1 inside the row's files: fix. L1 to L7: real bugs in the row's files: fix. L3's three new wordings
  are taken too (a widening, step 0 measures it).
