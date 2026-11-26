"""M-1 on the recorded answers (row #27 PR 2, CHK-07; the user, 2026-11-25): in every recorded
`assistant-orders` answer, a sentence that states an order's status with its number must match that
order's `get_order` status or scan history, and no answer shows a status code (D9).

At runtime this is held by the prompt only (decision 0015, row #27 note): nothing here runs in a
turn. It reads the cassettes, so it costs nothing and needs no model. The matcher is stricter than
the words above where that is cheap: "its" and "it" carry the last order named, and a dated scan
line must be a scan of an order the answer names. "Arrived" followed at once by a condition
("arrived damaged", "arrived with a tear") states the item's condition and is not read as a status
(the user, 2026-11-26); plain "arrived" still is, and is never true. It knows the words listed
here; a status in other words is not seen. The matcher is tested on wrong answers first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from northwind_common.errors import ToolError
from northwind_common.order_tool import CONFIRMED_STATUSES, STATUSES
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.wording import STATUS_WORDING

pytestmark = pytest.mark.req("CHK-07")

REPO = Path(__file__).resolve().parents[3]
CASSETTES = REPO / "cassettes" / "assistant-orders"
SHOPPER, REGION = "S0188", "US"

# "Arrived damaged" and the like is the item's condition, not a status (the user, 2026-11-26).
_AS_CONDITION = (
    r"(?!\s+(?:damaged|broken|faulty|torn|defective|ripped|cracked|stained|scratched|dented|"
    r"with (?:a |some )?(?:defect|damage|tear|hole|stain|fault)s?))"
)
# What an answer may call each status. `None`: no status the tool returns, so never true.
WORDS: tuple[tuple[str, str | None], ...] = (
    (r"out for delivery", "out_for_delivery"),
    (r"delivered", "delivered"),
    (r"in transit", "in_transit"),
    (r"picked up", "picked_up"),
    (r"label (?:has been |was |is )?created", "label_created"),
    (r"on hold", "on_hold"),
    (r"(?:being processed|processing)", "processing"),
    (r"not yet with the carrier", "confirmed"),
    (r"confirmed", "confirmed"),
    (r"placed", "placed"),
    (rf"(?:shipped|dispatched|delayed|cancell?ed|lost|arrived{_AS_CONDITION}|packed|on its way|"
     r"on the way)", None),
)  # fmt: skip
_CODE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")
_ORDER = re.compile(r"\bO\d{6}\b")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONTHS = "january february march april may june july august september october november december"
_LONG_DATE = re.compile(
    rf"\b(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)? (?P<m1>{_MONTHS.replace(' ', '|')})|"
    rf"(?P<m2>{_MONTHS.replace(' ', '|')}) (?P<d2>\d{{1,2}})(?:st|nd|rd|th)?)\b"
    r"(?:,? (?P<y>\d{4}))?",
    re.I,
)
_NEGATED = re.compile(r"(?:n't|\bnot|\bnever|\bno)\b[^.]{0,20}$", re.I)
_CONDITIONAL = re.compile(r"\b(?:once|when|until|if|after|before|whether)\b[^,;]{0,40}$", re.I)
_FUTURE = re.compile(r"\b(?:will|should|going to|expected to|due to)\b[^,;]{0,25}$", re.I)
_PRESENT = re.compile(r"\b(?:is|status:?)\s+(?:(?:now|currently|still|already)\s+)?$", re.I)
_CARRIES = re.compile(r"^(?:\W*(?:yes|no)\W+)?(?:its|it|it's|the order)\b", re.I)


@cache
def book() -> OrderBook:
    return OrderBook.from_data()


def result_of(order_id: str) -> Mapping[str, Any] | None:
    """What `get_order` returns the signed-in shopper, or None for not_found."""
    try:
        return book().view(order_id, shopper=SHOPPER, region=REGION)
    except ToolError as error:
        assert error.code == "not_found"
        return None


def _iso(text: str) -> str:
    """Long dates as ISO; the fixtures are all in 2026."""

    def one(m: re.Match[str]) -> str:
        month = _MONTHS.split().index((m["m1"] or m["m2"]).lower()) + 1
        return f"{m['y'] or 2026}-{month:02d}-{int(m['d1'] or m['d2']):02d}"

    return _LONG_DATE.sub(one, text)


def _dates(result: Mapping[str, Any], code: str) -> set[str]:
    if code == "placed":
        return {result["placed_on"]}
    scans = (result["tracking"] or {"scans": []})["scans"]
    return {s["on"] for s in scans if s["status"] == code}


def _ever(result: Mapping[str, Any]) -> set[str]:
    scans = (result["tracking"] or {"scans": []})["scans"]
    ever = {result["status"], "placed", "processing", *(s["status"] for s in scans)}
    if result["status"] in CONFIRMED_STATUSES:
        ever.add("confirmed")
    if result["tracking"] is not None:
        ever.add("label_created")
    return ever


def _claim_ok(result: Mapping[str, Any], code: str | None, before: str, after: str) -> bool:
    if _CONDITIONAL.search(before):
        return True  # states nothing yet
    if code is None or _FUTURE.search(before):
        return False
    if _NEGATED.search(before):
        return code not in _ever(result)
    dated = re.match(r"\W{0,3}(?:on\W{1,3})?(\d{4}-\d{2}-\d{2})", after)
    if dated:
        return dated.group(1) in _dates(result, code)
    if _PRESENT.search(before) or (code != "placed" and not re.search(r"\w\s+$", before)):
        return bool(code == result["status"])
    return code in _ever(result)


def problems(answer: str) -> list[str]:
    """Every sentence that states a status its order's result does not hold."""
    found: list[str] = []
    text = _iso(answer.replace("’", "'").replace("_", " ").replace("*", ""))
    named: list[str] = []  # orders named so far, the last one is "it"
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        line = sentence.strip()
        scan = re.fullmatch(r"[-•\d.)\s]*(\d{4}-\d{2}-\d{2})\W+([a-z ]+?)\W*", line, re.I)
        if scan:
            codes = [c for w, c in WORDS if re.search(rf"\b{w}\b", scan.group(2), re.I)]
            results = [r for r in map(result_of, named) if r is not None]
            if codes and not any(
                c is not None and scan.group(1) in _dates(r, c) for c in codes[:1] for r in results
            ):
                found.append(line)
            continue
        here = _ORDER.findall(line)
        named.extend(here)
        subjects = here or (named[-1:] if _CARRIES.search(line) else [])
        if not subjects:
            continue
        taken: list[tuple[int, int]] = []
        for words, code in WORDS:
            for m in re.finditer(rf"\b{words}\b", line, re.I):
                if any(a <= m.start() < b for a, b in taken):
                    continue
                taken.append(m.span())
                # With several orders in one sentence, the one named last before the claim.
                earlier = [o.group() for o in _ORDER.finditer(line, 0, m.start())]
                subject = (earlier or subjects)[-1]
                result = result_of(subject)
                before, after = line[: m.start()], line[m.end() :]
                if result is None or not _claim_ok(result, code, before, after):
                    found.append(f"{subject}: {line}")
    return found


def codes_in(answer: str) -> list[str]:
    return _CODE.findall(answer)


# ---- the matcher, on right and wrong answers ----------------------------------------------------

RIGHT = [
    "Order O000800 was delivered on 2026-10-26.",
    "Yes. O000800 was delivered on 26 October.",
    "Order O000928 was placed on October 13, 2026 and its status is in transit.",
    "O000928 is in transit. It was picked up on 2026-10-14.",
    "O000928 has not been delivered yet.",
    "Once O000928 is delivered, you can ask again.",
    "O000418 is on hold.",
    "Order O000406 was placed on 2026-10-14. Its status is label created, not yet scanned.",
    "Tracking scans for O000800:\n- 2026-10-21: picked up\n- 2026-10-26: delivered",
    "I couldn't find order O000001 on your account.",
    "What is your order number?",
    # The item's condition, not the order's status (the user, 2026-11-26).
    "The return for order O000800 needs review because the item arrived damaged.",
    "Your boots from O000800 arrived broken, so a person needs to review it.",
    "O000800: the jacket arrived faulty.",
    "Order O000800's scarf arrived torn and a person will review the return.",
    "O000800's item arrived defective.",
]
WRONG = [
    "Order O000418 has been confirmed.",  # M-1: it is on hold
    "O000418 is confirmed.",
    "Your order O000418 has been confirmed.",
    "Order O000418 is being processed.",
    "Order O000418 has not been placed.",
    "Order O000800 is in transit.",  # it was, once
    "O000800 was delivered on 2026-10-25.",  # another scan's date
    "O000800 was delivered on 21 October.",
    "O000800 has not been delivered.",
    "O000800 has not been confirmed.",
    "O000928 was delivered on 2026-10-16.",
    "O000928 is out for delivery.",
    "O000928 has been shipped.",
    "O000928 will be delivered on 2026-11-20.",
    "O000928 is on its way.",
    "O000928 has not been shipped.",  # no result says either way
    "O000800 will be delivered soon.",
    "O000406 was picked up on 2026-10-15.",
    "Order O000001 was delivered.",  # another shopper's
    "O000999 is in transit.",
    "O000928 is in transit. It was delivered on 2026-10-26.",
    "O000800 was delivered, and O000928 was delivered.",
    "O000928 is in transit and O000418 is in transit.",
    "Scans for O000928:\n- 2026-10-26: delivered",
    "Order O000928's status is delivered.",
    # Plain "arrived" stays a status word with no code, so it is never true.
    "Order O000928 has arrived.",
    "O000800 arrived on 2026-10-26.",
    "O000928 arrived, but the jacket was damaged.",
    "O000928 arrived and is damaged.",
]


@pytest.mark.parametrize("answer", RIGHT)
def test_the_matcher_passes_a_true_statement(answer: str) -> None:
    assert problems(answer) == [] and codes_in(answer) == []


@pytest.mark.parametrize("answer", WRONG)
def test_the_matcher_finds_a_false_statement(answer: str) -> None:
    assert problems(answer), answer


ONE_PER_STATUS = {
    "O000800": "delivered", "O000928": "in_transit", "O000406": "label_created",
    "O000418": "on_hold", "O000517": "confirmed", "O000898": "out_for_delivery",
    "O000267": "picked_up",
}  # fmt: skip


@pytest.mark.parametrize("order_id", ONE_PER_STATUS)
def test_the_present_status_of_each_order_is_true_and_every_other_one_false(order_id: str) -> None:
    result = result_of(order_id)
    assert result is not None and result["status"] == ONE_PER_STATUS[order_id]
    for code in sorted(STATUSES):
        answer = f"Order {order_id} is {STATUS_WORDING[code]}."
        assert bool(problems(answer)) is (code != result["status"]), (code, problems(answer))


@pytest.mark.parametrize("code", sorted(c for c in STATUSES if "_" in c))
def test_a_status_code_is_seen_as_one_whatever_is_true(code: str) -> None:
    assert codes_in(f"Its status is {code}.") == [code]
    assert codes_in(f"Its status is `{code}`.") == [code]
    assert codes_in(f"Its status is {STATUS_WORDING[code]}.") == []


# ---- the recordings -----------------------------------------------------------------------------


def _texts(body: str) -> str:
    out = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:])
        if event.get("type") == "content_block_delta" and event["delta"]["type"] == "text_delta":
            out.append(event["delta"]["text"])
    return "".join(out)


def _stop_reason(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("data:"):
            event = json.loads(line[5:])
            if event.get("type") == "message_delta":
                return str(event["delta"]["stop_reason"])
    return None


def _before_a_tool_call() -> list[tuple[str, str]]:
    """(scenario and call, text) for every recorded model message that ends in a tool call."""
    found = []
    for path in sorted(CASSETTES.glob("*.json")):
        cassette = json.loads(path.read_text(encoding="utf-8"))
        if _stop_reason(cassette["body"]) == "tool_use":
            name = cassette["test_id"].rsplit("[", 1)[-1].rstrip("]")
            found.append((f"{name}#{cassette['seq']}", _texts(cassette["body"])))
    return sorted(found)


def _recorded() -> list[tuple[str, str]]:
    """(scenario and call, what the model wrote) for every cassette of the suite."""
    found = []
    for path in sorted(CASSETTES.glob("*.json")):
        cassette = json.loads(path.read_text(encoding="utf-8"))
        name = cassette["test_id"].rsplit("[", 1)[-1].rstrip("]")
        found.append((f"{name}#{cassette['seq']}", _texts(cassette["body"])))
    return sorted(found)


RECORDED = _recorded()


def test_the_recordings_are_read() -> None:
    names = {name.split("#")[0] for name, _ in RECORDED}
    answered = {name.split("#")[0] for name, text in RECORDED if text.strip()}
    assert len(RECORDED) >= 11 and len(names) >= 6
    assert answered == names, "every scenario ends in an answer"
    assert sum(bool(_ORDER.search(text)) for _, text in RECORDED) >= 5


@pytest.mark.parametrize(("name", "answer"), RECORDED, ids=[n for n, _ in RECORDED])
def test_every_stated_status_is_the_orders_own(name: str, answer: str) -> None:
    assert problems(answer) == []


BEFORE_A_TOOL_CALL = _before_a_tool_call()


def test_the_messages_that_call_a_tool_are_read() -> None:
    assert len(BEFORE_A_TOOL_CALL) >= 11  # get_order in ten scenarios, and more in the returns


@pytest.mark.parametrize(
    ("name", "text"), BEFORE_A_TOOL_CALL, ids=[n for n, _ in BEFORE_A_TOOL_CALL]
)
def test_no_text_is_written_before_a_tool_call(name: str, text: str) -> None:
    """The shopper sees every word a turn writes, so a message that calls a tool writes none
    (row #27 PR 2's window: a reasoning line about "the shopper" reached the portal)."""
    assert text.strip() == ""


@pytest.mark.parametrize(("name", "answer"), RECORDED, ids=[n for n, _ in RECORDED])
def test_no_answer_shows_a_status_code(name: str, answer: str) -> None:
    assert codes_in(answer) == []
