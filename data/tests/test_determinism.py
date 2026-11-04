"""FOUND-1: same seed -> byte-identical output, independent of process and hash seed."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from northwind_data.dataset import Dataset
from northwind_data.generate import generate
from northwind_data.version import DEFAULT_SEED
from northwind_data.writer import sha256

SRC = Path(__file__).resolve().parents[1] / "src" / "northwind_data"


def _hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: sha256(data) for name, data in sorted(files.items())}


@pytest.mark.req("FOUND-1")
def test_same_seed_generates_byte_identical_output_twice() -> None:
    assert _hashes(generate(DEFAULT_SEED)) == _hashes(generate(DEFAULT_SEED))


@pytest.mark.req("FOUND-1")
def test_output_does_not_depend_on_process_or_hash_seed(tmp_path: Path) -> None:
    runs = []
    for hash_seed in ("1", "4242"):
        out = tmp_path / hash_seed
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        subprocess.run(  # noqa: S603 (fixed argv, no shell)
            [sys.executable, "-m", "northwind_data.cli", "--out", str(out)],
            check=True,
            env=env,
            capture_output=True,
        )
        runs.append({p.name: sha256(p.read_bytes()) for p in sorted(out.iterdir())})
    assert runs[0] == runs[1] == _hashes(generate(DEFAULT_SEED))


@pytest.mark.req("FOUND-1")
def test_a_different_seed_gives_different_data() -> None:
    a, b = generate(DEFAULT_SEED), generate(DEFAULT_SEED + 1)
    assert a["reviews.jsonl"] != b["reviews.jsonl"]
    assert a["stock.jsonl"] != b["stock.jsonl"]


@pytest.mark.req("FOUND-1")
def test_faker_is_pinned_exactly_and_recorded_in_the_manifest(ds: Dataset) -> None:
    pyproject = tomllib.loads((SRC.parents[1] / "pyproject.toml").read_text("utf-8"))
    installed = version("faker")
    assert f"faker=={installed}" in pyproject["project"]["dependencies"]
    assert ds.manifest["faker_version"] == installed


BANNED = re.compile(
    r"datetime\.now|date\.today|time\.time\(|uuid\.uuid4|uuid4\(|os\.urandom|\bsecrets\.|"
    r"random\.(?:random|choice|choices|randint|randrange|sample|shuffle|seed)\(|\bhash\("
)


@pytest.mark.req("FOUND-1")
def test_generator_source_has_no_clock_or_unseeded_randomness() -> None:
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for path in sorted(SRC.glob("*.py"))
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1)
        if BANNED.search(line)
    ]
    assert offenders == []
