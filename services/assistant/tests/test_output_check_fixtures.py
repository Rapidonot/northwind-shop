"""Over-blocking on trusted text (row #13, NFR-03): every public product, variant and document text
in the fixtures passes the output check with itself as the source, except the reviewed hit list.
FAQ-003 and FAQ-005 are on it by the user's decision (2026-11-11): they name stock and discount
codes, and an answer quoting them gets the class's fixed sentence, which says the same thing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_assistant.output_check import Source, hits

pytestmark = pytest.mark.req("NFR-03")

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"
REVIEWED_HIT_LIST = {"FAQ-003": {"stock"}, "FAQ-005": {"promotion"}}
KINDS = {"documents.jsonl": "document", "products.jsonl": "product", "variants.jsonl": "variant"}


def trusted_texts() -> list[tuple[str, Source]]:
    found = []
    for name, kind in KINDS.items():
        for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("visibility") == "internal":
                continue
            source_id = record.get("doc_id") or record.get("variant_id") or record["product_id"]
            for field, value in sorted(record.items()):
                if isinstance(value, str) and value.strip():
                    found.append((f"{source_id}.{field}", Source(source_id, kind, value)))
    return found


def test_trusted_fixture_text_passes_except_the_reviewed_hit_list() -> None:
    texts = trusted_texts()
    assert len(texts) > 1000
    blocked: dict[str, set[str]] = {}
    for _, source in texts:
        found = hits(source.text, [source], {source.source_id})
        if found:
            blocked.setdefault(source.source_id, set()).update(found)
    assert blocked == REVIEWED_HIT_LIST
