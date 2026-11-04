"""More ways to split a secret across stream deltas (SA-02; FOUND-2 review 6, reassembly half)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.scan import reassembled_blocks, scan_paths, scan_stream
from northwind_llm_replay.scripted import blocks_sse

pytestmark = pytest.mark.req("SA-02")

CANARY = "NWCANARY-feedfacecafe"
CARD = "4242" * 4
KEY = "sk-ant-api03-abcdefghijkl"
USAGE = {"input_tokens": 1, "output_tokens": 1}


def delta(index: int, kind: str, key: str, text: str) -> str:
    event = {"type": "content_block_delta", "index": index, "delta": {"type": kind, key: text}}
    return f"event: content_block_delta\ndata: {json.dumps(event)}\n\n"


def text_stream(*chunks: str) -> str:
    return blocks_sse("m", [{"type": "text", "chunks": list(chunks)}], "end_turn", USAGE, "msg_1")


def cassette(body: str) -> Cassette:
    return Cassette(
        hash="b" * 64,
        suite="assistant-smoke",
        test_id="t",
        seq=0,
        kind="sse",
        match={"method": "POST", "path": "/v1/messages"},
        status=200,
        content_type="text/event-stream",
        body=body,
        model="m",
        usage={},
        cost_usd=0.0,
        recorded_at="2026-11-04T00:00:00+00:00",
    )


def kinds(findings: list) -> set[str]:  # type: ignore[type-arg]
    return {f.kind for f in findings}


@pytest.mark.parametrize("cut", range(1, len(CANARY)))
def test_a_canary_is_found_whichever_byte_the_stream_splits_at(cut: int) -> None:
    findings = scan_stream(
        text_stream(CANARY[:cut], CANARY[cut:]), "x", canaries=frozenset({CANARY})
    )
    assert "reassembled-internal-canary" in kinds(findings)


@pytest.mark.parametrize("cut", range(1, len(CARD)))
def test_a_card_is_found_whichever_byte_the_stream_splits_at(cut: int) -> None:
    assert "reassembled-luhn-valid-pan" in kinds(
        scan_stream(text_stream(CARD[:cut], CARD[cut:]), "x")
    )


def test_a_canary_split_into_single_characters_is_found() -> None:
    body = text_stream(*CANARY)
    assert "reassembled-internal-canary" in kinds(
        scan_stream(body, "x", canaries=frozenset({CANARY}))
    )


def test_a_grouped_card_split_across_deltas_is_found() -> None:
    grouped = " ".join(CARD[i : i + 4] for i in range(0, 16, 4))
    assert "reassembled-luhn-valid-pan" in kinds(
        scan_stream(text_stream(grouped[:7], grouped[7:]), "x")
    )


def test_a_secret_split_in_a_tool_call_argument_is_found() -> None:
    body = delta(1, "input_json_delta", "partial_json", '{"q": "' + CARD[:6]) + delta(
        1, "input_json_delta", "partial_json", CARD[6:] + '"}'
    )
    assert "reassembled-luhn-valid-pan" in kinds(scan_stream(body, "x"))


def test_a_secret_split_around_another_blocks_deltas_is_found() -> None:
    body = (
        delta(0, "text_delta", "text", CANARY[:9])
        + delta(1, "text_delta", "text", "something else")
        + delta(0, "text_delta", "text", CANARY[9:])
    )
    assert "reassembled-internal-canary" in kinds(
        scan_stream(body, "x", canaries=frozenset({CANARY}))
    )


def test_a_delta_with_no_index_is_block_zero() -> None:
    event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a"}}
    body = f"data: {json.dumps(event)}\n\n" * 2
    assert reassembled_blocks(body) == ["aa"]


def test_data_lines_without_a_space_and_crlf_are_read() -> None:
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "x"},
    }
    body = (f"event: content_block_delta\r\ndata:{json.dumps(event)}\r\n\r\n") * 2
    assert reassembled_blocks(body) == ["xx"]


def test_a_split_api_key_is_found_in_a_stream_and_refused_by_put(tmp_path: Path) -> None:
    body = text_stream(KEY[:10], KEY[10:])
    assert "reassembled-anthropic-api-key" in kinds(scan_stream(body, "x"))
    with pytest.raises(DirtyCassetteError):
        CassetteStore(tmp_path, "s", canaries=frozenset()).put(cassette(body))
    assert not list(tmp_path.rglob("*.json"))


def test_a_split_configured_secret_is_found() -> None:
    secret = "zz-not-a-known-shape-0123456789abcdef"  # noqa: S105 - synthetic
    findings = scan_stream(text_stream(secret[:20], secret[20:]), "x", secret=secret)
    assert "api-key-fragment" in kinds(findings)  # the kind `cmd_scan` hides the place of


def test_a_refused_put_leaves_an_existing_cassette_alone(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "s", canaries=frozenset({CANARY}))
    clean = cassette(text_stream("Wash ", "cold."))
    path = store.put(clean)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(DirtyCassetteError):
        store.put(cassette(text_stream(CANARY[:9], CANARY[9:])))
    assert path.read_text(encoding="utf-8") == before


def test_scan_paths_finds_a_split_card_and_key_and_passes_a_clean_stream(tmp_path: Path) -> None:
    directory = tmp_path / "cassettes" / "s"
    directory.mkdir(parents=True)
    bodies = {
        "card": text_stream(CARD[:5], CARD[5:]),
        "key": text_stream(KEY[:10], KEY[10:]),
        "clean": text_stream("Wash ", "cold ", "[FAQ-001]."),
    }
    for name, body in bodies.items():
        (directory / f"{name}.json").write_text(cassette(body).to_json(), encoding="utf-8")
    findings = scan_paths([tmp_path / "cassettes"], canaries=frozenset({CANARY}))
    flagged = {Path(f.path).stem for f in findings}
    assert flagged == {"card", "key"}


def test_split_digits_that_are_not_a_card_are_not_flagged() -> None:
    not_luhn = "4242424242424243"
    assert scan_stream(text_stream(not_luhn[:5], not_luhn[5:]), "x") == []
