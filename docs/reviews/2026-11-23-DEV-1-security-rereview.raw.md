# DEV-1 security re-review, raw report (security-reviewer, 2026-11-23)

Scoped to the L1–L4 fixes. Diff: working tree against origin/main 73454be (= HEAD), before the commit. Verbatim.

## DEV-1 re-review of the L1-L4 fixes: pass

All four fixes are correct, and they open no new finding at any severity. I read only the fixes and what they touch in /tmp/claude-502/northwind-security-DEV-1-r2.diff (Makefile, test_runner_support.py, ship.sh, tests/hooks/test_ci_workflow.py), plus xdist 3.8.0's option list in .venv to check the L2 pattern. I did not run anything: your observed run and the mutation checks are your results, not mine.

**L1: fixed.** `test_a_spending_run_with_real_xdist_workers_refuses_before_any_claim` (test_runner_support.py, diff lines 293-309) starts real `-n 2` workers and goes red in both failure cases:
- If a later xdist set or renamed `PYTEST_XDIST_WORKER` after configure, a worker would reach `llm_proxy` and try to claim on port 1. The window hint would then appear and the refusal message would not.
- If the plugin's refusal at pytest_plugin.py:52 were removed, the same thing happens. Your mutation check matches this.

Removing the variable before the run keeps the child controller from being refused by the outer worker's marker. With `LLM_RUNNER_PORT=1`, even the mutated case cannot reach a real window. If no summary line were printed, `parseoutcomes()` would raise, and the test would fail rather than pass falsely.

**L2: fixed.**
- `WORKER_FLAG` covers every xdist 3.8.0 option that starts workers: `-n`/`--numprocesses` (plugin.py:71-72) and `--tx` (:157). It also covers `--dist` and `-p xdist`. The `-d`, `--px` and `--maxprocesses` options start no workers without `--tx` or `-n`, so it is fine that the pattern leaves them out.
- The pattern does not misfire on `--no-…` options, because it needs whitespace or line start before `-n`. Your negative cases include the real record line.
- Limiting it to recipe lines that contain "pytest" is sound.

**L3: fixed.**
- `override PYTEST_PAR := -n auto --dist loadgroup` (Makefile, diff line 165) beats both command-line and environment values, including `make -e`. It also reaches `check`'s nested `$(MAKE) test`.
- The pin regex (test_ci_workflow.py:1259) finds every non-comment line that assigns the variable, and does not catch the two `$(PYTEST_PAR)` uses.
- The `$$PYTEST_ADDOPTS` pass-through is a separate, older route of the same kind. As I said before, it is not introduced by this row.

**L4: fixed.**
- The ship.sh:412-414 comment now matches the code: every entry must pass, and each required job must exist.
- The Makefile:155-157 comment now describes the CI unit line correctly.

**Residual, informational only (no action needed).** The L2 check reads each recipe line on its own. Worker flags could still get past it by two routes:
- a make variable defined outside the recipe, such as `uv run pytest $(X)` with `X := -n 4`;
- a backslash continuation line that does not contain "pytest".

Both still fail closed, through pytest_plugin.py:52, which the L1 test now pins with real workers. Unconfirmed: whether pytest rejects abbreviated long options such as `--numproc=2`. That case fails closed the same way.

**Checked, no change from the first review:** the hard rules and the decision 0001 items. None of these fixes touches key handling, CI secrets, budget caps or the scanner's coverage.
