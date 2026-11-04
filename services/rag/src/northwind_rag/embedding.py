"""The `Embedder` interface and its local fastembed implementation (RAG-01)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384


class Embedder(Protocol):
    @property
    def id(self) -> str:
        """Names the model exactly. Stored per embedding: a new id re-embeds every chunk."""

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """Loads the model on first use, so importing this module stays cheap."""

    def __init__(self, model: str = DEFAULT_MODEL, dim: int = DEFAULT_DIM) -> None:
        self._model_name = model
        self._dim = dim
        self._model: object | None = None

    @property
    def id(self) -> str:
        return f"fastembed:{self._model_name}:{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from fastembed import TextEmbedding

        if not isinstance(self._model, TextEmbedding):
            self._model = TextEmbedding(model_name=self._model_name)
        return [[float(x) for x in vector] for vector in self._model.embed(list(texts))]
