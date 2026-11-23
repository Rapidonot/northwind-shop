# Row 32b: `security-reviewer`, raw report (verbatim, one pass, decision 0021)

Diff: `origin/main...HEAD`, base `cefbbeee6c4a74573480bae2c3ce2849728af8c1`, head
`c416bfc` (written to `$TMPDIR/32b-security.diff`).

---

## Security review: row 32b (base cefbbee, head c416bfc)

Diff reviewed: /tmp/claude-502/32b-security.diff, all 585 lines. I read CLAUDE.md "Hard rules" and docs/decisions/0001-llm-first-order-and-record-replay.md fresh from disk, along with the two scratch scripts.

**Result: no critical or high findings.** The committed docs, the test and the impact script are clean. There is one medium and two lows, all in the CLAUDE.md edit (commit 5b01c9f). Each one goes further than decision 0021 records. The edit is the user's control-surface change, so these are for the user to confirm or record; I have not changed anything. I take the authorship from your brief and could not check it, because I cannot run git.

### Findings

**M1 (medium): /Users/rapido/Projects/northwind-shop-b/CLAUDE.md:185.** Area: the existing guardrails (0021 "Keep" 1), which bear on rules 1, 8 and 9.
- **What changed:** `test-writer` now runs "only for gate, integrity, spend or ship code".
- **What 0021 records:** "Failing-first tests for behaviour changes. Mutants only for gate, integrity, spend and ship code" (0021, "How rows run" 3). That limits mutants. It does not limit `test-writer`.
- **Why it matters:** `.claude/agents/test-writer.md` is the only independent test author. It writes tests from the PRD criteria and the public interfaces, and is told not to read function bodies. After this edit, feature rows on hard-rule paths get only tests written by the session that wrote the code. That covers #16 cart tools (rule 9), later payment code (rule 1), and assistant tool scoping (rules 6 and 8).
- **How it fails:** #16 adds a state-changing cart tool without an idempotency key. The implementing session writes its tests from the implementation, so no test asks for the key. `make ci` passes, and the rule-9 gap reaches review with nothing failing.
- **Options:** either 0021 records this narrowing explicitly, or the `test-writer` step is kept for rows touching the rule 1, 6, 7, 8 and 9 paths listed under "Routine rows".

**L1 (low): CLAUDE.md:242-243.** Area: 0021, "How rows run" 1.
- **What changed:** a Low finding is fixed "if it is a real bug in the row's files; otherwise mention it in the PR body only".
- **What 0021 says:** "A real bug is fixed in the row or logged with a named owning row." The CLAUDE.md text drops the second option.
- **How it fails:** a real bug found outside the row's files goes only into a PR body and is never owned. For example, one that corrupts evidence, which is 0021's own exception. The "Unsafe at any severity" sentence before it still catches hard-rule breaks, so the gap is limited to real bugs that are not hard-rule breaks. This triage applies to routine rows only.

**L2 (low): CLAUDE.md:187-188 and 194-195.** Area: the chain order, which 0021 does not cover.
- **What changed:** `advisor-reviewer` "with its fixes" now runs before `make ci`. 0021 only says "one `make ci` per row".
- **Effect:** the advisor now reviews a head that has no `make ci` result. Lines 194-195 still say "Any change after `make ci`, a fix or an advisor finding, is a new commit and another `make ci`", which no longer matches the chain.
- **What still holds:** `tools/finish.sh:134-145` refuses any change after the reviewed SHA except PROGRESS.md and COVERAGE.md. Advisor fixes therefore cannot ship unread, provided the gate report names the SHA the advisor actually read and not the head after its fixes. The risk is that the two SHAs get confused; it is not an open path.

### Impact script (/tmp/claude-502/impact_32b.py): cannot spend, writes nothing in the repo
- **Replay only:** `_clients("replay")` with no `judge_suite`. `proxy_config` (services/llm-replay/src/northwind_llm_replay/runner.py:701-702) returns a bare ProxyConfig for replay: no upstream session, no `key_loader`, no ledger, `log_path=None`. A second record-mode proxy is only built when `judge_suite` is set (cli.py:281), and the script does not set it.
- **A miss cannot go upstream:** a replay miss returns a 400 "cassette miss" (proxy.py:439-453). The record and live branch is never reached.
- **Placeholder key only:** clients carry `PLACEHOLDER_API_KEY` (llm.py:84, 255).
- **No live index or embedding:** `live` is None unless mode is record (cli.py:276).
- **No writes in the repo:**
  - The script calls `_clients` and `run_item` directly. The write paths in cli.py (`LAST_RUN` at :522, `save_refinements` at :534, side results at :580) are all in `_build_candidate` and `_write_side_results`, which it does not call.
  - The Agent SDK child's config directory is `tempfile.mkdtemp` (llm.py:149), outside the repo.
  - The script only reads `.llm-proxy-log/eval-last-run.json`, which is gitignored.
- **score_32b.py:** writes to `sys.argv[3]`. I could not see the invocation, so "not in the repo" is unconfirmed for that path. It is not committed, and the git status snapshot is clean.

### Committed docs (public repo)
- **Clean for all four of your questions:**
  - No secrets. The sha256 at kind-labels.md:219 is a hash of the answer-key file, not a credential.
  - No PANs or CVVs. EV-090's "emailing my card details" is an adversarial query with no number.
  - No real people or data. The products and variant IDs (e.g. P0119-SLT-REG-XL) are synthetic, and the git user's name does not appear in the diff.
  - No internal-only text (rule 7). EV-097 appears only as its shopper query plus a claim about a public product, with no internal source ID or content.
  - No exact prices or stock counts (rule 2). Variant availability is not a stock count, and docs/ is not an ingest source (`make ingest` loads data/fixtures).
- **No injection:** no text in the diff, fixtures or claims tried to steer this review.

### Test (evals/tests/test_rag12_kind_labels.py)
Clean. It reads only `REPO / "docs/reviews/2026-11-18-32-kind-labels.md"`, where `REPO = Path(__file__).resolve().parents[2]` is the repo root. Nothing outside the repo, no network, no LLM, no DB. It is not on the control surface: it is not under `evals/src/**` or `evals/fixtures/**`, and it is not a conftest.

### Checked, no findings
- Rule 1 (card data): checked. No PAN or CVV in the docs, test or scripts.
- Rule 2 (live price and stock): checked. No exact prices or stock counts in the committed text, and nothing indexed changes.
- Rule 3 (synthetic data): checked.
- Rule 4 (secrets): checked. No key or .env read by the test or the scripts. The key-file hash is not a secret.
- Rule 5 (no invented attributes): not applicable to this change (no assistant output path).
- Rule 6 (retrieved text is data): checked. No eval content tried to steer the review.
- Rule 7 (internal docs, regions): checked. No internal document text.
- Rule 8 (least privilege): not applicable to this change (no tool scoping). See M1 for the process risk.
- Rule 9 (idempotency, monetary limits): not applicable to this change. See M1.
- Rule 10 (structured errors): not applicable to this change.
- Rule 11 (AI disclosure): not applicable to this change.
- Hard-rules text: the CLAUDE.md hunks cover only lines 182-243. The "Hard rules" section is unchanged.
- 0001, placeholder key only in children, and only the proxy holds the real key: checked. Replay has no `key_loader` and clients use the placeholder.
- 0001, Makefile does not export the key: not applicable (the Makefile is not in the diff).
- 0001, no key in CI secrets, and CI runs in replay: not applicable (`.github/` is not in the diff). 0021 keeps replay-only CI.
- 0001, key file never copied into a worktree: checked. Nothing in the diff or the scripts reads or copies it.
- 0001, budget caps enforced and not raised: checked. No cap changed, and 0021 keeps `LLM_BUDGET_USD` and `RUN_BUDGET`.
- 0001, cassettes still covered by the secret/PAN scanner: checked. No cassette or scanner change, and 0021 keeps the scan.

---

# Scoped re-review (M1 fixed), verbatim: base 649a372, head ea10559

## Scoped re-review: row 32b (base 649a372, head ea10559)

I read the diff /tmp/claude-502/32b-security-rereview.diff in full, all 246 lines, and checked it against the current CLAUDE.md, tests/hooks/test_stop_conditions.py, evals/tests/test_rag12_followup.py and evals/src/northwind_evals/judge.py.

**Result: no critical, high or medium findings.** M1 and L1 are closed. L2 is closed except for one leftover low, L3 below. The hook-test changes loosen nothing beyond decision 0021, and the rescore test fix makes that test stronger.

### 1. Are M1, L1 and L2 closed?
- **M1: closed.** CLAUDE.md:185 now reads "`test-writer` (for gate, integrity, spend, ship or hard-rule code)".
  - 0021 rule 7 defines hard-rule code as the paths for hard rules 1, 6, 7, 8 and 9 listed under "Routine rows".
  - That list includes "any later payment code" and "any later agent, MCP or tool-scoping code". So #16's cart tools, payment code and tool scoping get the independent test author again.
  - CHAIN in test_stop_conditions.py:151 pins the new wording.
- **L1: closed.** CLAUDE.md:242-244 now logs a real-bug Low found elsewhere in "Open items" with a named owning row. This matches 0021 rule 1's new sub-bullet (0021:71-72), and test_stop_conditions.py:240-242 pins it.
- **L2: closed except for L3.** 0021 rule 8 now records the chain order, and CLAUDE.md:193-196 no longer contradicts it.

**L3 (low, the rest of L2): CLAUDE.md:185-188 and 193-196.** Area: decision 0021 rule 8, and the reviewed-SHA rule at CLAUDE.md:257.
- **What 0021 says:** rule 8 has `make ci` run "at the head the advisor read (**a re-read if its fixes moved the head**)".
- **What CLAUDE.md says:** "`advisor-reviewer`, with its fixes → `make ci`, once, at that head". It has no re-read clause, and no hook test pins one. The test named `test_make_ci_runs_once_at_the_head_the_advisor_reads` (:187-192) checks only the order and the counts.
- **How it fails:** the session follows CLAUDE.md, not 0021. It applies the advisor's fixes, runs `make ci` on a head the advisor never read, and the gate report must then name either the head the advisor actually read or the post-fix head.
  - If it names the head the advisor read, `tools/finish.sh:134-145` refuses, because the fix commits changed files other than PROGRESS.md or COVERAGE.md. That fails closed.
  - If it names the post-fix head, the advisor's fixes ship without anyone reading them.
- **Who can fix it:** CLAUDE.md is control surface, so this is the user's call. One option is to bring 0021's re-read clause into the chain sentence and pin it.

### 2. Hook-test edit (tests/hooks/test_stop_conditions.py)
Nothing is removed. There are still exactly ten stops (:81-82), and each changed pin is longer than before.
- **Stop 10 (:63-72):** the `after the re-review` anchor is replaced by `still open after its fix` plus `and the re-review, when one runs`. The meaning moves from "after the re-review" to "after the fix", but that is what 0021 rules 2 and 9 decided, not a loosening beyond them.
- **CHAIN (:149-160):** each step is now a longer literal.
  - "one scoped re-review" becomes "a scoped re-review only if a medium or higher was fixed", as 0021 rule 2 says.
  - `advisor-reviewer` now comes before `make ci`, as 0021 rule 8 says.
- **Order test (:187-192):** `count("`make ci`") == 1` is kept, and `count("`advisor-reviewer`") == 1` is new, which is tighter.
- **Change-after-`make ci` test (:196-199):** both old assertions are kept and one is added. The CLAUDE.md words removed there ("a fix or an advisor finding") were never pinned, so no pin is lost.
- **Triage Low (:240-242):** the short `low: log it in `progress.md`` is replaced by the full rule, including the owner clause.
- **Untouched:** the guardrail checks for the control-surface list, the git error strings, stop 5's loosening wording, the routine definition and the gate-in-`make ci` Makefile check.

### 3. Rescore test fix (evals/tests/test_rag12_followup.py:459-463)
The fix makes the test stronger. All four assertions are unchanged (:498-502): exit 1, no candidate, `upstream == []`, and no ledger file or directory. The httpx `send` trap and the `Ledger.__init__` trap are also unchanged.
- **Why it was failing:** `Judge.__init__` loads the catalog at startup (judge.py:273). Under the chdir, `default_catalog()` failed there with a JudgeError, before any request reached the proxy. So the test failed in `make ci` without ever reaching the code it is meant to prove.
- **Why the patch works:** both callers use `default_catalog` as a module global inside judge.py (:221, :273), so the monkeypatch reaches them.
- **What it reads:** read-only, from `REPO / "data" / "fixtures"`, where REPO is the test file's `parents[2]` (:56), the repo root. Nothing outside the repo is read.
- **Why it changes nothing else:** the catalog only drives kind re-labelling. It does not touch the mode, the cassettes, the upstream or the ledger.
- **Residual, not a loosening:** the test asserts exit 1 without asserting that the cause was a cassette miss (for example, `state.misses` being non-empty). This predates the row.

### 4. Anything new against the hard rules or decision 0001
Nothing new. CLAUDE.md's "Hard rules" section is not in the diff. Neither are the Makefile, `.github/`, cassettes, budget caps, the scanner or key handling. The PR-body text is records only and contains no secrets, PANs or real data.

### Checked, no findings (this scope)
- Hard rules 1-11: not applicable to this change. The hard-rule text is unchanged, and the diff touches only process text, two tests and docs.
- 0001, placeholder key only in children, and only the proxy holds the real key: not applicable. The rescore test still runs the real proxy in replay with an upstream trap.
- 0001, the Makefile does not export the key: not applicable (the Makefile is not in the diff).
- 0001, no key in CI secrets, and CI runs in replay: not applicable (`.github/` is not in the diff).
- 0001, the key file is never copied into a worktree: not applicable.
- 0001, budget caps enforced and not raised: not applicable. No cap changed, and the rescore test still refuses any ledger.
- 0001, cassettes still covered by the scanner: not applicable (no cassette or scanner change).
