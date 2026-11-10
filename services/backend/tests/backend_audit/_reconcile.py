"""The one comparison rule (S1) every reconstruction test applies (row review F3).

A record matches the fakes' ground truth when:
- every confirmed field (fraud, payment status and amounts, shipment) equals the truth;
- an `uncertain` entry is listed with its amount (where the action moves money), and is never
  counted as done: confirmed state never shows more than happened;
- the only way confirmed state may show less than happened is an `uncertain` entry still `open`
  for that action: an action that happened is never reported as absent (R1), and a `not_done`
  settlement must agree with the truth;
- the payment label carries the doubt whenever the confirmed payment state differs from the truth.

Ground truth is read from the fakes' own records, test-only, so a fault still injected on a read
can't hide it.
"""

from __future__ import annotations

from northwind_backend.reconstruct import OrderRecord

from ._driver import Fakes

NO_AMOUNT = frozenset({"payment.void", "fraud.assess", "carrier.create_shipment"})


def assert_reconciles(record: OrderRecord, fakes: Fakes) -> None:
    order_id = record.order_id
    for entry in record.uncertain:
        assert entry.settled in ("open", "done", "not_done"), entry
        if entry.action not in NO_AMOUNT:
            assert entry.amount_minor is not None, f"{entry.action} uncertain with no amount"

    def still_open(action: str) -> bool:
        return any(u.action == action and u.settled == "open" for u in record.uncertain)

    # Fraud.
    assessment = fakes.fraud._assessments.get(order_id)
    if assessment is None:
        assert record.fraud_decision is None, "a fraud decision that never happened"
    elif record.fraud_decision is None:
        assert still_open("fraud.assess"), "an assessment that happened is reported as absent"
    else:
        assert (record.fraud_decision, record.fraud_score) == (
            assessment.decision.value,
            assessment.score,
        )

    # Payment.
    intents = [i for i in fakes.payment._intents.values() if i.order_id == order_id]
    if not intents:
        assert record.intent_id is None and record.payment_status is None
        assert (record.captured_minor, record.refunded_minor) == (0, 0)
    elif record.intent_id is None:
        assert still_open("payment.authorize"), (
            "an authorization that happened is reported as absent"
        )
        assert "uncertain" in record.payment_label
    else:
        truth = intents[-1]
        assert record.intent_id == truth.intent_id
        assert (record.currency, record.authorized_minor) == (
            truth.currency,
            truth.authorized_minor,
        )
        # Never counted as done beyond what happened.
        assert record.captured_minor <= truth.captured_minor, (
            "a capture counted that never happened"
        )
        assert record.refunded_minor <= truth.refunded_minor, "a refund counted that never happened"
        if record.captured_minor != truth.captured_minor:
            assert still_open("payment.capture"), "a capture that happened is reported as absent"
        if record.refunded_minor != truth.refunded_minor:
            assert still_open("payment.refund"), "a refund that happened is reported as absent"
        if record.payment_status != truth.status.value:
            assert any(
                still_open(a) for a in ("payment.capture", "payment.void", "payment.refund")
            ), f"status {record.payment_status} against {truth.status.value} with nothing open"
            assert "uncertain" in record.payment_label

    # Shipment.
    shipment = fakes.carrier._shipments.get(order_id)
    if shipment is None:
        assert record.shipment_id is None, "a shipment that never happened"
    elif record.shipment_id is None:
        assert still_open("carrier.create_shipment"), (
            "a shipment that happened is reported as absent"
        )
    else:
        assert (record.shipment_id, record.tracking_number) == (
            shipment.shipment_id,
            shipment.tracking_number,
        )
