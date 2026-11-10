"""BE-02 and BE-08 on Haiku, recorded once in the user's `backend-audit` window and replayed after
(row #20; decision 0013). `make record SUITE=backend-audit` runs these; `make test` replays them.

A recorded status that doesn't match the table is stop 7: never a re-record loop, and never an edit
to the expected status (that would be stop 5).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from northwind_backend.agents.orders_check import ACTOR, OrdersCheckTask, run_orders_check
from northwind_backend.audit import InMemoryAuditSink
from northwind_backend.errors import RunStatus
from northwind_backend.fakes._base import FaultKind
from northwind_backend.reconstruct import reconstruct_order
from northwind_backend.recovery import RetryPolicy
from northwind_common.llm import agent_runtime
from northwind_llm_replay.pytest_plugin import LLMProxyHandle

from ._driver import Fakes, context, first_of, orders, run_order, setup_captured, setup_unassessed

pytestmark = [
    pytest.mark.req("BE-02"),
    pytest.mark.req("BE-08"),
    pytest.mark.llm_suite("backend-audit"),
]

POLICY = RetryPolicy(max_attempts=3, deadline_seconds=5.0, backoff_seconds=())

# (run, order kind, fault, expected status), the table in the plan's LLM section.
SCENARIOS = [
    (1, "clean", None, RunStatus.COMPLETE),
    (2, "clean", "fraud_once", RunStatus.COMPLETE),
    (3, "clean", "fraud_always", RunStatus.PARTIAL),
    (4, "clean", "payment_outage", RunStatus.PARTIAL),
    (5, "fraud_review", None, RunStatus.COMPLETE),
    (6, "fraud_review", "fraud_once", RunStatus.COMPLETE),
    (7, "fraud_review", "fraud_always", RunStatus.FAILED),
    (8, "soft_decline", None, RunStatus.COMPLETE),
    (9, "soft_decline", "fraud_once", RunStatus.COMPLETE),
    (10, "soft_decline", "fraud_always", RunStatus.FAILED),
    (11, "unassessed", None, RunStatus.COMPLETE),
    (12, "unassessed", "fraud_outage", RunStatus.PARTIAL),
]
EXCEPTION = {
    "clean": None,
    "unassessed": None,
    "fraud_review": "fraud_flag",
    "soft_decline": "soft_decline",
}


@pytest.mark.parametrize(
    ("run", "kind", "fault", "expected"), SCENARIOS, ids=[f"run{s[0]}" for s in SCENARIOS]
)
def test_recorded_scenario(
    llm_proxy: LLMProxyHandle,
    tmp_path: Path,
    run: int,
    kind: str,
    fault: str | None,
    expected: RunStatus,
) -> None:
    row = first_of(orders(), EXCEPTION[kind])
    fakes = Fakes.for_orders([row])
    sink = InMemoryAuditSink()
    intent_id: str | None = None
    if kind == "clean":
        intent_id = setup_captured(row, fakes, sink, assess=True)
    elif kind == "unassessed":
        intent_id = setup_unassessed(row, fakes, sink)
    else:
        run_order(row, fakes, sink)
    if fault == "fraud_once":
        fakes.fraud.faults.inject("read", FaultKind.TIMEOUT_BEFORE, times=1)
    elif fault == "fraud_always":
        fakes.fraud.faults.inject("read", FaultKind.TIMEOUT_BEFORE)
    elif fault == "fraud_outage":
        fakes.fraud.faults.inject("read", FaultKind.OUTAGE)
    elif fault == "payment_outage":
        fakes.payment.faults.inject("status", FaultKind.OUTAGE)
    effects_before = (fakes.payment.effects, fakes.fraud.effects, fakes.carrier.effects)

    result = asyncio.run(
        run_orders_check(
            OrdersCheckTask(order_id=row["order_id"], intent_id=intent_id),
            payment=fakes.payment,
            fraud=fakes.fraud,
            audit=context(sink, row["order_id"]),
            correlation_id=f"run-{run}",
            runtime=agent_runtime(root=tmp_path),
            policy=POLICY,
        )
    )
    assert result.status is expected, (run, result.to_envelope())
    assert result.summary, run
    record = reconstruct_order(sink.read_all(), row["order_id"])
    assert f"run-{run}" in record.agent_runs
    assert {e.actor for e in sink.read_all() if e.correlation_id == f"run-{run}"} == {ACTOR}
    # A read-only agent: its run changed nothing anywhere.
    assert (fakes.payment.effects, fakes.fraud.effects, fakes.carrier.effects) == effects_before
