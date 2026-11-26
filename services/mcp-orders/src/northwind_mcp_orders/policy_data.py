"""The public policy documents and product kinds that return eligibility reads (row #27, CHK-07).

Read once at start from `data/fixtures`, each file checked against its manifest first. Only public
policy documents are kept, and only the subcategory of public products: no internal document (hard
rule 7), price or stock (hard rule 2) is ever in memory here. A document that no longer says what
`returns_policy` relies on stops the server from starting (D14)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northwind_backend.fakes.orders import DATA
from northwind_common import returns_policy
from northwind_mcp_orders.book import DataMismatch

FIXTURES = DATA.parent / "fixtures"
FILES = ("documents.jsonl", "products.jsonl")
DOC_TYPES: Mapping[str, str] = {"policy_returns": "returns", "policy_shipping": "shipping"}


@dataclass(frozen=True)
class PolicyDocument:
    document_id: str
    title: str
    region: str
    kind: str  # "returns" or "shipping"
    body: str


@dataclass(frozen=True)
class PolicyData:
    documents: Mapping[tuple[str, str], PolicyDocument]  # (kind, region)
    subcategories: Mapping[str, str]  # public product ID -> subcategory

    @classmethod
    def from_fixtures(cls, fixtures: Path = FIXTURES) -> PolicyData:
        manifest = json.loads((fixtures / "manifest.json").read_text(encoding="utf-8"))
        rows: dict[str, list[dict[str, Any]]] = {}
        for name in FILES:
            raw = (fixtures / name).read_bytes()
            if manifest.get("files", {}).get(name) != hashlib.sha256(raw).hexdigest():
                raise DataMismatch(f"{name} does not match data/fixtures/manifest.json")
            rows[name] = [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
        documents = {
            (DOC_TYPES[row["doc_type"]], row["region"]): PolicyDocument(
                row["doc_id"], row["title"], row["region"], DOC_TYPES[row["doc_type"]], row["body"]
            )
            for row in rows["documents.jsonl"]
            if row["visibility"] == "public" and row["doc_type"] in DOC_TYPES
        }
        missing = returns_policy.missing_facts(
            {doc.document_id: doc.body for doc in documents.values()}
        )
        if missing:
            ids = sorted({fact.document_id for fact in missing})
            raise DataMismatch(f"{', '.join(ids)} no longer holds a returns policy fact")
        return cls(
            documents,
            {
                row["product_id"]: row["subcategory"]
                for row in rows["products.jsonl"]
                if row["visibility"] == "public"
            },
        )
