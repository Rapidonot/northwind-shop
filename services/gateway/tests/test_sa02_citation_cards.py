"""The `citations` event carries a card per cited source (row #17, SA-02), through the real CLI and
a scripted model: the portal shows product cards and a source link on every product claim from it.

Hard rule 2: no card field holds a price or stock figure. Hard rule 6: a review's card holds no
review text, because that text is attacker-writable.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from northwind_assistant.caller import Caller
from northwind_assistant.rag_client import RagEndpoint
from northwind_common.llm import agent_runtime
from northwind_gateway.app import app
from northwind_gateway.chat import ChatServices, chat_services
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("SA-02")

CHUNKS = [
    RetrievedChunk(
        "c1",
        "product",
        "P0178",
        "product",
        "Larchpell Larch Area Rug\n\nBrand: Larchpell. Department: Living.\n\nA flatweave rug.",
    ),
    RetrievedChunk(
        "c2",
        "review",
        "R01080",
        "review",
        "PLANTED review title\n\nVerified purchase. Rating: 3 of 5.\n\nWarm underfoot.",
    ),
    RetrievedChunk("c3", "document", "FAQ-019", "faq", "Returns (United States)\n\n30 days."),
]


class Retriever:
    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        return CHUNKS


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[ScriptedLLM, TestClient]]:
    with ScriptedLLM() as llm, serve_retriever(Retriever(), Caller) as (url, _):
        services = ChatServices(
            RagEndpoint(url, KEY), agent_runtime(base_url=llm.url, root=tmp_path / "rt")
        )
        app.dependency_overrides[chat_services] = lambda: services
        try:
            yield llm, TestClient(app)
        finally:
            app.dependency_overrides.clear()


def citations(text: str) -> dict[str, Any]:
    for frame in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if not line.startswith(":"))
        if lines.get("event") == "citations":
            return dict(json.loads(lines["data"]))
    raise AssertionError("no citations event")


def test_the_citations_event_carries_a_card_per_cited_source(
    client: tuple[ScriptedLLM, TestClient],
) -> None:
    llm, http = client
    llm.reply_stream("It is a flatweave rug [P0178]. ", "One reviewer found it warm [R01080].")
    data = citations(http.post("/api/chat", json={"message": "Tell me about the rug"}).text)
    assert data == {
        "source_ids": ["P0178", "R01080"],
        "sources": [
            {
                "id": "P0178",
                "type": "product",
                "title": "Larchpell Larch Area Rug",
                "brand": "Larchpell",
                "department": "Living",
            },
            {"id": "R01080", "type": "review"},
        ],
    }
    assert "PLANTED" not in json.dumps(data)


def test_an_uncited_source_gets_no_card(client: tuple[ScriptedLLM, TestClient]) -> None:
    llm, http = client
    llm.reply_stream("I can't confirm that from the sources.")
    data = citations(http.post("/api/chat", json={"message": "Is it fireproof?"}).text)
    assert data == {"source_ids": [], "sources": []}
