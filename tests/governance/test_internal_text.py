"""NFR-07: the claims `data-flow.md` makes about internal text in recordings stay true (GOV-1).

Main's recordings only. Row #7's enrichment keeps no cassette, so this says nothing about that run.
Cassettes hold the request and the response, with the response body as a JSON string and retrieved
text escaped for the prompt, so a sentence is compared with what the model saw, not the raw file.
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


def strings(value: Any) -> Iterator[str]:
    """Every string in a parsed cassette, opening strings that are JSON themselves."""
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value
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
