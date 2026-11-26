"""RAG-03: `data/embeddings` is committed and fed to every index, so the default scan covers it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from northwind_llm_replay.cli import EVAL_ROOTS, cmd_scan
from northwind_llm_replay.scan import Finding, load_canaries, scan_paths

pytestmark = pytest.mark.req("RAG-03")

REPO = Path(__file__).resolve().parents[3]
PAN = "4111" + "1111" * 3  # a Luhn-valid sandbox test card, built at runtime


def test_the_frozen_vectors_are_a_default_scan_root() -> None:
    assert Path("data/embeddings") in EVAL_ROOTS


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    key = tmp_path / "data" / "fixtures" / "_answer_key.json"
    key.parent.mkdir(parents=True)
    key.write_text(json.dumps({"internal_only": []}), encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_LEDGER_PATH", str(ledger))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def put(repo: Path, text: str) -> None:
    path = repo / "data" / "embeddings" / "fastembed-x-4.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def default_scan(repo: Path) -> int:
    args = argparse.Namespace(paths=None, cassettes_dir=None, env_file=repo / "no-such-key-file")
    return cmd_scan(args)


def test_a_card_number_in_a_frozen_file_fails_the_default_scan(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put(repo, json.dumps({"key": "g" * 64, "vector": [int(PAN), 0, 0, 0]}) + "\n")
    assert default_scan(repo) == 1
    captured = capsys.readouterr()
    assert "embeddings" in captured.err
    assert PAN not in captured.out + captured.err


def test_a_clean_frozen_file_passes(repo: Path) -> None:
    put(repo, json.dumps({"key": "g" * 64, "vector": [0.5, 0.5, 0.5, 0.5]}) + "\n")
    assert default_scan(repo) == 0


def committed_scan(folder: Path, repo: Path) -> list[Finding]:
    """Row 18a (M8): every file of the folder, whatever its name, with the answer key's canaries."""
    return scan_paths([folder], canaries=load_canaries(repo, required=True))


def test_the_committed_file_scans_clean() -> None:
    """H1 of the plan review: hex digests hold Luhn-valid digit runs, so keys are digit-free and
    numbers are short. Every finding here would fail `make ci`."""
    folder = REPO / "data" / "embeddings"
    assert any(folder.iterdir())
    assert committed_scan(folder, REPO) == []


def test_the_committed_scan_reads_every_file_with_the_canaries(tmp_path: Path) -> None:
    key = tmp_path / "data" / "fixtures" / "_answer_key.json"
    key.parent.mkdir(parents=True)
    key.write_text(json.dumps({"internal_only": [{"canary": "NWCANARY-0ddba11"}]}), "utf-8")
    folder = tmp_path / "data" / "embeddings"
    folder.mkdir(parents=True)
    (folder / "vectors.json").write_text('{"note": "NWCANARY-0ddba11"}', encoding="utf-8")
    (folder / "README").write_text(f"vector {PAN}", encoding="utf-8")
    (folder / "clean.jsonl").write_text('{"vector": [0.5]}\n', encoding="utf-8")
    flagged = {(Path(f.path).name, f.kind) for f in committed_scan(folder, tmp_path)}
    assert flagged == {("vectors.json", "internal-canary"), ("README", "luhn-valid-pan")}
