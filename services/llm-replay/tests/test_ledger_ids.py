"""Ledger IDs and hashes written by the real ledger stay clear of the PAN check (RAG-12)."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.scan import scan_paths

pytestmark = pytest.mark.req("RAG-12")

USAGE = {"input_tokens": 10, "output_tokens": 5}


def records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_many_reservations_write_no_digit_in_any_id_and_only_short_hash_prefixes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, budget_usd=1000.0, run_budget_usd=None, run_id="r", suite="s")
    digests = {}
    for n in range(1500):
        digest = hashlib.sha256(f"request {n}".encode()).hexdigest()
        reservation = ledger.reserve(
            model="claude-sonnet-5-5", worst_case_usd=0.01, request_hash=digest
        )
        digests[reservation.id] = digest
        ledger.settle(reservation, USAGE)
    written = records(path)
    assert len(written) == 3000
    for record in written:
        record_id = str(record["id"])
        assert record_id
        assert not any(c.isdigit() for c in record_id), record_id
        digest = digests[record_id]
        if "request_hash" in record:  # a settle record may omit it
            shown = str(record["request_hash"])
            assert 0 < len(shown) <= 16
            assert digest.startswith(shown)
    assert sum("request_hash" in r for r in written) >= 1500
    assert len(digests) == 1500  # every reservation got its own ID
    assert scan_paths([path], ledger=path) == []


def test_ids_written_by_concurrent_threads_hold_no_digit(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, budget_usd=1000.0, run_budget_usd=None, run_id="r", suite="s")

    def work(start: int) -> None:
        for n in range(start, start + 100):
            digest = hashlib.sha256(str(n).encode()).hexdigest()
            reservation = ledger.reserve(
                model="claude-sonnet-5-5", worst_case_usd=0.01, request_hash=digest
            )
            ledger.settle(reservation, USAGE)

    threads = [threading.Thread(target=work, args=(k * 100,)) for k in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    written = records(path)
    assert len(written) == 1600
    assert not [r for r in written if any(c.isdigit() for c in str(r["id"]))]
    assert scan_paths([path], ledger=path) == []


def test_a_refused_reservation_writes_no_digit_id_either(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, budget_usd=0.5, run_budget_usd=None, run_id="r", suite="s")
    ledger.reserve(model="claude-sonnet-5-5", worst_case_usd=0.4, request_hash="ab" * 32)
    with pytest.raises(Exception, match="LLM_BUDGET_USD"):
        ledger.reserve(model="claude-sonnet-5-5", worst_case_usd=0.4, request_hash="cd" * 32)
    for record in records(path):
        assert not any(c.isdigit() for c in str(record["id"]))
        assert len(str(record.get("request_hash", ""))) <= 16
