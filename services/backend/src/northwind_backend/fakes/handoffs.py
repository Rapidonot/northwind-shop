"""HandoffQueuePort fake: the staff handoff queue (row #23, SA-06; a stub for `mcp-cases`, row #19).

Two writes, each with an idempotency key and `dry_run` (hard rule 9):
- `open_handoff` reserves a reference (`H000001`, ...) for an identity. Its fingerprint is the
  identity, so the same key always gets the first reference back: one open handoff per identity.
- `file_case` queues the built case once, under its reference. Its fingerprint is the case's
  code-filled fields, so a retry whose summary differs gets the first case back.
Card data anywhere in a case is refused before anything is stored or hashed, as in `fakes/cases.py`.
The queue lives on a `HandoffState` that several instances can share.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, replace
from typing import Any

from northwind_backend.errors import Ok, Result, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    FakeState,
    card_data_in,
)
from northwind_backend.handoff import REFERENCE, HandoffCase, Identity
from northwind_common.cards import argument_texts, contains_card_data, holds_card_data

HANDOFF_SCOPES = frozenset({"open_handoff", "file_case"})
UNKNOWN_REFERENCE = validation("unknown_reference", "No open handoff has that reference.")


class HandoffState(FakeState):
    def __init__(self) -> None:
        super().__init__()
        self.opened: dict[str, Identity] = {}
        self.cases: dict[str, HandoffCase] = {}


def _code_fields(case: HandoffCase) -> dict[str, Any]:
    """What idempotency compares: everything code filled in. The summary, the correlation ID and
    `summary_missing` are left out, as in `CaseDraft.code_fields`."""
    body = asdict(case)
    for name in ("summary", "summary_source", "correlation_id", "dry_run"):
        body.pop(name)
    body["flags"] = [flag for flag in case.flags if flag != "summary_missing"]
    return body


def _holds_card_data(case: HandoffCase) -> bool:
    body = asdict(case)
    texts = list(argument_texts(body))
    return (
        any(
            contains_card_data(text) or contains_card_data(unicodedata.normalize("NFKC", text))
            for text in texts
        )
        or card_data_in(*texts)
        or holds_card_data(body)
    )


class HandoffQueueFake(FakeService):
    def __init__(self, *, state: HandoffState, scopes: frozenset[str] = HANDOFF_SCOPES) -> None:
        super().__init__(scopes, state)
        self.state = state

    def open_handoff(
        self, *, idempotency_key: str, identity: Identity, dry_run: bool = False
    ) -> Result[str]:
        refused = self._admit("open_handoff", idempotency_key, identity.ref)
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[str]:
            if dry:
                # Security review L3: a dry run predicts the real call, which returns the identity's
                # open reference if it has one.
                for reference, opened in self.state.opened.items():
                    if opened == identity:
                        return Ok(reference)
            reference = f"H{len(self.state.opened) + 1:06d}"
            if not dry:
                self.state.opened[reference] = identity
                self._effects.append(Effect("open_handoff", reference))
            return Ok(reference)

        return self._commit("open_handoff", idempotency_key, asdict(identity), dry_run, effect)

    def file_case(
        self, *, idempotency_key: str, case: HandoffCase, dry_run: bool = False
    ) -> Result[HandoffCase]:
        refused = self._admit("file_case", idempotency_key) or (
            CARD_DATA_REFUSED if _holds_card_data(case) else None
        )
        if refused is not None:
            return refused
        if not REFERENCE.fullmatch(case.reference) or (
            self.state.opened.get(case.reference) != case.identity
        ):
            return UNKNOWN_REFERENCE

        def effect(dry: bool) -> Result[HandoffCase]:
            filed = replace(case, dry_run=dry)
            if not dry:
                self.state.cases[case.reference] = filed
                self._effects.append(Effect("file_case", case.reference))
            return Ok(filed)

        return self._commit("file_case", idempotency_key, _code_fields(case), dry_run, effect)


__all__ = ["HANDOFF_SCOPES", "HandoffQueueFake", "HandoffState", "UNKNOWN_REFERENCE"]
