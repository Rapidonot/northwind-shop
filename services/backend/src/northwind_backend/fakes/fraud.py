"""FraudPort fake. Scores come from a hash of the IDs, so the same order always scores the same.

Orders in `flagged_order_ids` are sent to review, which is how the generated fraud-flag orders reach
it. An order is assessed once: a second key for it is a business error.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from northwind_backend.errors import Ok, Result, ToolError, business, validation
from northwind_backend.fakes._base import (
    CARD_DATA_REFUSED,
    Effect,
    FakeService,
    card_data_in,
    check_order_id,
    matches,
)
from northwind_backend.ports import FraudAssessment, FraudDecision

FRAUD_SCOPES = frozenset({"assess", "read"})
DRY_RUN_ASSESSMENT_ID = "fa_dry_run"
FLAGGED_SCORE = 92
MAX_CLEAN_SCORE = 39

_SHOPPER_ID = re.compile(r"S\d{4}", re.ASCII)
BAD_SHOPPER = validation("invalid_shopper_id", "The shopper ID is malformed.")
ALREADY_ASSESSED = business("already_assessed", "This order has already been assessed.")


class FraudFake(FakeService):
    def __init__(
        self,
        *,
        flagged_order_ids: Iterable[str] = (),
        scopes: frozenset[str] = FRAUD_SCOPES,
    ) -> None:
        super().__init__(scopes)
        self._flagged = frozenset(flagged_order_ids)
        self._assessments: dict[str, FraudAssessment] = {}

    def assess(
        self, *, idempotency_key: str, order_id: str, shopper_id: str, dry_run: bool = False
    ) -> Result[FraudAssessment]:
        refused = (
            self._admit("assess", idempotency_key, order_id, shopper_id)
            or check_order_id(order_id)
            or _check_shopper(shopper_id)
        )
        if refused is not None:
            return refused

        def effect(dry: bool) -> Result[FraudAssessment]:
            if order_id in self._assessments:
                return ALREADY_ASSESSED
            assessment_id = DRY_RUN_ASSESSMENT_ID if dry else f"fa_{len(self._assessments) + 1:06d}"
            assessment = self._score(assessment_id, order_id, shopper_id, dry)
            if not dry:
                self._assessments[order_id] = assessment
                self._effects.append(Effect("assess", assessment_id))
            return Ok(assessment)

        request = {"order_id": order_id, "shopper_id": shopper_id}
        return self._commit("assess", idempotency_key, request, dry_run, effect)

    def assessments_for(self, order_id: str) -> Result[tuple[FraudAssessment, ...]]:
        refused = self._reachable("read")
        if refused is not None:
            return refused
        if card_data_in(order_id):
            return CARD_DATA_REFUSED
        bad = check_order_id(order_id)
        if bad is not None:
            return bad
        found = self._assessments.get(order_id)
        return Ok(() if found is None else (found,))

    def _score(
        self, assessment_id: str, order_id: str, shopper_id: str, dry: bool
    ) -> FraudAssessment:
        if order_id in self._flagged:
            return FraudAssessment(
                assessment_id,
                order_id,
                shopper_id,
                FLAGGED_SCORE,
                FraudDecision.REVIEW,
                ("flagged_order",),
                dry,
            )
        digest = hashlib.sha256(f"{order_id}:{shopper_id}".encode()).hexdigest()
        score = int(digest[:8], 16) % (MAX_CLEAN_SCORE + 1)
        return FraudAssessment(
            assessment_id, order_id, shopper_id, score, FraudDecision.ALLOW, (), dry
        )


def _check_shopper(shopper_id: object) -> ToolError | None:
    return None if matches(_SHOPPER_ID, shopper_id) else BAD_SHOPPER
