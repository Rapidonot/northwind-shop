"""NFR-07: the claims `data-flow.md` makes about internal text in recordings stay true (GOV-1).

Main's recordings only. Row #7's enrichment keeps no cassette, so this says nothing about that run.
Cassettes hold the request and the response. A response body is a JSON string, or for a streamed
reply a run of events whose deltas can split a word, and retrieved text is escaped for the prompt.
So a sentence is compared with what the model saw and said, not with the raw file.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.req("NFR-07")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "data" / "fixtures"
CONTEXTS = REPO / "evals" / "contexts" / "dense-only.jsonl"
MIN_LENGTH = 40
INTERNAL_PRODUCT = "P0009"
# A reply, thinking, and the input of a tool call. A tool call has no text delta at all.
STREAMED_KEYS = ("text", "thinking", "partial_json")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def normalised(text: str) -> str:
    return " ".join(html.unescape(text).split())


def sentences(documents: Iterable[dict[str, Any]], visibility: str) -> set[str]:
    found: set[str] = set()
    for document in documents:
        if document["visibility"] == visibility:
            parts = re.split(r"(?<=[.!?])\s+", normalised(document["body"]))
            found |= {part for part in parts if len(part) > MIN_LENGTH}
    return found


def streamed_text(body: str) -> str:
    """All the model wrote in a streamed response, joined back together."""
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    deltas = [event["delta"] for event in events if event["type"] == "content_block_delta"]
    return "".join(delta[key] for delta in deltas for key in STREAMED_KEYS if key in delta)


def strings(value: Any) -> Iterator[str]:
    """Every string in a parsed cassette, opening strings that are JSON or a stream themselves."""
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value
        if value.startswith("event: "):
            yield streamed_text(value)
        if value.startswith(("{", "[")):
            try:
                yield from strings(json.loads(value))
            except json.JSONDecodeError:
                pass


def recorded_text(cassettes: Path) -> str:
    paths = sorted(cassettes.rglob("*.json"))
    return "\n".join(
        normalised(text) for path in paths for text in strings(json.loads(path.read_text()))
    )


def found_in(wanted: set[str], text: str) -> set[str]:
    return {sentence for sentence in wanted if sentence in text}


@pytest.fixture(scope="module")
def documents() -> list[dict[str, Any]]:
    return read_jsonl(FIXTURES / "documents.jsonl")


@pytest.fixture(scope="module")
def recorded() -> str:
    return recorded_text(REPO / "cassettes")


def test_no_internal_sentence_is_in_a_cassette(
    documents: list[dict[str, Any]], recorded: str
) -> None:
    internal = sentences(documents, "internal")
    assert len({d["doc_id"] for d in documents if d["visibility"] == "internal"}) == 6
    assert internal, "no internal sentence to look for"
    assert found_in(internal, recorded) == set()


def test_the_same_scan_finds_public_sentences(
    documents: list[dict[str, Any]], recorded: str
) -> None:
    """Without this control, a scan that could not read a cassette would pass the check above."""
    assert found_in(sentences(documents, "public"), recorded)


def test_the_saved_contexts_do_not_name_the_internal_product() -> None:
    products = read_jsonl(FIXTURES / "products.jsonl")
    name = next(p["name"] for p in products if p["product_id"] == INTERNAL_PRODUCT)
    contexts = CONTEXTS.read_text()
    assert name not in contexts
    assert any(p["name"] in contexts for p in products), "the contexts name no product at all"


SENTENCE = "The clearance list for the coming season stays with the buying team."


def test_a_sentence_in_a_request_is_found(tmp_path: Path) -> None:
    cassette = {"match": {"body": {"messages": [{"content": f"<source>{SENTENCE}</source>"}]}}}
    (tmp_path / "a.json").write_text(json.dumps(cassette))
    assert found_in({SENTENCE}, recorded_text(tmp_path)) == {SENTENCE}


def test_a_sentence_in_a_response_body_is_found(tmp_path: Path) -> None:
    """Quotes, because a sentence JSON leaves alone is found even if the body is never opened."""
    sentence = 'The buying team calls it the "quiet list" and keeps it off the shop floor.'
    cassette = {"body": json.dumps({"content": [{"type": "text", "text": sentence}]})}
    (tmp_path / "a.json").write_text(json.dumps(cassette))
    assert found_in({sentence}, recorded_text(tmp_path)) == {sentence}


@pytest.mark.parametrize("key", ["text", "thinking", "partial_json"])
def test_a_sentence_split_across_a_stream_is_found(tmp_path: Path, key: str) -> None:
    """A streamed response is not JSON, and a delta can end in the middle of a word."""
    parts = (SENTENCE[:30], SENTENCE[30:])
    deltas = [{"type": "content_block_delta", "delta": {key: part}} for part in parts]
    body = "".join(f"event: content_block_delta\ndata: {json.dumps(d)}\n\n" for d in deltas)
    (tmp_path / "a.json").write_text(json.dumps({"kind": "sse", "body": body}))
    assert found_in({SENTENCE}, recorded_text(tmp_path)) == {SENTENCE}


NO_ARGUMENT_TOOL = "mcp__northwind-catalog__get_cart"  # the only tool that takes no argument


def is_bare_get_cart_call(body: str) -> bool:
    """Row #16, a stop-5 change the user approved (2026-11-18): a streamed response may hold no text
    only if it is exactly one `tool_use` block, named `get_cart` (the only tool with no argument),
    with input `{}` and no input delta that adds anything. Beside it only `thinking` blocks may
    appear (the model's own, which give no text here: `streamed_text` is already empty). Anything
    else without text, any other tool, or `get_cart` with any input, still fails the control."""
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    starts = [e["content_block"] for e in events if e["type"] == "content_block_start"]
    uses = [block for block in starts if block.get("type") == "tool_use"]
    others = [block for block in starts if block.get("type") not in ("tool_use", "thinking")]
    if len(uses) != 1 or others:
        return False
    [use] = uses
    deltas = [
        e["delta"].get("partial_json", "")
        for e in events
        if e["type"] == "content_block_delta" and e["delta"].get("type") == "input_json_delta"
    ]
    return use.get("name") == NO_ARGUMENT_TOOL and use.get("input") == {} and not "".join(deltas)


def test_every_streamed_recording_gives_its_answer_to_the_scan() -> None:
    """Without this control, a scan that lost a streamed answer would still find nothing. The one
    exception is a bare `get_cart` call, which has no answer to lose (`is_bare_get_cart_call`)."""
    cassettes = [json.loads(path.read_text()) for path in (REPO / "cassettes").rglob("*.json")]
    streamed = [cassette for cassette in cassettes if cassette["kind"] == "sse"]
    assert streamed, "no streamed recording to read"
    assert all(
        streamed_text(cassette["body"]) or is_bare_get_cart_call(cassette["body"])
        for cassette in streamed
    )


def _stream(*blocks: tuple[dict[str, Any], list[dict[str, Any]]]) -> str:
    """A minimal streamed body: each block's start, then its deltas."""
    events: list[dict[str, Any]] = [{"type": "message_start", "message": {}}]
    for index, (block, deltas) in enumerate(blocks):
        events.append({"type": "content_block_start", "index": index, "content_block": block})
        events += [{"type": "content_block_delta", "index": index, "delta": d} for d in deltas]
        events.append({"type": "content_block_stop", "index": index})
    events.append({"type": "message_stop"})
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


THINKING = ({"type": "thinking", "thinking": ""}, [{"type": "thinking_delta", "thinking": ""}])


def _use(name: str, partial: str = "") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    block = {"type": "tool_use", "id": "toolu_1", "name": name, "input": {}}
    return block, [{"type": "input_json_delta", "partial_json": partial}]


def _passes(body: str) -> bool:
    return bool(streamed_text(body)) or is_bare_get_cart_call(body)


def test_a_bare_get_cart_call_passes_the_control() -> None:
    assert _passes(_stream(_use(NO_ARGUMENT_TOOL)))
    assert _passes(_stream(THINKING, _use(NO_ARGUMENT_TOOL)))  # as recorded in W4


def test_a_text_bearing_response_with_its_text_removed_still_fails() -> None:
    """Failing-first (1): take a real recorded answer, remove its text: the control catches it."""
    bodies = [
        json.loads(path.read_text())["body"]
        for path in sorted((REPO / "cassettes/assistant-smoke").glob("*.json"))
    ]
    body = next(b for b in bodies if '"text_delta"' in b)
    assert _passes(body)
    stripped = re.sub(r'("type":\s*"text_delta",\s*"text":\s*)"(?:[^"\\]|\\.)*"', r'\1""', body)
    assert stripped != body and not streamed_text(stripped)
    assert not _passes(stripped)


@pytest.mark.parametrize(
    "body",
    [
        _stream(_use("mcp__northwind-catalog__get_variant")),  # another tool, with no input
        _stream(_use("mcp__northwind-rag__retrieve_context")),
        _stream(_use(NO_ARGUMENT_TOOL), _use(NO_ARGUMENT_TOOL)),  # two calls
        _stream(({"type": "text", "text": ""}, []), _use(NO_ARGUMENT_TOOL)),  # an empty text block
        _stream(),  # nothing at all
    ],
)
def test_any_other_response_without_text_still_fails(body: str) -> None:
    """Failing-first (2): only the bare get_cart call is excepted from the control."""
    assert not streamed_text(body)
    assert not _passes(body)


@pytest.mark.parametrize(
    "body",
    [
        _stream(_use("mcp__northwind-catalog__get_variant", '{"variant_id": "P0001-BLK-REG-M"}')),
        _stream(_use(NO_ARGUMENT_TOOL, '{"note": "x"}')),  # get_cart with input
    ],
)
def test_a_call_with_input_is_never_the_exception(body: str) -> None:
    """Failing-first (2), with input: the exception does not apply. Such a call passes the control
    only because the scan reads its input, as before row #16."""
    assert not is_bare_get_cart_call(body)
    assert streamed_text(body)


def test_a_sentence_escaped_for_the_prompt_is_found(tmp_path: Path) -> None:
    sentence = "The buyer's clearance list stays with the buying team this season."
    cassette = {"match": {"content": html.escape(sentence, quote=True)}}
    (tmp_path / "a.json").write_text(json.dumps(cassette))
    assert found_in({sentence}, recorded_text(tmp_path)) == {sentence}


def test_a_sentence_wrapped_over_lines_is_found(tmp_path: Path) -> None:
    cassette = {"match": {"content": SENTENCE.replace(" for the ", " for\nthe  ")}}
    (tmp_path / "a.json").write_text(json.dumps(cassette))
    assert found_in({SENTENCE}, recorded_text(tmp_path)) == {SENTENCE}


def test_a_short_sentence_is_not_looked_for() -> None:
    documents = [{"visibility": "internal", "body": f"Too short. {SENTENCE}"}]
    assert sentences(documents, "internal") == {SENTENCE}


def test_only_the_asked_visibility_counts() -> None:
    documents = [{"visibility": "public", "body": SENTENCE}]
    assert sentences(documents, "internal") == set()


def test_a_string_that_only_looks_like_json_is_kept() -> None:
    assert list(strings({"a": "{not json"})) == ["{not json"]
