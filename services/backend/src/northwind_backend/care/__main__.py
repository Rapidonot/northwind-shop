"""Build and queue one escalation case on the deterministic fakes (row #26, BE-07, demo).

    uv run python -m northwind_backend.care O000002 transcript.json --escalated-as fraud_review

`transcript.json` is a list of turns, each `{"role": "shopper" | "assistant", "text": ...}`.
`--escalated-as` first escalates the order on the fake with that reason code, as the Orders agent
would have. Model calls go through the LLM proxy (`make llm-proxy`, replay by default), never
straight to the API. Prints the case as staff read it, or the full result with `--json`;
`--audit FILE` keeps the audit events as JSONL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from northwind_backend.agents.care import CarePorts, CareTask, Turn, run_care
from northwind_backend.audit import AuditContext, AuditSink, InMemoryAuditSink, JsonlAuditSink
from northwind_backend.errors import Ok
from northwind_backend.fakes.cases import CaseQueueFake, CaseState
from northwind_backend.fakes.orders import OrderState, OrderStoreFake

DATA = Path("data/backend")


def fixture_ports(data: Path = DATA) -> CarePorts:
    return CarePorts(
        orders=OrderStoreFake(state=OrderState.from_data(data)),
        cases=CaseQueueFake(state=CaseState()),
    )


def load_transcript(path: Path) -> tuple[Turn, ...]:
    """A JSON list of `{"role", "text"}` turns; anything else is a usage error, never a traceback.
    The message names the problem only, never the file's text."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError
        return tuple(Turn(row["role"], row["text"]) for row in rows)
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(
            "the transcript file must hold a JSON list of turns, each with role and text"
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("order_id", help="the order, like O000002")
    parser.add_argument("transcript", type=Path, help="a JSON list of turns")
    parser.add_argument("--escalated-as", help="escalate the order first, with this reason code")
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--json", action="store_true", help="print the full structured result")
    parser.add_argument("--audit", type=Path, help="append the audit events to this JSONL file")
    args = parser.parse_args(argv)
    ports = fixture_ports(args.data)
    if args.escalated_as:
        done = ports.orders.escalate(
            idempotency_key=f"{args.order_id}:escalate",
            order_id=args.order_id,
            reason=args.escalated_as,
            context={"reason": args.escalated_as},
        )
        if not isinstance(done, Ok):
            raise SystemExit(f"could not escalate the order: {done.code}")
    sink: AuditSink = JsonlAuditSink(args.audit) if args.audit else InMemoryAuditSink()
    audit = AuditContext(sink, correlation_id="care-demo", actor="staff-cli")
    try:
        task = CareTask(args.order_id, load_transcript(args.transcript))
    except ValueError:
        raise SystemExit(
            "the order ID must look like O000123, with at most 20 turns of at most 20,000 "
            "characters each"
        ) from None
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    result = asyncio.run(run_care(task, ports=ports, audit=audit, correlation_id=run_id))
    if args.json:
        print(json.dumps(result.to_envelope(), indent=2))
    elif result.case is not None:
        print(result.case.render())
    else:
        print(f"No case queued ({result.status.value}):")
        for failure in result.failures:
            then = ", ".join(failure.alternatives)
            print(f"- {failure.attempted_action}: {failure.code}; then {then}")
    return 0 if result.case is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
