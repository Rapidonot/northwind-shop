"""The smoke questions through the real assistant and index (SA-02).

`make record SUITE=assistant-smoke` only runs pytest, so these tests are what creates the cassette
Playwright replays. The `e2e` marker keeps them out of `make test` and `make test-integration`,
because they need the real index and the CI `python` job never ingests.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import psycopg
import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.retriever import IndexRetriever
from northwind_assistant.turn import Citations, TextDelta, TurnEvent, stream_answer
from northwind_common.llm import agent_runtime
from northwind_data.textscan import find_money_or_stock
from northwind_rag.cli import DEFAULT_DATABASE_URL
from northwind_rag.embedding import FastEmbedEmbedder

pytestmark = [pytest.mark.e2e, pytest.mark.req("SA-02"), pytest.mark.llm_suite("assistant-smoke")]

REPO = Path(__file__).resolve().parents[3]
QUESTIONS = json.loads((REPO / "apps/portal/e2e/questions.json").read_text(encoding="utf-8"))


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    return psycopg.connect(url, autocommit=True)


def ask(question: str, root: Path) -> list[TurnEvent]:
    runtime = agent_runtime(root=root)
    retriever = IndexRetriever(connect, FastEmbedEmbedder())

    async def collect() -> list[TurnEvent]:
        return [e async for e in stream_answer(question, Caller(region="US"), retriever, runtime)]

    return asyncio.run(collect())


def answer_of(events: list[TurnEvent]) -> str:
    return "".join(event.text for event in events if isinstance(event, TextDelta))


def test_a_product_question_streams_and_cites_a_source(llm_proxy: Any, tmp_path: Path) -> None:
    events = ask(QUESTIONS["product"], tmp_path)
    assert sum(isinstance(event, TextDelta) for event in events) >= 2
    assert isinstance(events[-1], Citations) and events[-1].source_ids


def test_a_price_question_gets_the_product_page_not_a_price(llm_proxy: Any, tmp_path: Path) -> None:
    """Hard rule 2: there are no live tools yet, so the assistant quotes no price or stock."""
    answer = answer_of(ask(QUESTIONS["price"], tmp_path))
    assert find_money_or_stock(answer) == []
    assert "product page" in answer.lower()
