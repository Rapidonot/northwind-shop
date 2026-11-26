"""The hedge report runs, and its positive control lists what it must (18a part 5; row #18, step 0
(d)).

The report (`docs/reviews/2026-11-25-30-w6-hedge-report.py.txt`, row #30's W6, N-2 and N-3) lists
every recorded sentence a hedge excused, for the advisor to read by eye; RECORD-ALL runs it after
the recording. Its control feeds three fixed sentences: two a hedge excuses ("Don't worry, ..."),
one it must not list. If the control lists anything but those two, the report cannot be trusted to
list anything, so this test runs the documented command with `--control` and checks the count. The
run without `--control` reads the committed cassettes and must finish; its count is not pinned,
because every re-recording changes it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.req("SA-04")  # row #30 wrote the report; row #18 has no PRD ID

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "reviews" / "2026-11-25-30-w6-hedge-report.py.txt"
TOTAL = re.compile(r"^answer calls (\d+), excused sentences (\d+)$")


def run(*args: str) -> tuple[list[str], tuple[int, int]]:
    done = subprocess.run(  # noqa: S603 - this interpreter, a committed script
        # -P: the script's folder (docs/reviews/) is not put on sys.path (security review L6).
        [sys.executable, "-P", str(REPORT), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    lines = done.stdout.splitlines()
    found = TOTAL.match(lines[-1]) if lines else None
    assert found, done.stdout[-2000:]
    return lines[:-1], (int(found.group(1)), int(found.group(2)))


def test_the_control_lists_exactly_its_two_hedged_sentences() -> None:
    rows, (calls, excused) = run("--control")
    assert (calls, excused) == (3, 2)
    assert len(rows) == 2
    listed = {row.split(" | ")[-1] for row in rows}
    assert len(listed) == 2, rows
    assert all(sentence.startswith("Don't worry") for sentence in listed), rows
    assert not any("It is warm" in row for row in rows)


def test_the_report_reads_the_committed_cassettes() -> None:
    rows, (calls, excused) = run()
    assert calls > 0
    assert excused == len(rows)
