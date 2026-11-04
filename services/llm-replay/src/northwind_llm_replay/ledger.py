"""Spend ledger with a reservation-based budget guard (FOUND-2, NFR-16).

Every live call is admitted under a lock (thread lock + `flock` across processes):
committed spend + outstanding reservations + this call's worst case must fit both the total
budget (`LLM_BUDGET_USD`) and the run budget (`LLM_RUN_BUDGET_USD`). If it fits, the worst
case is reserved before the call goes upstream. When the call completes, the reservation is
settled to the actual cost from `usage`. A call that ends without final usage (a dropped stream,
an upstream error, a failure after the connection opened) is settled at its full worst case. Only
a call that provably never reached upstream is released at zero.

The ledger is an append-only JSONL file (gitignored). A reservation with no settlement, for
example after a crash, keeps counting at its reserved amount.
"""

from __future__ import annotations

import fcntl
import json
import math
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from northwind_llm_replay.pricing import cost_usd

# Suites whose calls do not count toward the 20-call budget checkpoint (decision 0001 §6).
CHECKPOINT_EXCLUDED_SUITES = frozenset({"spike"})


class BudgetExceededError(Exception):
    """The call would push spend past a budget cap. Nothing was sent upstream."""


# Generated IDs must not look like card numbers to the scanner: digit-free reservation IDs, and a
# hash prefix too short to hold the 13 digits the PAN check starts at.
_DIGITS_TO_LETTERS = str.maketrans("0123456789", "ghijklmnop")
HASH_PREFIX_CHARS = 12


def new_id() -> str:
    return uuid.uuid4().hex.translate(_DIGITS_TO_LETTERS)


@dataclass(frozen=True)
class Reservation:
    id: str
    model: str
    amount: float
    suite: str
    run_id: str
    request_hash: str


@dataclass(frozen=True)
class Totals:
    spent_usd: float  # settled costs + outstanding reservations, all runs
    run_spent_usd: float  # the same, for this run only
    outstanding_usd: float  # reservations not yet settled (all runs)
    settled_calls: int
    checkpoint_calls: int  # settled calls outside CHECKPOINT_EXCLUDED_SUITES
    by_suite: dict[str, float]
    by_model: dict[str, float]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _checked_cap(name: str, value: float) -> float:
    """NaN compares false against everything, so a NaN cap would admit any call (review #1)."""
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number, not {value!r}")
    return value


class Ledger:
    def __init__(
        self,
        path: Path,
        *,
        budget_usd: float,
        run_budget_usd: float | None,
        run_id: str,
        suite: str,
    ) -> None:
        self.path = path
        self.budget_usd = _checked_cap("LLM_BUDGET_USD", budget_usd)
        self.run_budget_usd = (
            None if run_budget_usd is None else _checked_cap("LLM_RUN_BUDGET_USD", run_budget_usd)
        )
        self.run_id = run_id
        self.suite = suite
        self._thread_lock = threading.Lock()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        with self._thread_lock, lock_path.open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def _append(self, entry: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()

    def _totals_unlocked(self) -> Totals:
        reserved: dict[str, dict[str, Any]] = {}
        settled: dict[str, dict[str, Any]] = {}
        for e in self._entries():
            if e["type"] == "reserve":
                reserved[e["id"]] = e
            elif e["type"] == "settle":
                settled[e["id"]] = e
        spent = run_spent = outstanding = 0.0
        checkpoint = 0
        by_suite: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for rid, r in reserved.items():
            s = settled.get(rid)
            amount = float(s["cost_usd"]) if s else float(r["amount_usd"])
            if not s:
                outstanding += amount
            elif r["suite"] not in CHECKPOINT_EXCLUDED_SUITES:
                checkpoint += 1
            spent += amount
            if r["run_id"] == self.run_id:
                run_spent += amount
            by_suite[r["suite"]] = by_suite.get(r["suite"], 0.0) + amount
            by_model[r["model"]] = by_model.get(r["model"], 0.0) + amount
        return Totals(
            spent_usd=spent,
            run_spent_usd=run_spent,
            outstanding_usd=outstanding,
            settled_calls=len(settled),
            checkpoint_calls=checkpoint,
            by_suite=by_suite,
            by_model=by_model,
        )

    def totals(self) -> Totals:
        with self._locked():
            return self._totals_unlocked()

    def reserve(self, *, model: str, worst_case_usd: float, request_hash: str) -> Reservation:
        """Admit a call or raise BudgetExceededError. Admission and reservation are one step
        under the lock, so concurrent callers cannot jointly overshoot a cap."""
        with self._locked():
            t = self._totals_unlocked()
            if t.spent_usd + worst_case_usd > self.budget_usd:
                raise BudgetExceededError(
                    f"LLM_BUDGET_USD {self.budget_usd:.2f} would be exceeded: spent "
                    f"{t.spent_usd:.4f} + worst case {worst_case_usd:.4f}"
                )
            if (
                self.run_budget_usd is not None
                and t.run_spent_usd + worst_case_usd > self.run_budget_usd
            ):
                raise BudgetExceededError(
                    f"LLM_RUN_BUDGET_USD {self.run_budget_usd:.2f} would be exceeded: run spent "
                    f"{t.run_spent_usd:.4f} + worst case {worst_case_usd:.4f}"
                )
            res = Reservation(
                id=new_id(),
                model=model,
                amount=worst_case_usd,
                suite=self.suite,
                run_id=self.run_id,
                request_hash=request_hash,
            )
            self._append(
                {
                    "type": "reserve",
                    "id": res.id,
                    "ts": now_iso(),
                    "model": model,
                    "amount_usd": worst_case_usd,
                    "suite": res.suite,
                    "run_id": res.run_id,
                    "request_hash": request_hash[:HASH_PREFIX_CHARS],
                }
            )
            return res

    def settle(self, res: Reservation, usage: Mapping[str, Any] | None) -> float:
        """Record the actual cost. `usage=None` means final usage never arrived: charge the
        reserved worst case."""
        cost = res.amount if usage is None else cost_usd(res.model, usage)
        with self._locked():
            self._append(
                {
                    "type": "settle",
                    "id": res.id,
                    "ts": now_iso(),
                    "cost_usd": cost,
                    "usage": dict(usage) if usage is not None else None,
                    "final_usage": usage is not None,
                }
            )
        return cost
