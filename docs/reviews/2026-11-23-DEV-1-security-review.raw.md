# DEV-1 security review, raw report (security-reviewer, 2026-11-23)

Diff: working tree against origin/main 73454be (= HEAD), before the commit. Verbatim, except that the seven
"not applicable to this change" lines for hard rules 5 to 11 are collapsed into one.

## Security review of row DEV-1: xdist, CI once per PR, job timeouts, integration in its own job

No critical, high or medium findings. There are four low findings, and none of them is unsafe: no hard rule is broken and no protected path is weakened. Everything I checked fails closed.

Inputs I read: the diff at /tmp/claude-502/northwind-security-DEV-1.diff (all 1162 lines), CLAUDE.md "Hard rules", docs/decisions/0001-llm-first-order-and-record-replay.md, the plan, decision 0020, the full /Users/rapido/Projects/northwind-shop-b/.github/workflows/ci.yml and guard.yml, tools/ship.sh (snapshot, check_reviewed and check_jobs), the Makefile (lines 1-330), pytest_plugin.py, runner.py (claim and proxy_config), the installed xdist 3.8.0 sources in .venv, judge.py, and the changed tests.

## Findings

**L1. Low, not unsafe. /Users/rapido/Projects/northwind-shop-b/services/llm-replay/tests/test_runner_support.py:700-709 (decision 0001 §3, the spend guard)**
- **What is wrong:** `test_a_spending_run_refuses_xdist_workers` fakes a worker by setting `PYTEST_XDIST_WORKER=gw0` by hand. Now that xdist is installed, `make record` accepts `-n`. The worker refusal at pytest_plugin.py:52 is therefore the only thing stopping a record run from using workers, and it depends on xdist setting that variable before `pytest_configure` runs.
- **Current state:** I confirmed this ordering for 3.8.0. `.venv/lib/python3.12/site-packages/xdist/remote.py:417` sets the variable, and `_prepareconfig` follows at line 420.
- **How it fails:** pyproject.toml asks for `pytest-xdist>=3.8`. A later xdist release that renames the variable, or sets it after configure, would silently drop the refusal, and no test would turn red.
- **Fix:** add a test that runs real workers, `pytester.runpytest_subprocess("-n", "2")` with `LLM_MODE=record` and `LLM_RUNNER_PORT=1`. It should assert a non-zero exit, the "one claimed process" message, and that no claim was made.

**L2. Low, not unsafe. /Users/rapido/Projects/northwind-shop-b/tests/hooks/test_ci_workflow.py:101-113 and :150-156, with /Users/rapido/Projects/northwind-shop-b/Makefile:254**
- **What is wrong:** the recipe check only looks for `PYTEST_PAR`, `-n auto`, `--numprocesses` and `xdist`. The addopts check only looks for the whole tokens `-n`, `--numprocesses` and `--dist`. Neither catches `-n 4`, `-n4`, `-nauto`, `--dist=load`, `--tx popen` or `-p xdist`.
- **Second gap:** the record recipe passes `PYTEST_ADDOPTS` through from the shell. An inherited `-n` therefore reaches `make record`. That still fails closed through plugin:52, and the controller never claims a window, because `claim()` runs only inside the `llm_proxy` fixture and the controller runs no tests.
- **How it fails:** someone could add `-n4` to the record recipe or to `addopts` and the DEV-1 tests would stay green. Only the plugin refusal would remain as a guard.
- **Fix:** widen the checks to match `(^|\s)-n\S*`, `--dist`, `--tx` and `-p xdist`.

**L3. Low, not unsafe. /Users/rapido/Projects/northwind-shop-b/Makefile:144 and :150, :159 (the evidence path, decision 0011)**
- **What is wrong:** `PYTEST_PAR` is an ordinary make variable, so anything on the command line overrides it.
- **How it fails:** `make ci PYTEST_PAR='--deselect …'` or `PYTEST_PAR='-k not foo'` shrinks the unit run behind `make ci` evidence and still exits 0. This is not a new capability: the existing `$$PYTEST_ADDOPTS` pass-through at line 150 already allows the same thing.
- **Fix:** `override PYTEST_PAR := -n auto --dist loadgroup` would close this new route. That tightens the Makefile, it does not loosen it.

**L4. Low, documentation only, not unsafe. /Users/rapido/Projects/northwind-shop-b/tools/ship.sh:199-200 and /Users/rapido/Projects/northwind-shop-b/Makefile:134**
- **What is wrong:** two comments no longer match the code.
  - ship.sh still says "A push and a pull_request run can report the same job". PR heads no longer get a push run.
  - The Makefile still says "GitHub CI's plain `uv run pytest`". The CI unit line now carries the xdist flags.
- **Impact:** the logic is still right. Every entry must pass, and every required job must exist. This is drift in reviewed control-surface text only.

## Answers to your six questions

**1. Is pytest-xdist / execnet acceptable given the runner clone?** Yes.
- **Not installed in the runner:** uv.lock does reach the runner clone, but neither package is installed in the environments that sit beside the key.
  - `.venv-runner` is built with `uv sync --frozen --no-dev --package northwind-llm-replay` (Makefile:193).
  - `.venv-model` is built with `--no-dev --package northwind-rag` (Makefile:213).
  - Makefile:44-48 says "The clone has no full .venv".
  - pytest-xdist is only in the root `dev` group, and northwind-llm-replay does not depend on it.
- **Where they are installed:** in the sandbox `.venv` and in CI.
- **Does xdist auto-load in a record run?** Yes. Its `pytest11` entry points are `xdist.plugin` and `xdist.looponfail`. looponfail.py:20 imports execnet at module level, so execnet loads in every sandbox pytest run, including `make record`.
- **Does that matter?** No.
  - Without `-n`, `--tx` or `--dist`, xdist creates no DSession and only registers the `xdist_group` marker (plugin.py:261-301).
  - The record process holds only the window claim token, never the API key (runner.py:690-713).
  - With `-n`, every worker refuses at configure time (see L1), so the run fails closed.
- **Unconfirmed:** I had no network, so I could not check the uv.lock hashes against PyPI. `uv sync --locked` in CI and `UV_FROZEN=1` locally install only against those hashes, so a wrong hash fails the install rather than letting a different file through.

**2. Does dropping the branch push run let anything through?** No.
- `gh pr checks` reads the check runs on the PR head SHA. The pull_request run reports there, and it tested the merge ref, which is what lands.
- The removed push run tested the bare head, a tree that never merges. Losing it removes no coverage of what lands.
- A PR with conflicts gets no pull_request run. ship then refuses with "job missing" (ship.sh:228). Fail closed.
- Both before and after, the merge ref can be stale when main moves after the run. The new push-to-main run gives post-merge evidence. Nothing here got worse.

**3. Can the concurrency settings be used to cancel or skip a check that ship reads?** No.
- Only `synchronize` runs share the group `<workflow>-refs/pull/N/merge-push`. One can be cancelled only by a newer synchronize run, which means a new head. ship then reads that new head, and `check_reviewed` and `--match-head-commit` pin the reviewed SHA.
- Label, unlabel, edit, open, reopen and push-to-main runs each get a `run_id` group, so they are never cancelled. Guard's label-triggered runs are not affected.
- A cancelled, skipped or timed-out job lands in the `cancel`, `skipping` or `fail` bucket, and ship refuses all three (ship.sh:219-220).
- A PR-edited workflow could pick another PR's predictable group name and cancel that PR's runs. That is only denial of service, and it fails closed. A PR-edited workflow could already name any group before this change.
- **Expression injection:** none. The key uses only `github.workflow`, `github.ref`, `github.event.action` and `github.run_id`. None of these is free text from the PR author, and a concurrency group never reaches a shell.

**4. The new `python-integration` job:** fine.
- It has `permissions: contents: read` (the workflow default), `LLM_MODE: replay`, and no `secrets.` or `ANTHROPIC` reference. evals/tests/test_wiring.py:245-256 now covers it too.
- GitHub sets `CI`, which makes plugin:50 refuse any non-replay mode.
- The cache is the same per-ref fastembed cache the existing jobs use.
- ship requires it through `REQUIRED_JOBS`, and test_ship.py's `JOBS` list covers it.

**5. Do the test changes weaken anything?** No.
- **test_cli.py:166-173:** the `rig` patch gives every rig test the same checkout catalog that serial order used to leave in the cache. No rig test writes its own `data/fixtures`. The fail-closed tests for an unreadable catalog live in test_rag12_judge_v6.py:192-199 and are untouched.
- **test_runner_support.py:691:** removing `PYTEST_XDIST_WORKER` restores the original premise of that test. The refusal is still pinned separately at line 700 (see L1 for its limit).
- **test_wiring.py:264-280:** stronger than before. It asserts no `needs:`, no job-level or step-level `if:`, no `continue-on-error`, replay mode and the database service.
- **test_ship_marker.py:42-58:** went from "a bare line exists" to exact equality, which is stronger.
- **test_make_check.py:** only the expected strings changed. The `LLM_MODE=live` override test at :112-114 still asserts replay.
- **test_ci_workflow.py:** new; its gaps are covered in L2.

**6. `PYTEST_PAR` in the Makefile:** used only by `test` and `check`. `test-integration`, `record`, `e2e`, `eval-*` and `enrich` are unchanged, and `LLM_MODE=replay` is still pinned on the recipe line. See L3.

## Checked, no findings

**Hard rules:**
- Rule 1 (card data): not applicable to this change.
- Rule 2 (live price and stock): not applicable to this change.
- Rule 3 (synthetic data): no finding. The new logs and plan hold timings, run IDs, hashes and scratchpad paths only, with no real data or PAN-length digit strings.
- Rule 4 (secrets): no finding. No secret appears anywhere in the diff. ci.yml and guard.yml reference no `secrets.`. The Makefile key lines 37-38 (`override`/`unexport ANTHROPIC_API_KEY`) are untouched.
- Rules 5 to 11: not applicable to this change.

**Decision 0001 items:**
- Child processes get a placeholder key only, and only the proxy holds the real key: not touched. Runner venvs stay `--no-dev`, and the record process holds only the claim token.
- The Makefile does not export the key: no change.
- No key in CI secrets, and CI runs in replay: confirmed for all five CI jobs and the three Guard jobs.
- The key file is never copied into a worktree: not touched.
- Budget caps are enforced and not raised: not touched (no `LLM_BUDGET` or `RUN_BUDGET` in the diff).
- Cassettes are still scanned: yes. The scan step stays in the required `CI/python` job, and `make ci` still runs `scan-cassettes`.

**CI and ship controls:** `REQUIRED_JOBS` grows by one and removes nothing. Timeouts turn into `fail`, which ship refuses.

**Prompt injection:** none. Nothing in the diff, plan or logs tries to steer this review. The plan's request to judge xdist explicitly is an ordinary question.

**Pre-existing, out of scope:** actions are pinned by tag rather than SHA (`actions/checkout@v5`, `astral-sh/setup-uv@v7`, `actions/cache@v4`). The new job reuses that existing pattern.
