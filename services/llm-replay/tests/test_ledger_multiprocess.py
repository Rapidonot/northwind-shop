"""Two processes (e.g. two git worktrees) reserving against one shared ledger at once
(FOUND-2 addendum (a), NFR-16): the file lock keeps the total under the cap.

The workers are separate interpreters started with subprocess, like two worktrees' test runs.
They import only northwind_llm_replay and start together when a "go" file appears.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from northwind_llm_replay.ledger import Ledger

CAP = 0.60
WORST = 0.05
ATTEMPTS = 10  # per process; 2 x 10 x 0.05 = 1.00 would overshoot the 0.60 cap

WORKER = """
import sys, time
from pathlib import Path
from northwind_llm_replay.ledger import BudgetExceededError, Ledger

path, run_id, go = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
cap, worst, attempts = float(sys.argv[4]), float(sys.argv[5]), int(sys.argv[6])
lg = Ledger(path, budget_usd=cap, run_budget_usd=None, run_id=run_id, suite="mp")
deadline = time.monotonic() + 30
while not go.exists():
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.005)
admitted = 0
for i in range(attempts):
    try:
        res = lg.reserve(model="claude-haiku-5-5", worst_case_usd=worst, request_hash=str(i))
        admitted += 1
        if i % 2:
            lg.settle(res, None)  # half settle at worst case, half stay outstanding
    except BudgetExceededError:
        pass
print(admitted)
"""


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_two_processes_share_one_ledger_without_overshooting(tmp_path: Path) -> None:
    path = tmp_path / "shared" / "ledger.jsonl"
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-c",
                WORKER,
                str(path),
                f"wt{n}",
                str(go),
                str(CAP),
                str(WORST),
                str(ATTEMPTS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in (1, 2)
    ]
    time.sleep(0.5)  # both interpreters up and polling
    go.touch()
    admitted = 0
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        admitted += int(out.strip())
    total = Ledger(path, budget_usd=CAP, run_budget_usd=None, run_id="-", suite="-").totals()
    assert admitted == round(CAP / WORST)  # exactly 12 fit, none more
    assert total.spent_usd <= CAP + 1e-9
    assert total.spent_usd == pytest.approx(admitted * WORST)
