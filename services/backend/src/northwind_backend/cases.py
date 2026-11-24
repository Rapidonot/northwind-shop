"""The escalation case and the case queue's port (row #26, BE-07; PRD §7 `northwind-cases`).

A case is what staff act on without the transcript: the order, the amounts, the root cause, the
recommended action and a summary. Code fills in every field but the summary, from tool data: the
order store, the catalog's live prices and the action table (hard rule 2). The summary is the Care
agent's, written from an untrusted transcript, kept as one line of plain text and labelled by
`summary_source`.

`mcp-cases` doesn't exist yet, so the queue is this port with an in-memory fake
(`fakes/cases.py`). A case never holds transcript text, a payment token, a postal code or card data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from northwind_backend.errors import Result

CASE_ID = re.compile(r"case_\d{6}", re.ASCII)
SummarySource = Literal["model", "code"]
SUMMARY_LABEL = {
    "model": "written by the Care agent (an AI model) from an untrusted transcript",
    "code": "no usable summary from the Care agent; act on the fields above",
}
NO_SUMMARY = "No summary: the Care agent gave none that could be used. Act on the fields above."


@dataclass(frozen=True, slots=True)
class CaseAmounts:
    """Minor units. `order_total_minor` is the lines at the catalog's live prices, None if a price
    could not be read; the payment fields are what the order's payment claim last reported."""

    order_total_minor: int | None
    authorized_minor: int
    captured_minor: int
    currency: str | None
    payment_status: str | None


@dataclass(frozen=True, slots=True)
class CaseDraft:
    order_id: str
    shopper_id: str
    region: str
    order_status: str
    root_cause: str
    recommended_action: str
    amounts: CaseAmounts
    summary: str
    summary_source: SummarySource
    flags: tuple[str, ...]
    correlation_id: str

    def code_fields(self) -> dict[str, Any]:
        """What the queue's idempotency compares: only what code filled in. The summary and the
        correlation ID are left out, so a retry whose model wrote other words gets the first case
        back, summary included (the user's change 1 to the plan)."""
        return {
            "order_id": self.order_id,
            "shopper_id": self.shopper_id,
            "region": self.region,
            "order_status": self.order_status,
            "root_cause": self.root_cause,
            "recommended_action": self.recommended_action,
            "amounts": asdict(self.amounts),
            "flags": list(self.flags),
        }


@dataclass(frozen=True, slots=True)
class Case(CaseDraft):
    case_id: str
    dry_run: bool = False

    @classmethod
    def from_draft(cls, draft: CaseDraft, *, case_id: str, dry_run: bool) -> Case:
        return cls(
            order_id=draft.order_id,
            shopper_id=draft.shopper_id,
            region=draft.region,
            order_status=draft.order_status,
            root_cause=draft.root_cause,
            recommended_action=draft.recommended_action,
            amounts=draft.amounts,
            summary=draft.summary,
            summary_source=draft.summary_source,
            flags=draft.flags,
            correlation_id=draft.correlation_id,
            case_id=case_id,
            dry_run=dry_run,
        )

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["flags"] = list(self.flags)
        body["summary_label"] = SUMMARY_LABEL[self.summary_source]
        return body

    def render(self) -> str:
        """The case as staff read it: plain text, every field on its own line."""
        a = self.amounts
        cur = a.currency or ""
        total = "unknown" if a.order_total_minor is None else f"{a.order_total_minor} {cur}"
        lines = [
            f"Case {self.case_id} for order {self.order_id} "
            f"(shopper {self.shopper_id}, {self.region}, {self.order_status})",
            f"Root cause: {self.root_cause}",
            f"Recommended action: {self.recommended_action}",
            f"Order total (minor units, live prices): {total}",
            f"Payment: {a.payment_status or 'none reported'}; authorized {a.authorized_minor}, "
            f"captured {a.captured_minor} {cur}".rstrip(),
            f"Flags: {', '.join(self.flags) or 'none'}",
            f"Summary ({SUMMARY_LABEL[self.summary_source]}): {self.summary}",
            f"Correlation ID: {self.correlation_id}",
        ]
        return "\n".join(lines)


def case_key(order_id: str, root_cause: str) -> str:
    """The queue's idempotency key, from the task's order ID and the code-derived root cause only
    (hard rule 9)."""
    return f"care:{order_id}:{root_cause}"


def missing_for_staff(case: CaseDraft) -> list[str]:
    """What staff would lack to act without the transcript; empty means actionable. A missing
    summary is fine when the case says so (`summary_missing`)."""
    missing = [
        name
        for name in ("order_id", "shopper_id", "region", "root_cause", "recommended_action")
        if not getattr(case, name)
    ]
    if not case.summary.strip() or (
        case.summary_source == "code" and "summary_missing" not in case.flags
    ):
        missing.append("summary")
    if case.amounts.order_total_minor is None and "total_unavailable" not in case.flags:
        missing.append("amounts")
    return missing


@runtime_checkable
class CasePort(Protocol):
    def create_case(
        self, *, idempotency_key: str, case: CaseDraft, dry_run: bool = False
    ) -> Result[Case]:
        """Queues the case for staff. The same key with the same code-filled fields returns the
        first case; with other code-filled fields it is an `idempotency_conflict`."""
        ...
