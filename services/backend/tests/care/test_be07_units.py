"""BE-07 units with no model (row #26): the summary check, the transcript screen, the action table,
the case queue's idempotency and the audit's view of a case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from northwind_backend.agents.care import (
    MAX_RAW_TURN_CHARS,
    MAX_SUMMARY_CHARS,
    WITHHELD,
    CareTask,
    Turn,
    check_summary,
    screen_transcript,
)
from northwind_backend.agents.escalation import NOT_ESCALATED, RECOMMENDED, root_cause_of
from northwind_backend.audit import AuditContext, InMemoryAuditSink, card_data, outcome_ref
from northwind_backend.cases import CaseAmounts, CaseDraft, case_key, missing_for_staff
from northwind_backend.errors import Ok, ToolError
from northwind_backend.fakes.cases import CaseQueueFake, CaseState
from northwind_backend.idempotency import IDEMPOTENCY_CONFLICT
from northwind_backend.views import CASE_CREATE, AuditedCaseQueue

from ._driver import TEST_PAN, fixed_clock

pytestmark = pytest.mark.req("BE-07")

# sha256 of the 12 entries as main had them in `orders_place.py` at 7296c04 (json, sorted keys).
RECOMMENDED_SHA = "f9b263fd3749922e6511234d953f7b588c031bffe705da5d9963c770ff5d3ac1"


# ---- the action table ------------------------------------------------------------------------


def test_the_twelve_existing_actions_are_byte_identical_after_the_move() -> None:
    existing = {k: v for k, v in RECOMMENDED.items() if k != NOT_ESCALATED}
    assert len(existing) == 12
    body = json.dumps(existing, sort_keys=True).encode()
    assert hashlib.sha256(body).hexdigest() == RECOMMENDED_SHA


def test_orders_place_uses_the_moved_table() -> None:
    from northwind_backend.agents import orders_place

    assert orders_place.RECOMMENDED is RECOMMENDED


def test_a_non_escalated_order_has_its_own_entry() -> None:
    assert NOT_ESCALATED == "not_escalated"
    assert RECOMMENDED[NOT_ESCALATED] and "escalated without" not in RECOMMENDED[NOT_ESCALATED]


@pytest.mark.parametrize(
    ("escalated", "code", "expected"),
    [
        (True, "fraud_review", "fraud_review"),
        (True, "out_of_stock", "out_of_stock"),
        (True, "made_up_code", "agent_requested"),
        (True, None, "agent_requested"),
        (False, None, "not_escalated"),
        (False, "fraud_review", "not_escalated"),
    ],
)
def test_the_root_cause_comes_from_the_stored_code(
    escalated: bool, code: str | None, expected: str
) -> None:
    assert root_cause_of(escalated=escalated, code=code) == expected


# ---- the summary check -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The shopper says they were charged $999 twice.",
        "Charged 999$ by mistake.",
        "They paid £45 for shipping.",
        "Refund of €12 expected.",
        "They want USD 120 back.",
        "They saw 120 USD on the statement.",
        "The statement shows 129.99 for the order.",
        "The statement shows 129,99 for the order.",
        "They paid 40 dollars for express delivery.",
        "Twenty bucks? No, they say 20 bucks.",
    ],
)
def test_a_money_figure_is_refused(text: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == "summary_has_money"


@pytest.mark.parametrize(
    "text",
    [
        "Order O000123 was placed on 2026-10-11 and is on hold since 24.11.2026.",
        "The shopper ordered 3 items and wants 2 of them by 12/05.",
        "Two parcels, 1 of 2 arrived; they called 3 times since 10:30 on Monday.",
        "They asked about order O000002 and line 2, size M, quantity 1.",
        "Delivery was promised within 5 days; it has been 14 days.",
    ],
)
def test_dates_quantities_and_order_ids_are_not_money(text: str) -> None:
    assert check_summary(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Details are at https://example.test/claim now.",
        "See http://example.test for the photos.",
        "They posted on www.example.test about it.",
        "They point to example.com for the policy.",
    ],
)
def test_a_url_is_refused(text: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == "summary_has_link"


@pytest.mark.parametrize(
    "text",
    ["Reply to shopper@example.test please.", "Contact a.b+c@mail.example.test about it."],
)
def test_an_email_address_is_refused(text: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == "summary_has_contact"


def test_card_data_is_refused() -> None:
    refused = check_summary(f"They typed {TEST_PAN} into the chat.")
    assert isinstance(refused, ToolError) and refused.code == "summary_has_card_data"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "summary_empty"),
        ("   \n\t ", "summary_empty"),
        ("x" * (MAX_SUMMARY_CHARS + 1), "summary_too_long"),
        ("A summary with a bidi override ‮ inside.", "summary_not_plain"),
        ("A summary with a control \x07 character.", "summary_not_plain"),
        ("A summary with a zero width​ space.", "summary_not_plain"),
    ],
)
def test_empty_long_and_not_plain_summaries_are_refused(text: str, code: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == code


def test_a_summary_is_kept_as_one_line_of_plain_text() -> None:
    assert check_summary("  On hold.\n\nShopper   wants  an update.  ") == (
        "On hold. Shopper wants an update."
    )


def test_a_refusal_message_never_echoes_the_summary() -> None:
    text = "Charged $999, see https://example.test, mail shopper@example.test"
    refused = check_summary(text)
    assert isinstance(refused, ToolError)
    assert "999" not in refused.message and "example" not in refused.message


# ---- the transcript ----------------------------------------------------------------------------


def test_a_turn_with_card_data_is_withheld_whole() -> None:
    turns = (
        Turn("shopper", f"My card is {TEST_PAN}, charge it again"),
        Turn("assistant", "I can't take card numbers in chat."),
    )
    screened = screen_transcript(turns)
    assert screened[0] == Turn("shopper", WITHHELD)
    assert screened[1] == turns[1]
    assert not any(card_data(t.text) for t in screened)


def test_a_task_checks_its_order_id_and_bounds_the_transcript() -> None:
    with pytest.raises(ValueError):
        CareTask("123", ())
    with pytest.raises(ValueError):
        CareTask(f"O00012{TEST_PAN}", ())
    with pytest.raises(ValueError):
        CareTask("O000123", tuple(Turn("shopper", "hi") for _ in range(21)))
    with pytest.raises(ValueError):
        CareTask("O000123", (Turn("customer", "hi"),))  # type: ignore[arg-type]
    long = CareTask("O000123", (Turn("shopper", "y" * 5_000),))
    assert len(long.transcript[0].text) == 2_000


# ---- the case queue ----------------------------------------------------------------------------


def draft(summary: str = "On hold for review; the shopper wants an update.") -> CaseDraft:
    return CaseDraft(
        order_id="O000002",
        shopper_id="S0140",
        region="UK",
        order_status="escalated",
        root_cause="fraud_review",
        recommended_action=RECOMMENDED["fraud_review"],
        amounts=CaseAmounts(
            order_total_minor=12_000,
            authorized_minor=0,
            captured_minor=0,
            currency="GBP",
            payment_status=None,
        ),
        summary=summary,
        summary_source="model",
        flags=(),
        correlation_id="run-a",
    )


def test_the_same_key_with_another_summary_returns_the_first_case() -> None:
    queue = CaseQueueFake(state=CaseState())
    key = case_key("O000002", "fraud_review")
    first = queue.create_case(idempotency_key=key, case=draft())
    again = queue.create_case(
        idempotency_key=key,
        case=replace(draft("A different model summary."), correlation_id="run-b"),
    )
    assert isinstance(first, Ok) and isinstance(again, Ok)
    assert again.value == first.value
    assert again.value.summary == "On hold for review; the shopper wants an update."
    assert len(queue.state.cases) == 1


def test_the_same_key_with_other_code_filled_fields_is_a_conflict() -> None:
    queue = CaseQueueFake(state=CaseState())
    key = case_key("O000002", "fraud_review")
    assert isinstance(queue.create_case(idempotency_key=key, case=draft()), Ok)
    other = replace(draft(), amounts=replace(draft().amounts, captured_minor=1))
    assert queue.create_case(idempotency_key=key, case=other) == IDEMPOTENCY_CONFLICT
    flagged = replace(draft(), flags=("summary_missing",))
    assert queue.create_case(idempotency_key=key, case=flagged) == IDEMPOTENCY_CONFLICT
    assert len(queue.state.cases) == 1


def test_a_dry_run_queues_nothing() -> None:
    queue = CaseQueueFake(state=CaseState())
    done = queue.create_case(idempotency_key="care:O000002:x", case=draft(), dry_run=True)
    assert isinstance(done, Ok) and done.value.dry_run and not queue.state.cases


def test_the_queue_refuses_card_data_in_a_case() -> None:
    queue = CaseQueueFake(state=CaseState())
    bad = draft(f"card {TEST_PAN}")
    refused = queue.create_case(idempotency_key="care:O000002:fraud_review", case=bad)
    assert isinstance(refused, ToolError) and refused.code == "card_data_refused"
    assert not queue.state.cases


def test_a_complete_case_has_nothing_missing_for_staff() -> None:
    queue = CaseQueueFake(state=CaseState())
    done = queue.create_case(idempotency_key=case_key("O000002", "fraud_review"), case=draft())
    assert isinstance(done, Ok)
    assert missing_for_staff(done.value) == []
    gappy = replace(done.value, recommended_action="", summary="")
    assert set(missing_for_staff(gappy)) == {"recommended_action", "summary"}


# ---- the audit ---------------------------------------------------------------------------------


def test_the_audit_writes_a_case_by_reference_only() -> None:
    sink = InMemoryAuditSink()
    audit = AuditContext(sink, correlation_id="run-a", actor="care-subagent", clock=fixed_clock)
    view = AuditedCaseQueue(CaseQueueFake(state=CaseState()), audit, allowed=CASE_CREATE)
    done = view.create_case(idempotency_key=case_key("O000002", "fraud_review"), case=draft())
    assert isinstance(done, Ok)
    events = sink.read_all()
    assert [(e.action, e.phase) for e in events] == [
        ("case.create", "attempt"),
        ("case.create", "outcome"),
    ]
    text = json.dumps([e.to_dict() for e in events])
    assert "update" not in text  # the summary is never written
    assert events[1].outcome is not None
    assert events[1].outcome["case_id"] == done.value.case_id
    assert events[1].outcome["root_cause"] == "fraud_review"
    assert events[1].outcome["summary"] == {"type": "str", "length": len(done.value.summary)}
    assert outcome_ref(done)["order_id"] == "O000002"


def test_an_agent_view_without_create_case_is_refused() -> None:
    sink = InMemoryAuditSink()
    audit = AuditContext(sink, correlation_id="run-a", actor="care-subagent", clock=fixed_clock)
    view = AuditedCaseQueue(CaseQueueFake(state=CaseState()), audit, allowed=frozenset())
    refused = view.create_case(idempotency_key="care:O000002:x", case=draft())
    assert isinstance(refused, ToolError) and refused.code == "not_permitted"


# ---- security review pass 1 fixes (docs/reviews/2026-11-24-BE-07-security-review.raw.md) -------


def test_m1_a_card_number_split_around_another_turn_is_withheld() -> None:
    turns = (
        Turn("shopper", "my card is 4111 1111 1111"),
        Turn("assistant", "I'm an AI assistant. Please don't share card numbers in chat."),
        Turn("shopper", "1111, please charge it"),
        Turn("assistant", "I can't take payments here."),
    )
    screened = screen_transcript(turns)
    assert screened[0].text == WITHHELD and screened[2].text == WITHHELD
    assert screened[3] == turns[3]
    task = CareTask("O000123", turns)
    assert "4111" not in task.prompt()


def test_m1_a_card_number_split_over_three_turns_with_text_between_is_withheld() -> None:
    turns = (
        Turn("shopper", "it starts 4111 1111"),
        Turn("assistant", "Please stop, I'm an AI assistant and can't take card numbers here."),
        Turn("shopper", "then 1111"),
        Turn("assistant", "Please don't, really."),
        Turn("shopper", "and 1111 ok?"),
    )
    screened = screen_transcript(turns)
    assert [t.text == WITHHELD for t in screened] == [True, False, True, False, True]


def test_m1_a_security_code_split_around_another_turn_is_withheld() -> None:
    turns = (
        Turn("shopper", "my cvv is"),
        Turn("assistant", "I'm an AI assistant and I can't take card details in chat, sorry."),
        Turn("shopper", "737"),
    )
    screened = screen_transcript(turns)
    assert screened[0].text == WITHHELD and screened[2].text == WITHHELD


def test_m1_ordinary_numbers_across_turns_are_kept() -> None:
    turns = (
        Turn("shopper", "Order O000123 came on 2026-10-11 with 3 items."),
        Turn("assistant", "I'm an AI assistant. I see 2 of the 3 items were sent."),
        Turn("shopper", "Yes, 1 is missing, size 10."),
    )
    assert screen_transcript(turns) == turns


def test_l1_the_queue_refuses_a_card_number_split_by_a_newline() -> None:
    queue = CaseQueueFake(state=CaseState())
    bad = draft("card 4111 1111\n1111 1111")
    refused = queue.create_case(idempotency_key="care:O000002:fraud_review", case=bad)
    assert isinstance(refused, ToolError) and refused.code == "card_data_refused"
    fullwidth = draft("card ４１１１ １１１１ １１１１ １１１１")
    refused = queue.create_case(idempotency_key="care:O000002:fraud_review", case=fullwidth)
    assert isinstance(refused, ToolError) and refused.code == "card_data_refused"
    assert not queue.state.cases


@pytest.mark.parametrize(
    "text",
    [
        "They were charged USD: 999 on the card.",
        "They saw 999 in USD on the statement.",
        "It was a 999-dollar charge.",
        "They saw 999 MXN on the statement.",
        "The bank showed SEK 450.",
    ],
)
def test_l2_more_money_spellings_are_refused(text: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == "summary_has_money"


@pytest.mark.parametrize(
    "text",
    [
        "Process it at northwind-refunds.top today.",
        "They point to pay.ru/claim for a refund.",
        "They were told to visit 192.168.10.20 for help.",
        "Use refunds.example.museum to claim.",
    ],
)
def test_l3_any_web_address_is_refused(text: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == "summary_has_link"


@pytest.mark.parametrize(
    "text",
    [
        "The shopper wants an update, e.g. a delivery date, i.e. by Friday.",
        "Order O000123 arrived on 24.11.2026 at 10:30 a.m. with 3 items.",
        "The U.S. order is on hold.",
    ],
)
def test_l3_abbreviations_and_dates_are_not_web_addresses(text: str) -> None:
    assert check_summary(text) == text


def test_l5_a_very_long_turn_is_refused_before_screening() -> None:
    with pytest.raises(ValueError):
        CareTask("O000123", (Turn("shopper", "y" * (MAX_RAW_TURN_CHARS + 1)),))
    ok = CareTask("O000123", (Turn("shopper", "y" * MAX_RAW_TURN_CHARS),))
    assert len(ok.transcript[0].text) == 2_000


def test_the_care_view_sets_are_exactly_these() -> None:
    from northwind_backend.views import CARE_PRICES, CARE_READ

    assert frozenset({"get"}) == CARE_READ
    assert frozenset({"price"}) == CARE_PRICES
    assert frozenset({"create_case"}) == CASE_CREATE


# ---- security re-review (pass 2) lows (docs/reviews/2026-11-24-BE-07-security-rereview.raw.md) --


def test_n5_a_turn_whose_nfkc_form_is_too_long_is_refused() -> None:
    expanding = "ﷺ" * (MAX_RAW_TURN_CHARS // 10)  # 18 characters each under NFKC
    with pytest.raises(ValueError):
        CareTask("O000123", (Turn("shopper", expanding),))


def test_n6_the_queue_refuses_card_data_split_across_fields() -> None:
    queue = CaseQueueFake(state=CaseState())
    bad = replace(draft(), shopper_id="4111 1111", region="1111 1111")
    refused = queue.create_case(idempotency_key="care:O000002:fraud_review", case=bad)
    assert isinstance(refused, ToolError) and refused.code == "card_data_refused"


@pytest.mark.parametrize(
    "text",
    [
        "Process it at northwind-refunds。top today.",
        "They point to pay.рф for a refund.",
    ],
)
def test_n7_ideographic_dots_and_non_ascii_domains_are_refused(text: str) -> None:
    refused = check_summary(text)
    assert isinstance(refused, ToolError) and refused.code == "summary_has_link"
