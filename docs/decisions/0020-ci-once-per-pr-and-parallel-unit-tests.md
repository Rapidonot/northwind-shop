# 0020: CI once per PR push, integration in its own job, and parallel unit tests

Status: accepted by the user, 2026-11-23, through the DEV-1 plan (`docs/reviews/2026-11-23-DEV-1-plan.md`),
with the user's amendments. The split job was added by the user's answer at the start of the row.

## Context

The wall time of a row was mostly test time. On `dd53b85`, local `make ci` took 60.5 min, and the serial
CI-form unit run took 54 min. GitHub `CI / python` took 29–35 min, and CI ran twice for every PR push.
Measurements: `docs/reviews/2026-11-23-DEV-1-xdist.log`.

## Decision

1. **Unit tests run on pytest-xdist** (`-n auto --dist loadgroup`), in `make test`, in `make check` and in CI's
   `python` job. The flags go on those command lines only, never in `addopts` or `PYTEST_ADDOPTS`: nested pytest
   runs inside the tests inherit the environment, and `addopts` would reach `make record`.
   - **Serial, unchanged:** integration, e2e, `record`, `eval-live`, `enrich` and the eval targets. The replay
     plugin already refuses xdist workers outside replay.
   - **Not parallel-safe:** a test that is unsafe in parallel gets an isolation fix. If a fix would change what
     the test proves, it gets an `xdist_group` mark instead. No assertion is loosened, skipped or deselected.
2. **CI triggers.** `ci.yml` runs on `pull_request`, and on `push` to `main`. The push run on a branch's bare head
   goes. A PR is tested on its merge ref, which is what `ship`'s merge commit lands, and `main` is tested again
   after the merge. A branch pushed with no PR gets no CI; `finish` opens the PR in the same step as the push.
3. **`python-integration`** is its own job beside `python`, with the same service and replay environment, and it
   runs `uv run pytest -m integration`, still serially. It is in `ship.sh`'s `REQUIRED_JOBS`. Once the runner
   syncs this change, `ship` refuses an open PR whose head has no such check ("job missing"). Merging main, or
   pushing again, fixes that.
4. **Job timeouts** are 2x the slowest measured job, rounded up to 5 min, with at least 10 min for jobs that
   start Postgres or install a browser:
   - CI: python 35, python-integration 40, eval-gate 10, e2e 10, portal 5.
   - Guard: eval-gate-main 10, baseline-label 5, eval-integrity-main 5.

   The previous default was 360 min. A timeout is a failure, and `ship` refuses it.
5. **Concurrency.** Both workflows use #12's key:
   `${{ github.workflow }}-${{ github.ref }}-${{ github.event.action == 'synchronize' && 'push' || github.run_id }}`,
   with `cancel-in-progress: true`. Only `synchronize` runs share a group, so a push cancels the run on the old
   SHA, which `ship` never reads. Label, edit, open and reopen runs, and the push to `main`, are never cancelled.

## Consequences

- No check is removed. Every job that ran before still runs on every PR. `REQUIRED_JOBS` grows by one.
- Tests that passed serially only through test order or an inherited environment were fixed for isolation; the PR
  body lists them.
- `uv.lock` adds `pytest-xdist` and `execnet`, which also reach the runner clone through runner-sync.
