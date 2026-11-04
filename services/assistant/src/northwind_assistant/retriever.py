"""`Retriever` over the RAG index. One short connection per search."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import psycopg

from northwind_assistant.caller import Caller
from northwind_rag.embedding import Embedder
from northwind_rag.retrieval import RetrievedChunk, retrieve_context


class IndexRetriever:
    def __init__(self, connect: Callable[[], psycopg.Connection], embedder: Embedder) -> None:
        self._connect = connect
        self._embedder = embedder

    def search(self, query: str, caller: Caller) -> Sequence[RetrievedChunk]:
        with self._connect() as conn:
            return retrieve_context(
                conn, self._embedder, query, region=caller.region, audience=caller.audience
            )
