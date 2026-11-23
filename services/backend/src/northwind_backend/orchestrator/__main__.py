"""Run one staff request through the orchestrator on the deterministic fakes (row #24, demo).

    uv run python -m northwind_backend.orchestrator "O000123: was it charged? Where is it?"

Model calls go through the LLM proxy (`make llm-proxy`, replay by default), never straight to the
API. The fakes are seeded from `data/backend/` with tracking and fraud flags only: no payment has
run, so a payment lookup names no intent and a fraud lookup finds no assessment yet, both valid
empty answers. Prints the one answer, or the full result with `--json`; `--audit FILE` keeps the
audit events as JSONL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from northwind_backend.audit import AuditSink, InMemoryAuditSink, JsonlAuditSink
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import PaymentFake
from northwind_backend.generate import fraud_flagged_order_ids
from northwind_backend.orchestrator.run import Ports, orchestrate
from northwind_backend.ports import TrackingEvent

DATA = Path("data/backend")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def fixture_ports(data: Path = DATA) -> Ports:
    rows = _jsonl(data / "orders.jsonl")
    tracking = {
        row["tracking_number"]: tuple(TrackingEvent(e["on"], e["status"]) for e in row["events"])
        for row in _jsonl(data / "tracking.jsonl")
    }
    return Ports(
        PaymentFake(),
        FraudFake(flagged_order_ids=fraud_flagged_order_ids(rows)),
        CarrierFake(tracking=tracking),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("request", help="the staff request, in plain words")
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--json", action="store_true", help="print the full structured result")
    parser.add_argument("--audit", type=Path, help="append the audit events to this JSONL file")
    args = parser.parse_args(argv)
    sink: AuditSink = JsonlAuditSink(args.audit) if args.audit else InMemoryAuditSink()
    result = asyncio.run(orchestrate(args.request, ports=fixture_ports(args.data), sink=sink))
    print(json.dumps(result.to_envelope(), indent=2) if args.json else result.answer)
    return 0 if result.status.value != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
