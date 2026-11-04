"""Read a generated dataset, from memory (`generate()`) or from a fixtures directory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass(frozen=True)
class Dataset:
    files: dict[str, bytes]

    @classmethod
    def from_dir(cls, path: Path = FIXTURES_DIR) -> Dataset:
        return cls(
            {
                p.name: p.read_bytes()
                for p in sorted(path.iterdir())
                if p.is_file() and not p.name.startswith(".")
            }
        )

    def jsonl(self, name: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.files[name].decode("utf-8").splitlines()]

    def json(self, name: str) -> Any:
        return json.loads(self.files[name])

    @property
    def manifest(self) -> dict[str, Any]:
        data: dict[str, Any] = self.json("manifest.json")
        return data

    @property
    def answer_key(self) -> dict[str, Any]:
        data: dict[str, Any] = self.json("_answer_key.json")
        return data

    def ingestible_records(self) -> list[tuple[str, dict[str, Any]]]:
        return [(name, rec) for name in self.manifest["ingestible"] for rec in self.jsonl(name)]
