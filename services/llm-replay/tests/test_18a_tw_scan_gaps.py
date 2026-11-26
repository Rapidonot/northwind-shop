"""Row 18a, part 1, gap tests (test-writer): grouped cards over every separator, json and CR-only
bodies, decoded strings, splits across blocks and requests (in `seq` order), unknown deltas, and
no finding of today lost. Sandbox test cards and made-up canaries only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.scan import scan_paths, scan_text
from northwind_llm_replay.scripted import blocks_sse

pytestmark = [pytest.mark.req("FOUND-2"), pytest.mark.req("PAY-05")]

CANARY = "NWCANARY-0123456789ab"
VISA = "4242424242424242"
USAGE = {"input_tokens": 1, "output_tokens": 1}
SEPARATORS = [" ", "-", ".", " ", " ", " "]
GROUPINGS = {
    "4242424242424242": (4, 4, 4, 4),
    "4111111111111111": (4, 4, 4, 4),
    "378282246310005": (4, 6, 5),
    "30569309025904": (4, 6, 4),
}


def grouped(number: str, sizes: tuple[int, ...], seps: list[str]) -> str:
    out, at = "", 0
    for i, size in enumerate(sizes):
        out += number[at : at + size] + (seps[i] if i < len(sizes) - 1 else "")
        at += size
    return out


def kinds(findings: list[Any]) -> set[str]:
    return {f.kind for f in findings}


def cassette(body: str, *, kind: str = "sse", test_id: str = "t", seq: int = 0) -> Cassette:
    return Cassette(
        hash=(f"{test_id}{seq}".encode().hex() * 64)[:64],
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


def stream(*chunks: str) -> str:
    return blocks_sse("m", [{"type": "text", "chunks": list(chunks)}], "end_turn", USAGE, "msg_1")


def blocks_stream(*texts: str) -> str:
    return blocks_sse(
        "m", [{"type": "text", "chunks": [t]} for t in texts], "end_turn", USAGE, "msg_1"
    )


def json_body(*texts: str, ascii_only: bool = True) -> str:
    content = [{"type": "text", "text": t} for t in texts]
    return json.dumps(
        {"type": "message", "role": "assistant", "content": content}, ensure_ascii=ascii_only
    )


def scan_items(tmp_path: Path, *items: Cassette) -> list[Any]:
    directory = tmp_path / "cassettes" / "s"
    directory.mkdir(parents=True, exist_ok=True)
    for n, item in enumerate(items):
        (directory / f"c{n:02d}.json").write_text(item.to_json(), encoding="utf-8")
    return scan_paths([tmp_path / "cassettes"], canaries=frozenset({CANARY}))


# ---- grouped cards: every shape x every separator -----------------------------------------------


@pytest.mark.parametrize("number", list(GROUPINGS))
@pytest.mark.parametrize("sep", SEPARATORS)
def test_every_grouping_is_found_with_every_single_separator(number: str, sep: str) -> None:
    text = grouped(number, GROUPINGS[number], [sep] * 3)
    assert kinds(scan_text(f"Card on file: {text}.")) == {"luhn-valid-pan"}, repr(text)


@pytest.mark.parametrize("number", ["378282246310005", "30569309025904", VISA])
def test_mixed_separators_are_found(number: str) -> None:
    sizes = GROUPINGS[number]
    seps = [" ", "-", " "]
    assert "luhn-valid-pan" in kinds(scan_text(grouped(number, sizes, seps)))
    seps = [".", " ", "-"]
    assert "luhn-valid-pan" in kinds(scan_text(grouped(number, sizes, seps)))


def test_a_grouped_number_is_found_once_per_number() -> None:
    assert len(scan_text("3782 822463 10005")) == 1
    assert len(scan_text("4242.4242.4242.4242")) == 1


@pytest.mark.parametrize(
    "text",
    [
        "3782 822463 10006",  # fails Luhn
        "3056 930902 5905",
        "4242 4242 4242 4243",
        "3782 82246 310005",  # 4-5-6: no card grouping
        "37828 22463 10005",
        "4242,4242,4242,4242",  # a comma is no separator
        "4242_4242_4242_4242",
    ],
)
def test_a_near_miss_of_a_grouped_card_is_clean(text: str) -> None:
    assert "luhn-valid-pan" not in kinds(scan_text(text))


# ---- json bodies, CR-only streams, decoded strings ----------------------------------------------


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize("number", ["378282246310005", "30569309025904"])
def test_a_json_body_with_a_grouped_card_is_found(
    tmp_path: Path, number: str, ascii_only: bool
) -> None:
    text = grouped(number, GROUPINGS[number], [" "] * 3)
    found = scan_items(tmp_path, cassette(json_body(text, ascii_only=ascii_only), kind="json"))
    assert any("luhn-valid-pan" in k for k in kinds(found))


def test_a_json_body_with_a_card_split_between_blocks_is_found(tmp_path: Path) -> None:
    body = json_body("ref " + VISA[:7], VISA[7:] + " ok")
    assert any(
        "luhn-valid-pan" in k for k in kinds(scan_items(tmp_path, cassette(body, kind="json")))
    )


def test_a_json_body_with_a_canary_is_found(tmp_path: Path) -> None:
    found = scan_items(tmp_path, cassette(json_body("note " + CANARY), kind="json"))
    assert any("internal-canary" in k for k in kinds(found))


def test_a_cr_only_stream_with_a_card_split_between_deltas_is_found(tmp_path: Path) -> None:
    body = stream("pay ", VISA[:6], VISA[6:], " ok").replace("\n", "\r")
    assert "\n" not in body
    assert any("luhn-valid-pan" in k for k in kinds(scan_items(tmp_path, cassette(body))))


def test_a_crlf_stream_is_still_read(tmp_path: Path) -> None:
    body = stream(CANARY[:9], CANARY[9:]).replace("\n", "\r\n")
    assert any("internal-canary" in k for k in kinds(scan_items(tmp_path, cassette(body))))


def test_a_cr_only_stream_split_between_blocks_is_found(tmp_path: Path) -> None:
    body = blocks_stream("note " + CANARY[:8], CANARY[8:]).replace("\n", "\r")
    assert any("internal-canary" in k for k in kinds(scan_items(tmp_path, cassette(body))))


def test_an_escaped_string_in_the_request_hides_nothing(tmp_path: Path) -> None:
    item = cassette(stream("Wash cold."))
    item.match = {
        "method": "POST",
        "path": "/v1/messages",
        "body": {"messages": [{"role": "user", "content": "x " + CANARY}]},
    }
    assert any("internal-canary" in k for k in kinds(scan_items(tmp_path, item)))


@pytest.mark.parametrize("header", ["x-api-key", "X-API-KEY", "X-Api-Key"])
def test_an_escaped_x_api_key_with_a_value_is_found_in_any_case(
    tmp_path: Path, header: str
) -> None:
    item = cassette(stream("Wash cold."))
    item.extra = {"headers": json.dumps({header: "abcd1234efgh5678"})}
    assert "x-api-key-header" in kinds(scan_items(tmp_path, item))


def test_an_escaped_x_api_key_with_an_empty_value_is_clean(tmp_path: Path) -> None:
    item = cassette(stream("Wash cold."))
    item.extra = {"headers": json.dumps({"x-api-key": ""})}
    assert scan_items(tmp_path, item) == []


# ---- splits across blocks and requests ----


@pytest.mark.parametrize("cut", [1, 5, 10, 15, 20])
def test_a_canary_cut_anywhere_between_two_blocks_is_found(tmp_path: Path, cut: int) -> None:
    body = blocks_stream("a " + CANARY[:cut], CANARY[cut:] + " b")
    assert any("internal-canary" in k for k in kinds(scan_items(tmp_path, cassette(body))))


@pytest.mark.parametrize("cut", [1, 5, 10, 15, 20])
def test_a_canary_cut_anywhere_between_two_requests_is_found(tmp_path: Path, cut: int) -> None:
    first = cassette(stream("a " + CANARY[:cut]), seq=0)
    second = cassette(stream(CANARY[cut:] + " b"), seq=1)
    assert any("internal-canary" in k for k in kinds(scan_items(tmp_path, first, second)))


def test_a_split_card_between_two_requests_is_found(tmp_path: Path) -> None:
    first = cassette(stream("card " + VISA[:9]), seq=0)
    second = cassette(stream(VISA[9:] + " ok"), seq=1)
    assert any("luhn-valid-pan" in k for k in kinds(scan_items(tmp_path, first, second)))


def test_requests_are_joined_by_seq_as_a_number_not_as_text(tmp_path: Path) -> None:
    """seq 9 comes before seq 10, though "10" sorts before "9" as text."""
    directory = tmp_path / "cassettes" / "s"
    directory.mkdir(parents=True)
    first = cassette(stream("a " + CANARY[:11]), seq=9)
    second = cassette(stream(CANARY[11:] + " b"), seq=10)
    # file names sort the other way round to the seq order
    (directory / "a.json").write_text(second.to_json(), encoding="utf-8")
    (directory / "b.json").write_text(first.to_json(), encoding="utf-8")
    found = scan_paths([tmp_path / "cassettes"], canaries=frozenset({CANARY}))
    assert any("internal-canary" in k for k in kinds(found))


def test_a_json_body_split_between_two_requests_is_found(tmp_path: Path) -> None:
    first = cassette(json_body("a " + CANARY[:11]), kind="json", seq=0)
    second = cassette(json_body(CANARY[11:] + " b"), kind="json", seq=1)
    assert any("internal-canary" in k for k in kinds(scan_items(tmp_path, first, second)))


def test_a_clean_run_of_requests_is_clean(tmp_path: Path) -> None:
    items = [cassette(stream(f"Wash cold, step {n}. [FAQ-00{n}]"), seq=n) for n in range(4)]
    assert scan_items(tmp_path, *items) == []


def test_other_tests_are_not_joined_even_in_seq_order(tmp_path: Path) -> None:
    first = cassette(stream("a " + CANARY[:11]), test_id="x", seq=0)
    second = cassette(stream(CANARY[11:] + " b"), test_id="y", seq=1)
    assert scan_items(tmp_path, first, second) == []


def test_put_refuses_a_card_split_between_two_requests(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset())
    store.put(cassette(stream("card " + VISA[:9]), seq=0))
    with pytest.raises(DirtyCassetteError):
        store.put(cassette(stream(VISA[9:] + " ok"), seq=1))
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_put_refuses_a_secret_split_between_two_blocks(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset({CANARY}))
    with pytest.raises(DirtyCassetteError):
        store.put(cassette(blocks_stream("a " + CANARY[:8], CANARY[8:])))
    assert list(tmp_path.rglob("*.json")) == []


def test_put_refuses_a_json_body_with_an_escaped_card(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset())
    with pytest.raises(DirtyCassetteError):
        store.put(cassette(json_body("4242 4242 4242 4242"), kind="json"))


def test_put_writes_a_clean_cassette(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset({CANARY}))
    path = store.put(cassette(stream("Wash cold.")))
    assert path.exists()


# ---- unknown deltas ----


def with_delta(delta: dict[str, Any]) -> str:
    event = {"type": "content_block_delta", "index": 0, "delta": delta}
    return stream("Wash cold.") + f"event: content_block_delta\ndata: {json.dumps(event)}\n\n"


@pytest.mark.parametrize("name", ["citations_delta", "future_delta", "text", ""])
def test_an_unknown_delta_type_is_a_finding_and_put_refuses_it(tmp_path: Path, name: str) -> None:
    body = with_delta({"type": name, "text": "x"})
    assert "unknown-delta-type" in kinds(scan_items(tmp_path, cassette(body)))
    with pytest.raises(DirtyCassetteError):
        CassetteStore(tmp_path / "store", "s", canaries=frozenset()).put(cassette(body))


def test_an_unknown_delta_in_a_cr_only_stream_is_a_finding(tmp_path: Path) -> None:
    body = with_delta({"type": "future_delta", "text": "x"}).replace("\n", "\r")
    assert "unknown-delta-type" in kinds(scan_items(tmp_path, cassette(body)))


@pytest.mark.parametrize(
    "delta",
    [
        {"type": "text_delta", "text": "Wash cold."},
        {"type": "thinking_delta", "thinking": "Plan."},
        {"type": "input_json_delta", "partial_json": '{"a": 1}'},
        {"type": "signature_delta", "signature": "EqQBCgIYAhIM"},
    ],
)
def test_a_known_delta_type_is_clean(tmp_path: Path, delta: dict[str, Any]) -> None:
    assert scan_items(tmp_path, cassette(with_delta(delta))) == []


# ---- no finding of today is lost ----


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("key sk-ant-api03-abcdefgh1234", "anthropic-api-key"),
        ("key sk-abcdefghijklmnopqrstuvwx", "generic-sk-key"),
        ("Authorization: Bearer abcdefghijklmnop1234", "bearer-token"),
        ('"x-api-key": "abcd1234"', "x-api-key-header"),
        ("x-api-key: abcd1234", "x-api-key-header"),
        (f"pan {VISA}", "luhn-valid-pan"),
        ("pan 4111-1111-1111-1111", "luhn-valid-pan"),
        ("pan 4111 1111 1111 1111", "luhn-valid-pan"),
        (f"leak {CANARY}", "internal-canary"),
    ],
)
def test_each_finding_of_today_is_still_found_in_text_and_in_a_cassette(
    tmp_path: Path, text: str, kind: str
) -> None:
    assert kind in kinds(scan_text(text, canaries=frozenset({CANARY})))
    found = scan_items(tmp_path, cassette(stream(text)))
    assert any(k == kind or k.endswith(kind) for k in kinds(found)), kinds(found)


def test_a_key_fragment_is_still_found() -> None:
    secret = "k" * 8 + "ABCDEFGHIJKLMNOP" + "z" * 8
    assert "api-key-fragment" in kinds(scan_text("see " + secret[5:25], secret=secret))
