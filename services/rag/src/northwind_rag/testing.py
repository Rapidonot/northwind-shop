"""Test double for the `Embedder` interface: no model, no download (RAG-01)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence


class FakeEmbedder:
    """Deterministic vectors derived from the text. `embedded` lists every text it was given."""

    def __init__(self, id: str = "fake:sha256:384", dim: int = 384) -> None:  # noqa: A002
        self._id = id
        self._dim = dim
        self.embedded: list[str] = []

    @property
    def id(self) -> str:
        return self._id

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(f"{self._id}|{text}".encode()).digest()
        return [digest[i % len(digest)] / 255 for i in range(self._dim)]
