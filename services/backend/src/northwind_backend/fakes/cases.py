"""CasePort fake: the staff case queue (row #26, BE-07; a stub for `mcp-cases`, PRD §7).

One write, `create_case`, with an idempotency key and `dry_run` (hard rule 9). Its fingerprint is
the case's code-filled fields only (`CaseDraft.code_fields`), so a retry whose model summary differs
returns the first case. Card data in any one field of a case, or split across neighbouring fields,
is refused before anything is stored or hashed; a number split across fields far apart is not caught
(a residual: only the summary is free text today; advisor F-A).
The queue lives on a `CaseState` that several instances can share.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict

from northwind_backend.cases import Case, CaseDraft
from northwind_backend.errors import Ok, Result
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    FakeState,
    card_data_in,
    check_order_id,
)
from northwind_common.cards import argument_texts, contains_card_data, holds_card_data

CASE_SCOPES = frozenset({"create_case"})


class CaseState(FakeState):
    def __init__(self) -> None:
        super().__init__()
        self.cases: list[Case] = []


class CaseQueueFake(FakeService):
    def __init__(self, *, state: CaseState, scopes: frozenset[str] = CASE_SCOPES) -> None:
        super().__init__(scopes, state)
        self.state = state

    def create_case(
        self, *, idempotency_key: str, case: CaseDraft, dry_run: bool = False
    ) -> Result[Case]:
        refused = (
            self._admit("create_case", idempotency_key)
            # Every leaf as written, never a JSON encoding, which hides split and non-ASCII digits
            # (security review L1).
            or (CARD_DATA_REFUSED if _holds_card_data(case) else None)
            or check_order_id(case.order_id)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[Case]:
            number = len(self.state.cases) + 1
            made = Case.from_draft(case, case_id=f"case_{number:06d}", dry_run=dry)
            if not dry:
                self.state.cases.append(made)
                self._effects.append(Effect("create_case", made.case_id))
            return Ok(made)

        return self._commit("create_case", idempotency_key, case.code_fields(), dry_run, effect)


def _holds_card_data(case: CaseDraft) -> bool:
    texts = list(argument_texts(asdict(case)))
    return (
        any(
            contains_card_data(text) or contains_card_data(unicodedata.normalize("NFKC", text))
            for text in texts
        )
        or card_data_in(*texts)
        or holds_card_data(asdict(case))
    )  # across fields too (re-review N6)
