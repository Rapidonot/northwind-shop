"""The `Embedder` interface, its fastembed implementation (RAG-01), and frozen vectors (RAG-03).

Frozen vectors: the same text embedded on arm64 and on x86_64, or in a batch of another shape,
comes out different by up to about 4e-4 per component. That is enough to swap near-tied dense
ranks, and it made PR #30's Linux gate disagree with macOS (docs/reviews/2026-11-08-RAG-03-drift.md,
decision 0012). `data/embeddings/<embedder>.jsonl` holds the vectors that produced every saved
context: the fixture chunks, taken bit-exact from the index, and the eval and smoke queries.
`CachedEmbedder` serves them and sends any other text to the model, so a new text still works.

The file is committed, scanned, and only ever added to (`make embed-cache`, `make eval-integrity`).
Keys are a digit-free sha256 of the text, because a hex digest can hold a digit run that passes the
Luhn check, and the scanner allows no exemption. Each number is the shortest form that reads back
to the same float32, at most 9 significant digits, so no run of 13 or more digits appears.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import psycopg

    from northwind_rag.models import Chunk

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384
FROZEN_DIR = Path(__file__).resolve().parents[4] / "data" / "embeddings"
NORM_TOLERANCE = 1e-3
# How far a frozen vector may sit from what the model gives its text today. Measured on macOS:
# batch shape moves a vector by at most 6.2e-7 in cosine distance, and two different fixture texts
# are never closer than 1.4e-3, so a vector taken from another text is refused. The limit measures
# distance, not direction: a nudge aimed inside it can still move near-tied ranks (an open item from
# the drift fix review). x86_64's distance is not measured yet: integrity prints the largest seen.
VOUCH_TOLERANCE = 1e-4
_MAX_DIGITS = 9  # enough for any float32 to read back exactly
FLOAT32_MAX = 3.4028234663852886e38
_DIGITS_TO_LETTERS = str.maketrans("0123456789", "ghijklmnop")
_KEY = re.compile(r"[a-p]{64}")


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


# ---- frozen vectors ---------------------------------------------------------------------------


class FrozenVectorError(ValueError):
    """The frozen file, or an entry for it, is not exactly what it must be. Nothing is written."""


def digit_free(hex_digest: str) -> str:
    """A hex digest with its digits mapped to letters: no digit run for the PAN check to read.
    `data/enrichment` keys use the same mapping."""
    return hex_digest.translate(_DIGITS_TO_LETTERS)


def frozen_key(text: str) -> str:
    return digit_free(hashlib.sha256(text.encode("utf-8")).hexdigest())


def frozen_path(embedder_id: str, directory: Path = FROZEN_DIR) -> Path:
    return directory / (re.sub(r"[:/]", "-", embedder_id) + ".jsonl")


def to_float32(x: float) -> float:
    return float(struct.unpack("<f", struct.pack("<f", x))[0])


def format_float32(x: float) -> str:
    """The shortest `%g` form that reads back, by `to_float32(float(text))`, to the same bits."""
    value = to_float32(x)
    for digits in range(1, _MAX_DIGITS + 1):
        text = f"{value:.{digits}g}"
        if abs(float(text)) > FLOAT32_MAX:
            continue  # rounded past the largest float32: more digits are needed
        same_sign = math.copysign(1, float(text)) == math.copysign(1, value)
        if to_float32(float(text)) == value and same_sign:
            return text
    raise FrozenVectorError(f"{value!r} does not read back exactly in {_MAX_DIGITS} digits")


def frozen_line(key: str, vector: Sequence[float]) -> str:
    numbers = ",".join(format_float32(x) for x in vector)
    return f'{{"key": "{key}", "vector": [{numbers}]}}'


def _refuse_constant(name: str) -> float:
    raise FrozenVectorError(f"{name} is not a vector component")


def _parse(line: str, dim: int, where: str) -> tuple[str, tuple[float, ...]]:
    try:
        record = json.loads(line, parse_constant=_refuse_constant)
    except ValueError as error:  # JSONDecodeError, and an integer literal too long to parse
        raise FrozenVectorError(f"{where}: not JSON ({error})") from None
    if not isinstance(record, dict) or set(record) != {"key", "vector"}:
        raise FrozenVectorError(f"{where}: an entry is exactly a key and a vector")
    key, vector = record["key"], record["vector"]
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise FrozenVectorError(f"{where}: a key is 64 letters a to p")
    if not isinstance(vector, list) or len(vector) != dim:
        raise FrozenVectorError(f"{where}: a vector has {dim} components")
    if not all(isinstance(x, int | float) and not isinstance(x, bool) for x in vector):
        raise FrozenVectorError(f"{where}: every component is a number")
    try:
        floats = [float(x) for x in vector]
    except OverflowError:  # an integer beyond float64
        raise FrozenVectorError(f"{where}: every component is a finite float32") from None
    if not all(math.isfinite(x) and abs(x) <= FLOAT32_MAX for x in floats):
        raise FrozenVectorError(f"{where}: every component is a finite float32")
    values = tuple(to_float32(float(x)) for x in vector)
    if abs(math.sqrt(math.fsum(x * x for x in values)) - 1.0) > NORM_TOLERANCE:
        raise FrozenVectorError(f"{where}: a vector has unit norm")
    return key, values


_LOADED: dict[tuple[str, int, int, int], dict[str, tuple[float, ...]]] = {}


def load_frozen(path: Path, dim: int) -> dict[str, tuple[float, ...]]:
    """Every entry in `path`, checked, as exact float32 values. Fails closed on any bad line."""
    stat = path.stat()
    cache = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, dim)
    if cache not in _LOADED:
        entries: dict[str, tuple[float, ...]] = {}
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            key, values = _parse(line, dim, f"{path.name}:{number}")
            if key in entries:
                raise FrozenVectorError(f"{path.name}:{number}: key {key} appears twice")
            entries[key] = values
        _LOADED.clear()  # one file in memory at a time is enough
        _LOADED[cache] = entries
    return _LOADED[cache]


class CachedEmbedder:
    """`inner`, except that a text with a frozen vector gets that vector. The id stays the inner
    one, so `retriever_id`, saved contexts and stored embeddings all stay valid. With no file for
    the inner id (every test fake), each call goes straight through and nothing is read."""

    def __init__(self, inner: Embedder, directory: Path = FROZEN_DIR) -> None:
        self.inner = inner
        self._directory = directory  # the path follows `inner.id`, read on first use
        self.hits = 0
        self.misses = 0

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def dim(self) -> int:
        return self.inner.dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        path = frozen_path(self.inner.id, self._directory)
        if not path.exists():
            self.misses += len(texts)
            return self.inner.embed(texts)
        frozen = load_frozen(path, self.dim)
        found = [frozen.get(frozen_key(text)) for text in texts]
        missing = [i for i, vector in enumerate(found) if vector is None]
        live = self.inner.embed([texts[i] for i in missing]) if missing else []
        by_index = dict(zip(missing, live, strict=True))
        self.hits += len(texts) - len(missing)
        self.misses += len(missing)
        return [list(v) if v is not None else by_index[i] for i, v in enumerate(found)]


def cached(embedder: Embedder) -> CachedEmbedder:
    """`embedder` behind its frozen vectors; an embedder that already is one comes back as is."""
    return embedder if isinstance(embedder, CachedEmbedder) else CachedEmbedder(embedder)


def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.fsum(x * x for x in a), math.fsum(y * y for y in b)
    return math.inf if na == 0.0 or nb == 0.0 else 1.0 - dot / math.sqrt(na * nb)


def vouch_distances(
    entries: Mapping[str, Sequence[float]], texts: Mapping[str, str], embedder: Embedder
) -> dict[str, float]:
    """Each known key's cosine distance from what `embedder` gives its text (known keys only)."""
    known = [key for key in entries if key in texts]
    fresh = embedder.embed([texts[key] for key in known]) if known else []
    return {
        key: _cosine_distance(entries[key], vector)
        for key, vector in zip(known, fresh, strict=True)
    }


def unvouched(
    entries: Mapping[str, Sequence[float]],
    texts: Mapping[str, str],
    embedder: Embedder,
    tolerance: float = VOUCH_TOLERANCE,
    distances: Mapping[str, float] | None = None,
) -> list[str]:
    """Keys the model does not vouch for: no known text behind the key, or a vector further than
    `tolerance` (cosine distance) from what `embedder` gives that text. This is what keeps a planted
    vector out of the file, since a reader cannot check 384 numbers by eye."""
    problems = [
        f"frozen vector {key}: no fixture, eval or smoke text has this key"
        for key in entries
        if key not in texts
    ]
    measured = vouch_distances(entries, texts, embedder) if distances is None else distances
    for key, distance in measured.items():
        if not distance <= tolerance:
            problems.append(
                f"frozen vector {key}: {distance:.1e} from the model's ({tolerance:.0e} allowed)"
            )
    return problems


def add_frozen(path: Path, entries: Mapping[str, Sequence[float]], dim: int) -> int:
    """Adds the keys `path` does not have yet and returns how many. An existing key is never
    rewritten: the same vector is skipped, a different one is refused. Existing lines stay
    byte-identical, and the result is checked before it replaces the file."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing = {json.loads(line)["key"]: line for line in lines}
    added: dict[str, str] = {}
    for key, vector in entries.items():
        if not _KEY.fullmatch(key):
            raise FrozenVectorError(f"key {key!r} is not 64 letters a to p")
        line = frozen_line(key, vector)
        if _parse(line, dim, key)[1] != tuple(to_float32(x) for x in vector):
            raise FrozenVectorError(f"{key}: the written vector does not read back exactly")
        if key in existing:
            if _parse(existing[key], dim, key)[1] != _parse(line, dim, key)[1]:
                raise FrozenVectorError(f"{key}: an existing frozen vector may not change")
            continue
        added[key] = line
    if not added:
        return 0
    merged = sorted([*existing.values(), *added.values()], key=lambda x: json.loads(x)["key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(f"{line}\n" for line in merged), encoding="utf-8")
    load_frozen(temporary, dim)  # every line, old and new, must load
    os.replace(temporary, path)
    return len(added)


# Plain rows only: generated context stays inside the index writers (decision 0010), so this never
# fetches it, and a plain chunk's embedded text is its text.
_ROWS = """SELECT chunk_id, text, embedder_id, embedding::real[]::float8[]
FROM rag_chunks WHERE chunk_id = ANY(%(ids)s) AND context IS NULL"""


def export_chunk_vectors(
    conn: psycopg.Connection, chunks: Sequence[Chunk], embedder_id: str
) -> dict[str, list[float]]:
    """Each chunk's stored vector, bit-exact, keyed by its text. Refuses a chunk with no plain row
    (missing, or enriched), a row holding other text or from another embedder, and two chunks with
    the same text whose stored vectors differ."""
    rows = {r[0]: r for r in conn.execute(_ROWS, {"ids": [c.chunk_id for c in chunks]})}
    out: dict[str, list[float]] = {}
    for chunk in chunks:
        row = rows.get(chunk.chunk_id)
        if row is None:
            raise FrozenVectorError(f"{chunk.chunk_id} has no plain row in the index")
        _, text, embedder, vector = row
        if text != chunk.text:
            raise FrozenVectorError(f"{chunk.chunk_id}: the index row holds other text")
        if embedder != embedder_id:
            raise FrozenVectorError(f"{chunk.chunk_id} was embedded by {embedder}")
        key = frozen_key(chunk.text)
        exact = [to_float32(x) for x in vector]
        if key in out and out[key] != exact:
            raise FrozenVectorError(f"{chunk.chunk_id}: same text, another stored vector")
        out[key] = exact
    return out


def check_frozen(
    conn: psycopg.Connection, chunks: Sequence[Chunk], path: Path, embedder_id: str, dim: int
) -> list[str]:
    """Problems if any chunk lacks an entry, or its entry differs from the index by a single
    bit. Writes nothing."""
    frozen = load_frozen(path, dim) if path.exists() else {}
    rows = {r[0]: r for r in conn.execute(_ROWS, {"ids": [c.chunk_id for c in chunks]})}
    problems: list[str] = []
    for chunk in chunks:
        entry = frozen.get(frozen_key(chunk.text))
        row = rows.get(chunk.chunk_id)
        if entry is None:
            problems.append(f"{chunk.chunk_id}: no frozen vector")
        elif row is None or row[1] != chunk.text or row[2] != embedder_id:
            problems.append(f"{chunk.chunk_id}: no plain row from {embedder_id} with this text")
        elif list(entry) != [to_float32(x) for x in row[3]]:
            problems.append(f"{chunk.chunk_id}: the frozen vector differs from the index")
    return problems
