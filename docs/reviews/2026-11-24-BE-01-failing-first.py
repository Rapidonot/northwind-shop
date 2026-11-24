"""BE-01 failing-first for the review fixes (advisor finding 1). Run from the checkout root:

    uv run python <this file> >> docs/reviews/2026-11-23-BE-01-failing-first.log

For each fix: copy services/backend/src to a scratch dir, revert that one fix there (an exact string
swap, which must match once), and run that fix's tests with the copy first on PYTHONPATH. Every case
must fail. Then the same tests run once at the head, where they must pass. Optional arguments pick
cases whose name contains them. The collection run of the left-out test files against origin/main's
source is a separate command, saved with its output in the failing-first log.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd()
SRC = ROOT / "services/backend/src"
T = "services/backend/tests/orchestrator/"
RF = T + "test_be01_review_fixes.py::"
UNITS = T + "test_be01_units.py::"

CASES: list[tuple[str, str, str, str, list[str]]] = [
    (
        "M1 orders_check refuses an intent of another order",
        "northwind_backend/agents/orders_check.py",
        "if intent.order_id != task.order_id:",
        "if False:",
        [
            RF
            + "test_an_intent_of_another_order_is_refused_and_its_values_reach_neither_model_nor_answer"  # noqa: E501
        ],
    ),
    (
        "M1/N6 aggregate takes a foreign payment out",
        "northwind_backend/orchestrator/aggregate.py",
        "sections = [bound_to_its_order(s) for s in sections]",
        "sections = list(sections)",
        [RF + "test_a_payment_under_another_order_is_taken_out_of_the_one_result"],
    ),
    (
        "M2 the planner's run failures travel with a good plan",
        "northwind_backend/orchestrator/run.py",
        "    failures = plan.failures\n",
        "    failures = plan.failures if not concerns_of(plan) else ()\n",
        [RF + "test_a_planner_audit_gap_makes_the_run_partial_even_with_a_good_plan"],
    ),
    (
        "M3 every ID in the request must be covered",
        "northwind_backend/orchestrator/plan.py",
        "if not ids_in(request) <= covered:",
        "if False:",
        [RF + "test_a_plan_that_drops_an_id_the_request_names_is_refused"],
    ),
    (
        "N1 one canonical text (NFKC, Cf removed) for checks and model",
        "northwind_backend/orchestrator/plan.py",
        'return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")',
        "return request",
        [
            RF + "test_an_id_written_in_other_characters_counts_as_the_id_the_model_sees",
            RF + "test_a_fullwidth_low_line_makes_an_intent_id_too",
        ],
    ),
    (
        "L2/N7 the length limit holds on what the model reads",
        "northwind_backend/orchestrator/plan.py",
        "or len(_model_text(request)) > MAX_REQUEST_CHARS",
        "",
        [
            RF + "test_the_length_limit_holds_after_normalisation",
            RF + "test_the_length_limit_holds_on_what_the_model_reads_after_escaping",
        ],
    ),
    (
        "hard rule 6 the request is escaped inside its block",
        "northwind_backend/orchestrator/plan.py",
        "return html.escape(canonical(request), quote=False)",
        "return canonical(request)",
        [UNITS + "test_the_request_cannot_close_its_own_block_in_the_planner_prompt"],
    ),
    (
        "L1/R5 a cancelled subagent run writes its agent.run outcome",
        "northwind_backend/agents/runtime.py",
        '            audit.outcome(action_id, "agent.run", run_inputs, cancelled)\n',
        "            pass\n",
        [
            RF + "test_a_deadline_that_cancels_a_subagent_still_closes_its_run_in_the_audit",
            RF + "test_a_cancelled_run_counts_the_steps_that_finished_before_the_cancel",
        ],
    ),
    (
        "N4 a cancel from outside closes the run attempt (orchestrator.run)",
        "northwind_backend/orchestrator/run.py",
        '_close_cancelled(audit, action_id, "orchestrator.run", run_inputs)',
        "pass",
        [RF + "test_a_cancel_from_outside_closes_every_open_attempt"],
    ),
    (
        "N4 a cancel from outside closes the task attempts (orchestrator.task)",
        "northwind_backend/orchestrator/run.py",
        '_close_cancelled(audit, action_id, "orchestrator.task", inputs, labels)',
        "pass",
        [RF + "test_a_cancel_from_outside_closes_every_open_attempt"],
    ),
]


def pytest(nodes: list[str], pythonpath: str | None) -> tuple[int, str]:
    env = dict(os.environ)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    argv = ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", *nodes]
    proc = subprocess.run(  # noqa: S603 - fixed argv; the node IDs come from this file
        argv,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = [line for line in proc.stdout.splitlines() if " passed" in line or " failed" in line]
    return proc.returncode, (tail[-1] if tail else proc.stdout.strip().splitlines()[-1:][0])


def main() -> int:
    git = ["git", "rev-parse", "--short", "HEAD"]
    head = subprocess.run(git, capture_output=True, text=True)  # noqa: S603
    print(f"\n# Review-fix failing-first (advisor finding 1), at head {head.stdout.strip()}")
    bad = 0
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        picked = [c for c in CASES if not sys.argv[1:] or any(a in c[0] for a in sys.argv[1:])]
        for name, rel, old, new, nodes in picked:
            copy = Path(tmp) / "src"
            if copy.exists():
                shutil.rmtree(copy)
            shutil.copytree(SRC, copy)
            target = copy / rel
            text = target.read_text()
            if text.count(old) != 1:
                print(f"- {name}: SWAP DID NOT MATCH ONCE ({text.count(old)}); not run")
                bad += 1
                continue
            target.write_text(text.replace(old, new))
            code, summary = pytest(nodes, str(copy))
            verdict = "fails as it should" if code != 0 else "DID NOT FAIL"
            bad += code == 0
            print(f"- {name} (fix reverted in {rel}): {summary} -> {verdict}")
            code, summary = pytest(nodes, None)
            bad += code != 0
            print(f"  at head: {summary}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
