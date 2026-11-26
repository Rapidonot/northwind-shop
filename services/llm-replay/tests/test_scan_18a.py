"""Row 18a, part 1: the scanner widenings (FOUND-2 review 6; the user's triage H6). Each one
failing first, each with a near-miss that stays clean, and nothing loosened: every pattern of today
is still read, so today's findings are a subset of the new ones. Sandbox test card numbers only
(hard rule 3). Written from the plan's list (`docs/reviews/2026-11-26-18a-plan.md`, part 1), not
from the code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.scan import scan_paths, scan_text
from northwind_llm_replay.scripted import blocks_sse

pytestmark = [pytest.mark.req("FOUND-2"), pytest.mark.req("PAY-05"), pytest.mark.req("NFR-02")]

CANARY = "NWCANARY-feedfacecafe"
VISA = "4242424242424242"
AMEX = "378282246310005"  # 15 digits, 4-6-5
DINERS = "30569309025904"  # 14 digits, 4-6-4
USAGE = {"input_tokens": 1, "output_tokens": 1}


def kinds(findings: list[Any]) -> set[str]:
    return {f.kind for f in findings}


def pan_found(text: str) -> bool:
    return "luhn-valid-pan" in kinds(scan_text(text))


def cassette(body: str, *, kind: str = "sse", test_id: str = "t", seq: int = 0) -> Cassette:
    return Cassette(
        hash=(f"{test_id}{seq}" * 64)[:64],
        suite="assistant-smoke",
        test_id=test_id,
        seq=seq,
        kind=kind,
        match={"method": "POST", "path": "/v1/messages"},
        status=200,
        content_type="text/event-stream" if kind == "sse" else "application/json",
        body=body,
        model="m",
        usage={},
        cost_usd=0.0,
        recorded_at="2026-11-26T00:00:00+00:00",
    )


def text_stream(*chunks: str) -> str:
    return blocks_sse("m", [{"type": "text", "chunks": list(chunks)}], "end_turn", USAGE, "msg_1")


def two_blocks(first: str, second: str) -> str:
    return blocks_sse(
        "m",
        [{"type": "text", "chunks": [first]}, {"type": "text", "chunks": [second]}],
        "end_turn",
        USAGE,
        "msg_1",
    )


def scan_cassettes(tmp_path: Path, *items: Cassette) -> list[Any]:
    directory = tmp_path / "cassettes" / "s"
    directory.mkdir(parents=True, exist_ok=True)
    for n, item in enumerate(items):
        (directory / f"c{n}.json").write_text(item.to_json(), encoding="utf-8")
    return scan_paths([tmp_path / "cassettes"], canaries=frozenset({CANARY}))


# ---- grouped card numbers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "3782 822463 10005",  # Amex 4-6-5
        "3782-822463-10005",
        "3056 930902 5904",  # Diners 4-6-4
        "3056-930902-5904",
        "4242.4242.4242.4242",  # dot
        "4242 4242 4242 4242",  # no-break space
        "4242 4242 4242 4242",  # narrow no-break space
        "4242 4242 4242 4242",  # thin space
        "4242 4242-4242.4242",  # mixed
        "card 3782.822463.10005 on file",
    ],
)
def test_a_grouped_card_number_is_found(text: str) -> None:
    assert pan_found(text)


@pytest.mark.parametrize(
    "text",
    [
        "4242.4242.4242.4241",  # fails Luhn
        "3782 822463 10006",
        "4242.4242.4242",  # three groups
        "4242  4242  4242  4242",  # two spaces in a gap: no single separator
        "Version 2026.1126.0001",
        "SKU 3782 8224 6310 005",  # 4-4-4-3: no card grouping
    ],
)
def test_a_near_miss_of_a_grouped_number_is_clean(text: str) -> None:
    assert not pan_found(text)


@pytest.mark.parametrize(
    "text",
    ["4242 4242 4242 4242", "4242-4242-4242-4242", VISA, AMEX, DINERS, "x" + VISA + "y"],
)
def test_today_s_card_findings_are_still_found_once(text: str) -> None:
    findings = scan_text(text)
    assert [f.kind for f in findings] == ["luhn-valid-pan"]


# ---- x-api-key escaped in a cassette string -----------------------------------------------------


def test_an_escaped_x_api_key_header_in_a_cassette_is_found(tmp_path: Path) -> None:
    item = cassette(text_stream("Wash cold."))
    item.extra = {"headers": json.dumps({"x-api-key": "abcd1234efgh5678"})}
    assert '\\"x-api-key\\": \\"abcd1234' in item.to_json()  # escaped as stored
    assert "x-api-key-header" in kinds(scan_cassettes(tmp_path, item))


@pytest.mark.parametrize(
    "line",
    [
        '"x-api-key": "abcd1234"',
        "x-api-key: abcd1234",
        "X-Api-Key=abcd1234",
        '{\\"x-api-key\\": \\"abcd1234\\"}',
        '{\\\\"x-api-key\\\\": \\\\"abcd1234\\\\"}',
    ],
)
def test_an_x_api_key_header_with_a_value_is_found(line: str) -> None:
    assert kinds(scan_text(line)) == {"x-api-key-header"}


@pytest.mark.parametrize(
    "line",
    ['{\\"x-api-key\\": \\"\\"}', '"x-api-key": ""', "the x-api-key header is required"],
)
def test_an_x_api_key_header_with_no_value_is_clean(line: str) -> None:
    assert scan_text(line) == []


# ---- json bodies, CR-only streams, every string decoded -----------------------------------------


def json_body(*blocks: dict[str, Any], ascii_only: bool = True) -> str:
    message = {"type": "message", "role": "assistant", "content": list(blocks)}
    return json.dumps(message, ensure_ascii=ascii_only)


def test_a_card_in_a_json_body_behind_an_escape_is_found(tmp_path: Path) -> None:
    body = json_body({"type": "text", "text": "4242 4242 4242 4242"})
    assert "\\u00a0" in body  # the separator is escaped in the stored body
    item = cassette(body, kind="json")
    assert "luhn-valid-pan" not in kinds(scan_text(item.to_json()))  # the raw text hides it
    assert any("luhn-valid-pan" in k for k in kinds(scan_cassettes(tmp_path, item)))


def test_a_secret_split_between_two_json_blocks_is_found(tmp_path: Path) -> None:
    body = json_body(
        {"type": "text", "text": "Your note: " + CANARY[:10]},
        {"type": "text", "text": CANARY[10:] + " ends."},
    )
    found = kinds(scan_cassettes(tmp_path, cassette(body, kind="json")))
    assert "rebuilt-internal-canary" in found


def test_a_tool_call_input_in_a_json_body_is_read(tmp_path: Path) -> None:
    note = "4242\u00a04242\u00a04242\u00a04242"
    body = json_body({"type": "tool_use", "id": "t1", "name": "x", "input": {"note": note}})
    item = cassette(body, kind="json")
    assert "luhn-valid-pan" not in kinds(scan_text(item.to_json()))  # the raw text hides it
    assert any("luhn-valid-pan" in k for k in kinds(scan_cassettes(tmp_path, item)))


def test_a_card_behind_escapes_in_a_requests_tool_result_is_found(tmp_path: Path) -> None:
    """A tool's JSON text is built with `ensure_ascii`, so a no-break space is `\\u00a0` in the
    string the request carries: only decoding that string shows the card."""
    result = json.dumps({"note": "4242\u00a04242\u00a04242\u00a04242"})
    item = cassette(text_stream("Wash cold."))
    item.match = {
        "method": "POST",
        "path": "/v1/messages",
        "body": {
            "messages": [{"role": "user", "content": [{"type": "tool_result", "content": result}]}]
        },
    }
    assert "luhn-valid-pan" not in kinds(scan_text(item.to_json()))  # the raw text hides it
    assert "decoded-luhn-valid-pan" in kinds(scan_cassettes(tmp_path, item))


def test_a_cr_only_stream_is_reassembled(tmp_path: Path) -> None:
    body = text_stream(CANARY[:9], CANARY[9:]).replace("\n", "\r")
    assert "\n" not in body
    assert "reassembled-internal-canary" in kinds(scan_cassettes(tmp_path, cassette(body)))


def test_a_clean_json_body_is_clean(tmp_path: Path) -> None:
    body = json_body({"type": "text", "text": "Wash cold [FAQ-001]. Order O000800, 30 days."})
    assert scan_cassettes(tmp_path, cassette(body, kind="json")) == []


# ---- a secret split between two blocks, or two requests -----------------------------------------


def test_a_secret_split_between_two_stream_blocks_is_found(tmp_path: Path) -> None:
    body = two_blocks("Note " + CANARY[:12], CANARY[12:] + " done.")
    assert "rebuilt-internal-canary" in kinds(scan_cassettes(tmp_path, cassette(body)))


def test_a_card_split_between_two_stream_blocks_is_found(tmp_path: Path) -> None:
    body = two_blocks("ref " + VISA[:8], VISA[8:] + " ok")
    assert "rebuilt-luhn-valid-pan" in kinds(scan_cassettes(tmp_path, cassette(body)))


def test_a_secret_split_between_two_requests_is_found(tmp_path: Path) -> None:
    first = cassette(text_stream("Your note: ", CANARY[:11]), seq=0)
    second = cassette(text_stream(CANARY[11:], " ends."), seq=1)
    found = scan_cassettes(tmp_path, first, second)
    assert "rebuilt-across-requests-internal-canary" in kinds(found)
    assert {Path(f.path).stem for f in found} == {"c1"}  # the later call, where it closes


def test_a_card_split_over_three_requests_is_found(tmp_path: Path) -> None:
    parts = [VISA[:6], VISA[6:11], VISA[11:]]
    items = [cassette(text_stream(part), seq=n) for n, part in enumerate(parts)]
    assert "rebuilt-across-requests-luhn-valid-pan" in kinds(scan_cassettes(tmp_path, *items))


def test_two_tests_are_not_joined(tmp_path: Path) -> None:
    first = cassette(text_stream("Your note: ", CANARY[:11]), test_id="a")
    second = cassette(text_stream(CANARY[11:], " ends."), test_id="b")
    assert scan_cassettes(tmp_path, first, second) == []


def test_requests_are_joined_in_seq_order_not_file_order(tmp_path: Path) -> None:
    later = cassette(text_stream(CANARY[11:], " ends."), seq=1)
    earlier = cassette(text_stream("Your note: ", CANARY[:11]), seq=0)
    assert "rebuilt-across-requests-internal-canary" in kinds(
        scan_cassettes(tmp_path, later, earlier)
    )
    reverse = cassette(text_stream("Your note: ", CANARY[:11]), seq=1)
    start = cassette(text_stream(CANARY[11:], " ends."), seq=0)
    assert scan_cassettes(tmp_path / "r", reverse, start) == []


def test_a_secret_in_one_earlier_request_is_reported_for_that_request_only(tmp_path: Path) -> None:
    first = cassette(text_stream("Note " + CANARY), seq=0)
    second = cassette(text_stream("Wash cold."), seq=1)
    assert {Path(f.path).stem for f in scan_cassettes(tmp_path, first, second)} == {"c0"}


def test_put_refuses_the_request_that_completes_a_split_secret(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset({CANARY}))
    store.put(cassette(text_stream("Your note: ", CANARY[:11]), seq=0))
    with pytest.raises(DirtyCassetteError, match="rebuilt-across-requests-internal-canary"):
        store.put(cassette(text_stream(CANARY[11:], " ends."), seq=1))
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_put_keeps_tests_apart(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset({CANARY}))
    store.put(cassette(text_stream("Your note: ", CANARY[:11]), test_id="a"))
    store.put(cassette(text_stream(CANARY[11:], " ends."), test_id="b"))
    assert len(list(tmp_path.rglob("*.json"))) == 2


# ---- every delta type is read --------------------------------------------------------------------


def with_delta(delta: dict[str, Any]) -> str:
    event = {"type": "content_block_delta", "index": 0, "delta": delta}
    return text_stream("Wash cold.") + f"event: content_block_delta\ndata: {json.dumps(event)}\n\n"


@pytest.mark.parametrize(
    "delta",
    [
        {"type": "citations_delta", "citation": {"cited_text": "x"}},
        {"type": "something_new", "text": "x"},
        {"text": "no type"},
    ],
)
def test_a_delta_of_an_unknown_type_is_a_finding(tmp_path: Path, delta: dict[str, Any]) -> None:
    assert "unknown-delta-type" in kinds(scan_cassettes(tmp_path, cassette(with_delta(delta))))
    with pytest.raises(DirtyCassetteError, match="unknown-delta-type"):
        CassetteStore(tmp_path / "store", "s", canaries=frozenset()).put(
            cassette(with_delta(delta))
        )


def test_a_signature_delta_is_read_not_skipped(tmp_path: Path) -> None:
    halves = [{"type": "signature_delta", "signature": part} for part in (CANARY[:9], CANARY[9:])]
    events = [{"type": "content_block_delta", "index": 1, "delta": half} for half in halves]
    body = text_stream("Wash cold.") + "".join(
        f"event: content_block_delta\ndata: {json.dumps(event)}\n\n" for event in events
    )
    assert any("internal-canary" in k for k in kinds(scan_cassettes(tmp_path, cassette(body))))
    clean = with_delta({"type": "signature_delta", "signature": "EqQBCgIYAhIM1gbcDa9GJwZA"})
    assert scan_cassettes(tmp_path / "clean", cassette(clean)) == []


def test_a_known_delta_type_is_no_finding(tmp_path: Path) -> None:
    body = with_delta({"type": "thinking_delta", "thinking": "Plan the answer."})
    assert scan_cassettes(tmp_path, cassette(body)) == []


# ---- security review L4 and L7 -----------------------------------------------------------------


def test_a_carry_cut_inside_a_number_does_not_hide_a_card_split_between_requests(
    tmp_path: Path,
) -> None:
    """L4: the carry's own findings are taken off the join's. A carry cut in the middle of a
    17-digit run that fails Luhn starts with a 16-digit run that passes it, which is no finding of
    the earlier call; it must not hide the card the join makes."""
    run = "1" + VISA  # 17 digits, fails Luhn
    assert "luhn-valid-pan" not in kinds(scan_text(f"x {run} y"))
    tail = VISA + " " + "a" * (256 - len(VISA) - len(" ref 42424242") - 1) + " ref 42424242"
    first_text = "x 1" + tail
    assert len(tail) == 256 and first_text.endswith("ref 42424242")
    first = cassette(text_stream(first_text), seq=0)
    second = cassette(text_stream("42424242 ok"), seq=1)
    assert scan_cassettes(tmp_path / "alone", first) == []
    found = kinds(scan_cassettes(tmp_path / "both", first, second))
    assert "rebuilt-across-requests-luhn-valid-pan" in found


def test_put_finds_the_same_card_however_its_carry_was_cut(tmp_path: Path) -> None:
    tail = VISA + " " + "a" * (256 - len(VISA) - len(" ref 42424242") - 1) + " ref 42424242"
    store = CassetteStore(tmp_path, "s", canaries=frozenset())
    store.put(cassette(text_stream("x 1" + tail), seq=0))
    with pytest.raises(DirtyCassetteError, match="rebuilt-across-requests-luhn-valid-pan"):
        store.put(cassette(text_stream("42424242 ok"), seq=1))


@pytest.mark.parametrize("slashes", [3, 4, 6])
def test_an_x_api_key_escaped_more_deeply_is_found(slashes: int) -> None:
    """L7: a header escaped twice or more, in a file the scanner reads raw (ledger, proxy log)."""
    bs = "\\" * slashes
    assert kinds(scan_text(f'{{{bs}"x-api-key{bs}": {bs}"abcd1234{bs}"}}')) == {"x-api-key-header"}
    assert scan_text(f'{{{bs}"x-api-key{bs}": {bs}"{bs}"}}') == []
