"""The chunk model (RAG-01).

"Doesn't apply" is always None (NULL in the index), never "" or an empty collection, so a filter
can tell "no brand" from "brand unknown because of a bug". A chunk that breaks this cannot exist.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Literal, get_args

SourceType = Literal["product", "variant", "review", "document"]
Visibility = Literal["public", "internal"]

# Upper limit for chunk text, prefix included. bge-small reads 512 tokens, about 2,000 characters
# of English, so a chunk at this limit is embedded whole.
MAX_CHUNK_CHARS = 1000
# The generator's bands, exactly. A looser check would pass an exact price as "139-139".
PRICE_BANDS = frozenset(
    {"0-25", "25-50", "50-75", "75-100", "100-150", "150-200", "200-300", "300+"}
)


class ChunkError(ValueError):
    """A source record cannot become a valid chunk. Aborts the run before anything is written."""


def parse_visibility(value: object, where: str) -> Visibility:
    """Fails closed: a missing or unknown visibility is an error, never public (hard rule 7)."""
    if value not in get_args(Visibility):
        raise ChunkError(f"{where}: visibility must be public or internal, got {value!r}")
    return "public" if value == "public" else "internal"


@dataclass(frozen=True)
class ChunkMetadata:
    category: str | None
    brand: str | None
    size: tuple[str, ...] | None
    colour: tuple[str, ...] | None
    material: str | None
    price_band: dict[str, str] | None
    region: tuple[str, ...] | None
    updated: date
    visibility: Visibility

    def __post_init__(self) -> None:
        parse_visibility(self.visibility, "metadata")
        for name in ("category", "brand", "size", "colour", "material", "price_band", "region"):
            value = getattr(self, name)
            if value is None:
                continue
            if not value or (isinstance(value, tuple) and not all(value)):
                raise ChunkError(f"metadata.{name}: use None for 'does not apply', got {value!r}")
        for name in ("category", "brand", "material"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ChunkError(f"metadata.{name}: expected text, got {value!r}")
        for region, band in (self.price_band or {}).items():
            if region not in (self.region or ()):
                raise ChunkError(f"metadata.price_band: {region!r} is not one of the regions")
            if not isinstance(band, str) or band not in PRICE_BANDS:
                raise ChunkError(f"metadata.price_band: {region} is not a band: {band!r}")


@dataclass(frozen=True)
class Chunk:
    source_type: SourceType
    source_id: str
    seq: int
    text: str
    metadata: ChunkMetadata
    parent_id: str | None = None
    doc_type: str | None = None
    # Generated, and only for finding the chunk (RAG-02). Kept out of `text` on purpose: `text`
    # is what a model reads and cites, so generated words never reach an answer (hard rules 5, 6).
    context: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ChunkError(f"{self.chunk_id}: empty text")
        if self.context is not None and not self.context.strip():
            raise ChunkError(f"{self.chunk_id}: use None for no context, got {self.context!r}")

    @property
    def chunk_id(self) -> str:
        """Stable across runs: it depends on the source record, never on the run."""
        return f"{self.source_type}:{self.source_id}:{self.seq}"

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def embed_text(self) -> str:
        """What gets embedded: the context, when there is one, in front of the text."""
        return f"{self.context}\n\n{self.text}" if self.context else self.text

    @property
    def embed_hash(self) -> str:
        return hashlib.sha256(self.embed_text.encode("utf-8")).hexdigest()

    @property
    def content_hash(self) -> str:
        """Covers everything stored for the chunk except the embedding. A plain chunk hashes as it
        did before RAG-02, so a plain index keeps its version."""
        payload: dict[str, object] = {
            "text_hash": self.text_hash,
            "parent_id": self.parent_id,
            "doc_type": self.doc_type,
            "metadata": asdict(self.metadata),
        }
        if self.context is not None:
            payload["context"] = self.context
        canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
