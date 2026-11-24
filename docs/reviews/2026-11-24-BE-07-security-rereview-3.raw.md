# BE-07 security re-review, scoped, pass 3 (verbatim)

`security-reviewer`, read-only, on the user's decisions' changes (`$TMPDIR/northwind-security-BE-07-pass3.diff`, 1,248 lines, working tree against `93566a0`), 2026-11-24.

---

## BE-07 security re-review, pass 3 (read-only)

**Bottom line:** I found nothing at medium or above. N1 and N2 are closed for every shape that was traced, and for any card number split into groups of 2 or more digits. Four lows are open. Two of them are unsafe in effect (F1, F2) and should be fixed under the row's triage rule: fix unsafe findings at any severity. F1 is also a regression against 93566a0.

**Scope.** I read the diff at `/tmp/claude-502/northwind-security-BE-07-pass3.diff`, the current files you listed, `services/common/src/northwind_common/cards.py`, `services/backend/src/northwind_backend/audit.py` (`card_data`), CLAUDE.md "Hard rules", decision 0001, the pass-2 report and the plan's "Decisions on the stop". I ran nothing (I have no Bash), so every trace below was worked out by hand. Nothing in the diff, the tests or the review files tried to steer this review.

---

## Findings (most severe first)

### F1: low, unsafe in effect (hard rule 1). Single-digit pieces get past `mask_numbers`. For pieces split across turns this is a regression against 93566a0
Where: `services/backend/src/northwind_backend/agents/care.py:258` (`_NUMBER = \d{2,}`), `:261-273` (`mask_numbers`, NFKC only, nothing stripped).

**What is wrong.** The mask only matches runs of two or more digits that touch each other. Any character between two digits splits the run into single digits, and single digits are kept by design ("size 9, 2 of 3"). The digit pass that 93566a0 had counted single-digit runs across turns; it is gone now. The shared net (`cards.py`) misses these shapes too.

**How it fails, traced with the test card 4111 1111 1111 1111:**
```
0 shopper:   "card 4 1 1 1 1 1 1 1"
1 assistant: "I'm an AI assistant; please don't share card numbers."
2 shopper:   "sorry, the rest is 1 1 1 1 1 1 1 1"
```
- `_has_card` on each turn: 8 digits, fewer than 13, so no turn is caught.
- Adjacent window and shopper-role window: the two 8-digit groups are separated by words. `_wide_groups_pass` ignores runs shorter than 3 digits, so it misses them.
- Nothing is masked, and all 16 digits reach `prompt()`.
- On 93566a0, `_split_numbers` found the single-digit runs from turns {0, 2}, joined them into a Luhn-valid 16-digit number, and withheld both turns.
- The same happens with a withheld turn in between (for example "cvv 123" as turn 1). That is the N1 shape with 1-digit pieces.

**Other forms of the same gap:**
- **Invisible characters inside the groups.** Example: `"4​1​1​1, 1​1​1​1, 1​1​1​1, 1​1​1​1"` in one turn. NFKC keeps zero-width spaces (and soft hyphens, U+2060, U+FEFF), and the model reads ordinary 4-digit groups.
  - `_DIGIT_GROUP` stops at the 2-character ", " gap.
  - `_wide_groups_pass` sees only 1-digit runs.
  - `_LONG_DIGITS` misses it, and `\d{2,}` never matches.
  - Combining marks after each digit (`4́ 1́ …`) behave the same way.
  - This one breaks the approved spec itself: "4111" is a number of two or more digits.
- **Parenthesized digits** (U+2474–U+2487, for example ⑷⑴⑴⑴). NFKC turns them into "(4)(1)(1)(1)…", all single digits, and `mask_numbers` hands the model plain ASCII.
- **Control characters.** `\x04\x01\x01…` contain no digits, so they get past the screen. Then `json.dumps` in `prompt()` (`:332`) writes them as `\u0004\u0001…`, so digit runs reach the prompt that the mask never saw. I did not trace whether `run_subagent` strips these.

**Why low and not medium:**
- Every form needs deliberate obfuscation.
- The same text in a single turn also gets past `cards.py`, so the multi-turn screen is no longer weaker than the single-turn one. That gap is what made N1 medium.
- It is the same class as digits spelled out in words, which `cards.py` already documents.
- Every natural way of writing or splitting a card is closed: groups of 2–6 digits, with or without separators, across any turns, around withheld turns, and expiry dates like 12/27.

**Docs that are now false.** `care.py:20-23`, `:281-283` and the data-flow row (`docs/governance/data-flow.md`, Care row) say "no card number can be rebuilt from pieces, whatever lies between them".

**Suggested fix (tightening only, in the row's files):**
- In `screen_transcript`, after NFKC and before both the card checks and the mask, drop control characters except `\n` and `\t`, and drop format characters. The summary check and the orchestrator's ID counting already do this.
- Treat combining marks after a digit as part of the digit run.
- Optionally, count digits joined by at most one non-alphanumeric character as one number, the same rule as `cards.py`'s `_DIGIT_GROUP`. That masks "4 1 1 1" and "4-1-1-1" and still keeps "size 9, 2 of 3" and "2 of 3".
- Whatever is left (single digits separated by words, digits spelled out) goes to the user as an explicit residual.
- Add the trace above to `test_be07_units.py`. `_digits_shown` only looks at runs of 2 or more digits, so it cannot see this gap. Assert on the concatenated digits, as the end-to-end test does.
- Do this before the `backend-care` recording. The cassette scanner's narrower patterns would not catch these shapes either.

### F2: low, unsafe in effect, pre-existing class (hard rule 1). The order-ID and date exceptions carry free digits
Where: `care.py:254-257` (`_KEEP`).

**How it fails:**
- `"O411111 O111111 O111111 O111111"` in one turn:
  - `card_data` misses it, because digits split by letters is a gap `cards.py` already documents.
  - `_KEEP` keeps all four, so 24 digits containing the card number reach the model.
- Dates of the form `20yy-01-01` each carry 2 free digits.
- `_KEEP` has no `re.ASCII`, so `O٤١١١١١` (Arabic-Indic digits) is kept as well. `_ORDER_ID` at `:94` does use `re.ASCII`.

93566a0 missed the order-ID shape too, so this is not a regression. It is deliberate obfuscation.

**Suggested tightening:**
- Keep only the task's own order ID, and mask other `O\d{6}` as a placeholder.
- Compile `_KEEP` with `re.ASCII`.
- Keeping other order IDs can be left to the user if staff need them.

### F3: low, pre-existing, outside this diff (hard rule 1). A security code written as three single digits with a separator other than space or hyphen
Where: `services/common/src/northwind_common/cards.py:36` (`_CVV_DIGITS` allows only `[ -]`).

**How it fails:** "cvv 1.2.3", "cvv 1, 2, 3" and "cvv 1/2/3" in one turn are not caught, and the mask keeps single digits, so the code reaches the model next to its name.
- "cvv 123" and "cvv 1 2 3" are caught.
- "737" after a withheld turn (the CVV part of N1) is now masked, so that part is closed.

**Owner:** `cards.py`. F1's fix for format characters also covers the zero-width-space form ("1​2​3").

### F4: low, not unsafe (hard rule 2, within the user's N4 decision). Currency codes in lowercase or mixed case pass the summary check
Where: `care.py:145`, and the test `test_n4_a_currency_code_counts_only_in_capitals`, which pins `"usd 5 per pair"` as accepted.

**How it fails:** a summary containing "usd 45" or "Eur 30" is kept. Since the transcript's numbers are masked, such a figure would come from the model itself. Staff see the summary labelled as model-written, and the amounts staff act on come from code.

This matches the decision ("in capitals only"). It is listed so the user knows the pinned example goes beyond "try 2 sizes".

### F5: low, not unsafe (staff criterion). Masking removes some context staff may want
Where: `care.py:261-273`, `:321`.

**What is lost:**
- Variant and product IDs become "P[number removed]-IVO-REG-XL". On a multi-line order the summary can no longer say which line the complaint is about. The case lists the lines but does not link them to the complaint.
- Also masked: shopper IDs, 2-digit sizes ("size 10", "32x34"), dates not in ISO form ("11/02", "28 Nov"), times, and tracking or return-authorisation numbers.
- Masking can make text up to 8 times longer (2 digits become 16 characters) before the 2,000-character cut, so turns dense with numbers are cut sooner.

**Not lost:** root cause, action, order and shopper IDs, lines and payment amounts are all filled in by code. Plain-word dates such as "Nov 2" survive. The criterion still holds: `missing_for_staff` is empty.

Keeping variant IDs would bring back 4 free digits each (the same issue as F2), so that is the user's call.

### F6: low, not unsafe (pin precision)
Where: `services/backend/tests/backend_audit/test_be08_views.py:416-423`.

**What is wrong:** in `agents/care.py`, any view class may use either `CARE_READ` or `CASE_CREATE`; for example, `AuditedOrderStore(..., allowed=CASE_CREATE)` passes the test.

**Why it is harmless:** each view enforces its allow-list at runtime, and the criteria test bans payment and catalog names in `care.py`'s source.

**Optional:** pair the class with its set (`AuditedOrderStore` with `CARE_READ`, `AuditedCaseQueue` with `CASE_CREATE`).

---

## Answers to (a)–(e)

**(a) N1 and N2:**
- **N1 is closed** for its trace, the N3 trace, splits into 2–6-digit groups (with or without a withheld turn between), the CVV around a withheld turn, superscript, fullwidth and circled digits (NFKC), and other scripts' digits (`\d`).
- **Still open, all low, all needing deliberate obfuscation:**
  - single digits (F1)
  - invisible characters or combining marks between digits (F1)
  - parenthesized digits (F1)
  - control characters escaped by `json.dumps` (F1)
  - digits disguised as order IDs or dates (F2)
  - a 3-digit CVV with dot, comma or slash separators (F3)
  - digits written in words or in Han numerals: inherent, already documented in `cards.py`.
- **N2 is closed.** Dates and ordinary numbers no longer withhold turns. Withholding now comes only from `_has_card` on single turns and windows, which has the shared net's false-positive rate. I traced a turn with several dates and it does not trip it.

**(b) Hard rules 2, 8 and 9:** no regression.
- **Hard rule 2:** the case's amounts come only from the order store (the payment claim). `order_total_minor` is always None and the case is flagged `total_unavailable`. The model's view has no amounts. Nothing is read from the transcript or the summary.
- **Hard rule 8:**
  - `CarePorts` holds `orders` and `cases` only.
  - `CARE_PRICES` is gone and its absence is pinned.
  - Nothing related to the catalog is left under `care.py`, `care/`, `cases.py` or `fakes/cases.py`.
  - The model's tools are still `get_order` and `submit_summary`, which matches PRD §7's Care row.
- **Hard rule 9:**
  - The key is unchanged (`care:<order>:<root_cause>`).
  - The fingerprint (`cases.py:64-79`) now leaves out `summary_missing` and includes `lines` and `total_unavailable`, as decided.
  - Consequence: a retry after a case with no summary returns the first case with `NO_SUMMARY`, even if the retry wrote a usable summary. That is the user's L4 decision.
  - The new conflict tests (no flags, other lines, a changed payment report) confirm that code-filled changes still conflict.

**(c) Staff criterion:** it still holds. What is lost is in F5. The one loss that matters is linking the complaint to a specific line on a multi-line order.

**(d) Pin:** sound, and it keeps its meaning.
- The Care sets are admitted in `agents/care.py` only.
- `AuditedCaseQueue` is now checked.
- The values are pinned, and other modules can still use only the read-only constants.
- The `allowed=` keyword must be a name, so a positional argument fails the test (it fails closed).
- The money-name check still covers `care.py`, with no exemption.
- Minor precision point: F6.

**(e) Weakened tests:** none found.
- Every removed or rewritten test matches behaviour the user removed: the catalog total, price timeout, outage and retry, currency mismatch, and the `summary_missing` conflict (L4).
- `test_m1_…` got stricter.
- The tools test now also bans `CatalogPort` and `AuditedCatalogPort`.
- The three removed price tests carried `req("BE-02")`. Their replacement does not. I did not check BE-02 coverage outside the diff.
- `_digits_shown` cannot detect F1, so add the trace from F1.

---

## Checked, no findings
- Hard rule 1: see F1–F3. The audit gets the transcript as type and length only, unchanged. The queue's card check (N6) still covers the new `lines` field.
- Hard rule 2: no finding (F4 is within the user's decision).
- Hard rule 3: no finding. Test card numbers are sandbox numbers.
- Hard rule 4: not applicable to this change.
- Hard rule 5: not applicable to this change.
- Hard rule 6: no finding. The labelling, the order binding and the tool list are unchanged, and masked text is still labelled as untrusted.
- Hard rule 7: not applicable to this change.
- Hard rule 8: no finding.
- Hard rule 9: no finding.
- Hard rule 10: no finding. The price failure paths are removed; the case-create and order-read failures are unchanged and structured.
- Hard rule 11: no finding. The system prompt is unchanged.
- Placeholder key for child processes, only the proxy holds the real key: not applicable to this change (`llm.py` is untouched).
- Makefile does not export the key: not applicable to this change (Makefile is not in the diff).
- No key in CI secrets, CI runs in replay: not applicable to this change (no workflow changed).
- Key file never copied into a worktree: not applicable to this change.
- Budget caps enforced and not raised: not applicable to this change.
- Cassettes still covered by the secret/PAN scanner: no cassette was added and the scanner is unchanged. Fix F1 (and decide F2) before the `backend-care` recording, because the scanner would not catch these shapes either.

## Relevant files
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/agents/care.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/cases.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/src/northwind_backend/fakes/cases.py
- /Users/rapido/Projects/northwind-shop-b/services/common/src/northwind_common/cards.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/tests/backend_audit/test_be08_views.py
- /Users/rapido/Projects/northwind-shop-b/services/backend/tests/care/test_be07_units.py
- /Users/rapido/Projects/northwind-shop-b/docs/governance/data-flow.md
