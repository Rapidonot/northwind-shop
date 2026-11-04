"""The scanner reads streamed text as a whole (SA-02; FOUND-2 review 6, reassembly half).

A cassette stores raw SSE, so text split across deltas is never on one line. SA-02 records the first
real multi-delta cassettes, and a split canary or card number would otherwise pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.scan import reassembled_blocks, scan_paths, scan_stream, scan_text
from northwind_llm_replay.scripted import blocks_sse

pytestmark = pytest.mark.req("SA-02")

CANARY = "NWCANARY-feedfacecafe"
CARD = "4242" * 4
USAGE = {"input_tokens": 1, "output_tokens": 1}


def stream(*chunks: str) -> str:
    return blocks_sse("m", [{"type": "text", "chunks": list(chunks)}], "end_turn", USAGE, "msg_1")


def cassette(body: str, request_hash: str = "a" * 64) -> Cassette:
    return Cassette(
        hash=request_hash,
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


def event(name: str, **data: object) -> str:
    return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"


def test_text_split_across_deltas_is_put_back_together() -> None:
    assert reassembled_blocks(stream("Wash ", "it ", "cold.")) == ["Wash it cold."]


def test_a_cassette_and_its_body_give_the_same_text() -> None:
    body = stream("Wash ", "cold.")
    assert reassembled_blocks(cassette(body).to_json()) == reassembled_blocks(body)


def test_blocks_stay_separate_and_in_index_order() -> None:
    body = "".join(
        event("content_block_delta", index=i, delta={"type": "text_delta", "text": text})
        for i, text in [(1, "b"), (0, "a"), (1, "c")]
    )
    assert reassembled_blocks(body) == ["a", "bc"]


@pytest.mark.parametrize(
    ("kind", "key"),
    [("text_delta", "text"), ("thinking_delta", "thinking"), ("input_json_delta", "partial_json")],
)
def test_every_kind_of_delta_counts(kind: str, key: str) -> None:
    body = "".join(
        event("content_block_delta", index=0, delta={"type": kind, key: part})
        for part in (CANARY[:9], CANARY[9:])
    )
    assert reassembled_blocks(body) == [CANARY]


@pytest.mark.parametrize("document", ["", "not json", '{"body": 3}', '{"status": 200}', "data: {"])
def test_a_document_with_no_stream_has_no_blocks(document: str) -> None:
    assert reassembled_blocks(document) == []


def test_the_raw_text_alone_misses_a_split_canary() -> None:
    """Why the reassembly exists: the line scan finds nothing here."""
    body = stream(CANARY[:9], CANARY[9:])
    assert scan_text(body, "x", canaries=frozenset({CANARY})) == []


@pytest.mark.parametrize(
    ("chunks", "kind"),
    [
        ((CANARY[:9], CANARY[9:]), "internal-canary"),
        ((CARD[:7], CARD[7:]), "luhn-valid-pan"),
        (("sk-ant-api03", "-abcdefghijkl"), "anthropic-api-key"),
    ],
    ids=["canary", "card", "key"],
)
def test_a_split_secret_is_found_by_scan_stream(chunks: tuple[str, str], kind: str) -> None:
    findings = scan_stream(stream(*chunks), "x", canaries=frozenset({CANARY}))
    assert f"reassembled-{kind}" in [finding.kind for finding in findings]


def test_scan_paths_finds_a_split_canary_in_a_new_suite(tmp_path: Path) -> None:
    """A new cassette suite is covered with no change to the scan roots."""
    path = tmp_path / "cassettes" / "assistant-smoke" / "x.json"
    path.parent.mkdir(parents=True)
    path.write_text(cassette(stream(CANARY[:9], CANARY[9:])).to_json(), encoding="utf-8")
    findings = scan_paths([tmp_path / "cassettes"], canaries=frozenset({CANARY}))
    assert "reassembled-internal-canary" in [finding.kind for finding in findings]


@pytest.mark.parametrize(
    "chunks", [(CANARY[:9], CANARY[9:]), (CARD[:7], CARD[7:])], ids=["canary", "card"]
)
def test_put_refuses_a_split_secret(tmp_path: Path, chunks: tuple[str, str]) -> None:
    store = CassetteStore(tmp_path, "assistant-smoke", canaries=frozenset({CANARY}))
    with pytest.raises(DirtyCassetteError):
        store.put(cassette(stream(*chunks)))
    assert not list(tmp_path.rglob("*.json"))


def test_put_keeps_a_clean_multi_delta_stream(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path, "assistant-smoke", canaries=frozenset({CANARY}))
    assert store.put(cassette(stream("Wash ", "cold."))).exists()
