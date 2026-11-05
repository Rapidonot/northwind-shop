"""RAG-02: `data/enrichment` holds model output, so the default scan covers it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from northwind_llm_replay.cli import EVAL_ROOTS, cmd_scan

pytestmark = pytest.mark.req("RAG-02")

CANARY = "CANARY-SYNTHETIC-ZETA-0001"
PAN = "4111" + "1111" * 3  # a Luhn-valid sandbox test card, built at runtime
SECRET = "sk-ant-" + "api03-" + "Zq7" * 20  # fake, never a real key


def test_the_enrichment_cache_is_a_default_scan_root() -> None:
    assert Path("data/enrichment") in EVAL_ROOTS


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch repo root: the default scan reads paths relative to the working directory."""
    key = tmp_path / "data" / "fixtures" / "_answer_key.json"
    key.parent.mkdir(parents=True)
    key.write_text(json.dumps({"internal_only": [{"canary": CANARY}]}), encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    monkeypatch.setenv("LLM_LEDGER_PATH", str(ledger))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def entry(context: str) -> str:
    row = {"chunk_id": "product:P1:0", "context": context, "status": "ok", "visibility": "public"}
    return json.dumps(row) + "\n"


def default_scan(repo: Path) -> int:
    args = argparse.Namespace(paths=None, cassettes_dir=None, env_file=repo / "no-such-key-file")
    return cmd_scan(args)


def put(repo: Path, line: str) -> None:
    path = repo / "data" / "enrichment" / "public.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line, encoding="utf-8")


def test_a_clean_cache_file_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(repo, entry("This passage sits inside the Northwind catalog record it belongs to."))
    assert default_scan(repo) == 0
    assert "clean" in capsys.readouterr().out


def test_a_missing_cache_directory_is_not_a_failure(repo: Path) -> None:
    assert default_scan(repo) == 0


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("secret shape", f"The key is {SECRET} here."),
        ("card number", f"Pay with card {PAN} today."),
        ("canary", f"This part covers {CANARY} in the guide."),
    ],
)
def test_a_secret_a_card_or_a_canary_in_the_cache_fails_the_default_scan(
    repo: Path, capsys: pytest.CaptureFixture[str], label: str, payload: str
) -> None:
    put(repo, entry(payload))
    assert default_scan(repo) == 1, label
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    assert "enrichment" in captured.err  # the finding names the file
    assert SECRET not in captured.out + captured.err
    assert PAN not in captured.out + captured.err


def test_the_scan_kinds_are_the_right_ones(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(repo, entry(f"{SECRET} and {PAN} and {CANARY}"))
    assert default_scan(repo) == 1
    err = capsys.readouterr().err
    assert "anthropic-api-key" in err
    assert "luhn-valid-pan" in err
    assert "canary" in err
