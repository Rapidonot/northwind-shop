findings

No critical or high is open. 2 medium, 8 low. F1 to F14 and F16 to F18 are closed as records, some with small leftovers below. F15 is not closed (N1).

The SA lines match in both files: base 7f0f8d9b84bb46b169b9d43e7f159e5072e563e1, head b60b87045fa3ff8eff8eb309bc11152f8e5db971. The branch ref is at b60b870 too. I ran nothing. Root of paths: `/Users/rapido/Projects/northwind-shop/`.

**No code changed: confirmed from the diff.** `9027b444..b60b870` touches 11 files: `PROGRESS.md`, `docs/governance/data-flow.md` and 9 files in `docs/reviews/` (one new, my raw report). In the full diff, the code, test, fixture and baseline sections have the same files and the same section lengths as before. There are 1,440 cassette diffs, as before. Total is 1,496 sections against 1,495, the extra one being the raw report.

**About F1's approval.** It is recorded in `pr-body.md:68-72` and `records-draft.md:44-46`, and my finding is stated honestly there. I have it only through your message, so I confirm that it is recorded, not that it was given. `ship` lists the file, so the user sees it again.

## New findings

### Medium

**N1. (1, accuracy; F15 not closed) The named owner does not hold the work.** `PROGRESS.md:169, 173`; `pr-body.md:123-124, 156-157`.
- I read window B's draft as it is now: `/Users/rapido/Projects/northwind-shop-b/docs/reviews/2026-11-25-18a-plan-draft.md`. It names neither M-A nor M-B. It was committed at `8740c1e`, before read 3 was recorded (`187b075`), so that commit cannot hold them. Later commits (`c5f494e`, `2000223`) changed it; the current text still does not.
- Its output-check part holds "two fixes only (the user)": "available to me/you/us" and "I can't confirm it's available" (IC-040, EV-042). It says "**Left alone:** IC-024, the cart's 'These alternatives are in stock:', split ranges", and lists a wider narrowing and any widening of scope as stops.
- This row's `PROGRESS.md` gives 18a M-A and M-B "first on its list", and all the over-blocking "with the split-range finding". The two disagree. M-A and M-B are hard rule 2.
- There is no 18a row in window B's `PROGRESS.md` either.
- It should say that the user handed M-A and M-B to 18a, that 18a's draft at `2000223` does not list them yet, and that the user's own answer there leaves IC-024, the cart line and split ranges alone. For the rest (IC-029, IC-042, RT-021, the W6 set beyond EV-042), name an owner or "won't do". The user decides; window B must be told. Cite `2000223`, not `8740c1e`.

**N2. (Accuracy) "Not read by `security-reviewer`" is incomplete.** `pr-body.md:86-87`.
- It lists three items. These also came after the re-review's head (`567753e`) and outside read 3's three files:
  - `evals/src/northwind_evals/cli.py` (`INTENT_SETS`, `_frozen_queries`), which feeds the frozen-vector vouching
  - `evals/src/northwind_evals/intents.py`, the `is in stock` edit (`83142c6`)
  - the vector-cap change in `evals/tests/test_rag04_post_review_tw.py`, and `services/rag/tests/test_frozen_vectors.py`
- The first two are `evals/src`, control surface. Add them, or say why they were not read. This is a disclosure gap; I found no fault in that code.

### Low

**N3.** `pr-body.md:84-85`, the D8 note. "CHK-06's docstring-only change" is wrong: `prompt.py` also gained `ORDERS_RULES` and the signed-in branch (`prompt.py:95, 144`). What CHK-06 left alone is the anonymous prompt text, and its PR body says every score equals main's. The note also skips #17's own later check change (`74c8ba9d` to `a1e0e605`) and #11's promote, retriever v2 to v3, between the two baselines. Say so.

**N4.** IC-042, `pr-body.md:127`, `PROGRESS.md:175`, `w1-intents.md:77-78`. The quoted sentence is verified in the cassette (`670dc82c…`). "The shopper's own words" is too strong. The shopper said "picked up a sweater two weeks ago"; "after delivery" is the model's. Say "the shopper's own two weeks".

**N5.** `w6-eval-answers.md`.
- Line 63, under "Not yet", says `eval-integrity` is red until the promote. The promote happened (`552d5a9`) and `pr-body.md:52` says it passes.
- Lines 57-58 omit IC-042 from the over-blocking list.

**N6.** `records-draft.md:17` still lists only IC-024 and IC-040, and `:22` still says the hedge sentences were "read by the advisor", against the W6 report's "the session read all three". Fix at `/req-done` or now.

**N7.** `pr-body.md:59` says "the pin-heavy directories passed serially at `8112273`", with no log or directory names. Name them, or drop the sentence. The moved head is `b60b870`.

**N8.** `pr-body.md:109-110` (L-8): "W3's journey names every variant" has no cited evidence. The W3 report quotes an answer saying "I need the exact variant ID". Cite the test or say "the session's reading". IC-024's `get_price` calls in the committed cassettes are confirmed.

**N9.** The tail count agrees with `.llm-proxy-log/sa04.json` (ten). `pr-body.md:140` dropped "lookup" without saying that CL-006 and CL-020 come near one. Optional.

**N10. My own error.** F9 cited `38df1d8` for the frozen-answer change. That commit is W5. The right one is `37587eb`, which the PR body has. The error is in the committed raw report (`2026-11-25-30-advisor-review.raw.md`); leave it verbatim, with a one-line note elsewhere if wanted. My F1 also said the edit "only adds coverage". The test ends with `set(frozen) == chunk_keys | query_keys`, so the edit also admits those 75 keys. The PR body's "adds to the allowed list" is the accurate wording. "Loosens no assertion" holds for the assertion text.

## Closure of F1 to F18

| Finding | State |
|---|---|
| F1 | Closed as a record, in both files. The user's approval is seen only through you |
| F2 | Closed (`pr-body.md:13-16`) |
| F3 | Closed; figures match the baseline diff |
| F4 | Closed |
| F5 | Closed (ten; N9) |
| F6 | Closed; IC-042's blocked text read in the cassette (N4) |
| F7 | Closed: PR body names the file; draft now says 17 refinements (10 unanswerable, 4 adversarial, 3 others), which I checked against the file. The user's step 4 I have only from you; the draft is committed, before `make ci` |
| F8 | Closed |
| F9 | Mostly closed; incomplete (N2) |
| F10 | Closed |
| F11 | Closed, except N5 |
| F12, F13 | Closed. My sum is $5.077362 and the ledger difference $5.077363, a rounding difference |
| F14 | Closed. 3 failed and 211 passed equals test-writer's 214, and it is labelled as reproduced after the fact. The M1 collection error stands as it was |
| F15 | **Open** (N1) |
| F16 | Closed; the explanation is the session's, not re-measured |
| F17 | Closed, with N6 |
| F18 | Closed. The full stop is in and the hashes are marked superseded |

## Checked
1. Scope: no code since 9027b444; F7 named.
2. Failing first: the H4 run is added.
3. Weakened tests: none in this move.
4. Hard rules: no change; M-A and M-B ownership is N1.
5. Spend: no new window; totals agree.
6. Requested changes: yes, except N1.
7. Control surface: unchanged since my first report. `test_frozen_vectors.py` is now in the PR body.

## Could not check
- The user's approval of F1, and step 4's wording.
- The "pin-heavy" run, the H4 rerun, the 585 against 522 explanation, the ledger, `make ci` and the browser runs.
- The 18a draft's text at `8740c1e` itself; I read the current file in window B's worktree.
- EV-302 and RT-022 by eye.

## Before `make ci`
N1 and N2 are record fixes in this row's files, but N1 needs the user's answer. Any fix moves the head, so make them first, then run `make ci` once.

## Control surface
Unchanged: `evals/src/northwind_evals/{cli,harness,intents}.py`; `evals/fixtures/sa04_prompts.jsonl` and `sa03_conversations.jsonl`; `evals/baselines/dense-only.json`; `evals/contexts/refinements.ndjson`; `data/embeddings/fastembed-BAAI-bge-small-en-v1.5-384.jsonl`; `cassettes/**` (1,440 files); `services/rag/tests/test_frozen_vectors.py`.
