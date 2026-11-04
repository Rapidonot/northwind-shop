"""Reservation-based budget guard (FOUND-2, NFR-16 budget breach; user change [3])."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from northwind_llm_replay.ledger import BudgetExceededError, Ledger


def ledger(
    tmp_path: Path,
    *,
    budget: float = 1.0,
    run: float | None = None,
    run_id: str = "r1",
    suite: str = "s",
) -> Ledger:
    return Ledger(
        tmp_path / "ledger.jsonl", budget_usd=budget, run_budget_usd=run, run_id=run_id, suite=suite
    )


USAGE = {"input_tokens": 1_000, "output_tokens": 100}  # Sonnet: $0.003


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0])
@pytest.mark.parametrize("cap", ["budget", "run"])
def test_non_finite_or_negative_caps_are_rejected(tmp_path: Path, cap: str, bad: float) -> None:
    """Review #1: `budget_usd=nan` used to admit a $1e9 reservation."""
    with pytest.raises(ValueError, match="finite, non-negative"):
        ledger(tmp_path, **{cap: bad})  # type: ignore[arg-type]
    assert ledger(tmp_path, budget=0.0, run=0.0).budget_usd == 0.0  # zero stays a valid "no spend"


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_reserve_then_settle_charges_actual_cost(tmp_path: Path) -> None:
    lg = ledger(tmp_path)
    res = lg.reserve(model="claude-sonnet-5-5", worst_case_usd=0.5, request_hash="h")
    assert lg.totals().spent_usd == pytest.approx(0.5)
    assert lg.totals().outstanding_usd == pytest.approx(0.5)
    cost = lg.settle(res, USAGE)
    assert cost == pytest.approx(0.003)
    t = lg.totals()
    assert t.spent_usd == pytest.approx(0.003)
    assert t.outstanding_usd == 0
    assert t.settled_calls == 1


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_budget_breach_is_refused_before_any_call(tmp_path: Path) -> None:
    lg = ledger(tmp_path, budget=1.0)
    res = lg.reserve(model="claude-sonnet-5-5", worst_case_usd=0.9, request_hash="h1")
    lg.settle(res, None)  # no final usage: charged the worst case, 0.9
    with pytest.raises(BudgetExceededError, match="LLM_BUDGET_USD"):
        lg.reserve(model="claude-sonnet-5-5", worst_case_usd=0.2, request_hash="h2")
    # Nothing was reserved for the refused call.
    assert lg.totals().spent_usd == pytest.approx(0.9)


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_run_budget_breach_is_refused_and_counts_only_this_run(tmp_path: Path) -> None:
    other = ledger(tmp_path, budget=10.0, run=0.5, run_id="old")
    other.settle(
        other.reserve(model="claude-sonnet-5-5", worst_case_usd=0.4, request_hash="h"), None
    )
    lg = ledger(tmp_path, budget=10.0, run=0.5, run_id="new")
    lg.reserve(
        model="claude-sonnet-5-5", worst_case_usd=0.4, request_hash="h1"
    )  # fits: run spent 0
    with pytest.raises(BudgetExceededError, match="LLM_RUN_BUDGET_USD"):
        lg.reserve(model="claude-sonnet-5-5", worst_case_usd=0.2, request_hash="h2")


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_missing_final_usage_is_charged_worst_case(tmp_path: Path) -> None:
    lg = ledger(tmp_path)
    res = lg.reserve(model="claude-haiku-5-5", worst_case_usd=0.07, request_hash="h")
    assert lg.settle(res, None) == pytest.approx(0.07)
    assert lg.totals().spent_usd == pytest.approx(0.07)


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_unsettled_reservation_survives_a_crash(tmp_path: Path) -> None:
    ledger(tmp_path).reserve(model="claude-sonnet-5-5", worst_case_usd=0.6, request_hash="h")
    restarted = ledger(tmp_path)  # a new process reading the same file
    assert restarted.totals().outstanding_usd == pytest.approx(0.6)
    with pytest.raises(BudgetExceededError):
        restarted.reserve(model="claude-sonnet-5-5", worst_case_usd=0.5, request_hash="h2")


@pytest.mark.req("FOUND-2")
@pytest.mark.req("NFR-16")
def test_concurrent_admissions_cannot_jointly_overshoot(tmp_path: Path) -> None:
    lg = ledger(tmp_path, budget=1.0)
    admitted: list[bool] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        try:
            lg.reserve(model="claude-sonnet-5-5", worst_case_usd=0.3, request_hash="h")
            admitted.append(True)
        except BudgetExceededError:
            admitted.append(False)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert admitted.count(True) == 3  # 3 x 0.3 = 0.9 <= 1.0; a 4th would be 1.2
    assert lg.totals().spent_usd == pytest.approx(0.9)


@pytest.mark.req("FOUND-2")
def test_spike_suite_is_excluded_from_the_20_call_checkpoint(tmp_path: Path) -> None:
    spike = ledger(tmp_path, budget=10.0, suite="spike")
    spike.settle(
        spike.reserve(model="claude-haiku-5-5", worst_case_usd=0.01, request_hash="h"), USAGE
    )
    real = ledger(tmp_path, budget=10.0, suite="answers")
    real.settle(
        real.reserve(model="claude-haiku-5-5", worst_case_usd=0.01, request_hash="h"), USAGE
    )
    t = real.totals()
    assert t.settled_calls == 2
    assert t.checkpoint_calls == 1
