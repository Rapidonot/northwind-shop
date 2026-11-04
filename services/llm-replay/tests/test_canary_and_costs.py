"""Canary scanning and fixed-precision costs, both pulled forward into RAG-12.

Canaries come from the answer key's `internal_only[].canary` (hard rule 7): an internal document's
marker must never reach a cassette, a saved eval context or a log. Costs are rounded to fixed
precision so a long float digit run cannot pass the scanner's Luhn check and refuse a recording
already paid for. Everything here is synthetic.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from northwind_llm_replay import cli
from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.ledger import Ledger
from northwind_llm_replay.pricing import COST_DECIMALS, cost_usd, worst_case_usd
from northwind_llm_replay.scan import ANSWER_KEY, load_canaries, scan_paths, scan_text

pytestmark = pytest.mark.req("RAG-12")

CANARY = "NWCANARY-" + "0123456789ab"  # fake
OTHER = "NWCANARY-" + "ba9876543210"


def write_key(root: Path, canaries: list[str]) -> None:
    path = root / ANSWER_KEY
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"internal_only": [{"canary": c} for c in canaries]}))


def cassette(body: str = "hello", cost: float = 0.003) -> Cassette:
    return Cassette(
        hash="a" * 64,
        suite="s",
        recorded_at="2026-11-04T00:00:00+00:00",
        match={"method": "POST", "path": "/v1/messages", "body": {"x": 1}},
        status=200,
        content_type="application/json",
        kind="json",
        body=body,
        model="claude-sonnet-5-5",
        usage={"input_tokens": 1, "output_tokens": 1},
        cost_usd=cost,
    )


# --- canaries --------------------------------------------------------------------------------


def test_load_canaries_reads_every_internal_only_entry(tmp_path: Path) -> None:
    write_key(tmp_path, [CANARY, OTHER])
    assert load_canaries(tmp_path) == frozenset({CANARY, OTHER})


def test_load_canaries_is_empty_without_an_answer_key_or_internal_entries(tmp_path: Path) -> None:
    assert load_canaries(tmp_path) == frozenset()
    path = tmp_path / ANSWER_KEY
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    assert load_canaries(tmp_path) == frozenset()


def test_load_canaries_finds_the_real_answer_key() -> None:
    repo = Path(__file__).resolve().parents[3]
    canaries = load_canaries(repo)
    assert canaries
    assert all(c.startswith("NWCANARY-") for c in canaries)


def test_the_scanner_catches_a_canary_embedded_anywhere_in_a_line() -> None:
    for text in (CANARY, f"x{CANARY}y", f'{{"text": "see {CANARY}."}}'):
        assert {f.kind for f in scan_text(text, canaries=frozenset({CANARY}))} == {
            "internal-canary"
        }


def test_without_canaries_the_same_text_is_clean() -> None:
    assert scan_text(CANARY) == []


def test_the_scanner_reports_the_line_and_every_canary_kind_once_per_line() -> None:
    findings = scan_text(f"clean\n{CANARY} and {OTHER}\nclean", canaries=frozenset({CANARY, OTHER}))
    assert [f.line for f in findings] == [2]


def test_findings_name_the_kind_but_never_print_the_canary(tmp_path: Path) -> None:
    [finding] = scan_text(f"a {CANARY}", "somewhere.json", canaries=frozenset({CANARY}))
    assert "NWCANARY" not in str(finding)
    assert "internal-canary" in str(finding)


def test_the_store_refuses_a_cassette_with_a_canary_and_writes_nothing(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset({CANARY}))
    with pytest.raises(DirtyCassetteError) as error:
        store.put(cassette(body=f"the launch record says {CANARY}"))
    assert "internal-canary" in str(error.value)
    assert CANARY not in str(error.value)
    assert not list(tmp_path.rglob("*.json"))
    assert store.all() == []


def test_the_store_refuses_a_canary_in_the_match_key_too(tmp_path: Path) -> None:
    """The request side: an internal chunk sent to the model is as bad as one in the answer."""
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset({CANARY}))
    dirty = cassette()
    dirty.match = {**dirty.match, "body": {"messages": [{"content": f"<source>{CANARY}</source>"}]}}
    with pytest.raises(DirtyCassetteError):
        store.put(dirty)
    assert not list(tmp_path.rglob("*.json"))


def test_the_store_loads_canaries_from_the_answer_key_beside_its_root(tmp_path: Path) -> None:
    write_key(tmp_path, [CANARY])
    store = CassetteStore(tmp_path / "cassettes", "s")  # canaries=None: read from root.parent
    with pytest.raises(DirtyCassetteError):
        store.put(cassette(body=CANARY))


def test_the_store_accepts_a_clean_cassette_when_canaries_are_loaded(tmp_path: Path) -> None:
    write_key(tmp_path, [CANARY])
    store = CassetteStore(tmp_path / "cassettes", "s")
    path = store.put(cassette(body="a clean answer"))
    assert path.exists()
    assert store.get("a" * 64) == cassette(body="a clean answer")


def test_scan_paths_flags_a_canary_in_a_saved_context(tmp_path: Path) -> None:
    context = tmp_path / "evals" / "contexts" / "dense-only.jsonl"
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"chunks": [{"text": f"internal {CANARY}"}]}) + "\n")
    found = scan_paths([context.parent], canaries=frozenset({CANARY}))
    assert [f.kind for f in found] == ["internal-canary"]
    assert scan_paths([context.parent]) == []


def test_make_scan_cassettes_fails_on_a_canary_in_the_default_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_key(tmp_path, [CANARY])
    context = tmp_path / "evals" / "contexts"
    context.mkdir(parents=True)
    (context / "dense-only.jsonl").write_text(f'{{"text": "{CANARY}"}}\n')
    (tmp_path / "cassettes").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "no-ledger.jsonl"))
    code = cli.main(["scan", "--env-file", str(tmp_path / "no-such-env-file")])
    captured = capsys.readouterr()
    assert code == 1
    assert "internal-canary" in captured.err
    assert CANARY not in captured.err + captured.out


def test_scan_is_clean_when_there_is_no_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_key(tmp_path, [CANARY])
    (tmp_path / "cassettes").mkdir()
    (tmp_path / "evals" / "contexts").mkdir(parents=True)
    (tmp_path / "evals" / "contexts" / "c.jsonl").write_text('{"text": "plain"}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_LEDGER_PATH", str(tmp_path / "no-ledger.jsonl"))
    assert cli.main(["scan", "--env-file", str(tmp_path / "no-such-env-file")]) == 0


def test_committed_contexts_and_cassettes_hold_no_canary() -> None:
    repo = Path(__file__).resolve().parents[3]
    assert (
        scan_paths([repo / "cassettes", repo / "evals" / "contexts"], canaries=load_canaries(repo))
        == []
    )


# --- fixed-precision costs -------------------------------------------------------------------


def decimals(value: float) -> int:
    text = repr(value)
    return len(text.split(".")[1]) if "." in text and "e" not in text else COST_DECIMALS


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-haiku-5-5"])
def test_costs_never_exceed_the_fixed_precision_over_many_usages(model: str) -> None:
    rng = random.Random(12)  # noqa: S311 - test data, not security
    for _ in range(500):
        usage = {
            "input_tokens": rng.randrange(1, 60_000),
            "output_tokens": rng.randrange(1, 4_000),
            "cache_read_input_tokens": rng.randrange(0, 30_000),
            "cache_creation_input_tokens": rng.randrange(0, 10_000),
        }
        cost = cost_usd(model, usage)
        assert cost == round(cost, COST_DECIMALS)
        assert decimals(cost) <= COST_DECIMALS


def test_the_precision_is_finer_than_the_cheapest_token_rate() -> None:
    """One input token on the cheapest model must not round to nothing."""
    assert cost_usd("claude-haiku-5-5", {"input_tokens": 1}) > 0
    assert cost_usd("claude-haiku-5-5", {"output_tokens": 1}) > 0


def test_a_cost_is_still_exact_to_the_cent_at_fixed_precision() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cost_usd("claude-sonnet-5-5", usage) == pytest.approx(12.0, abs=1e-9)


def test_worst_case_is_also_fixed_precision() -> None:
    value = worst_case_usd("claude-sonnet-5-5", 12_345, 1024)
    assert value == round(value, COST_DECIMALS)


def test_ledger_settles_at_fixed_precision_and_writes_it_to_the_file(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, budget_usd=10.0, run_budget_usd=None, run_id="r", suite="s")
    usage = {"input_tokens": 3333, "output_tokens": 777, "cache_read_input_tokens": 4111}
    reservation = ledger.reserve(model="claude-sonnet-5-5", worst_case_usd=0.5, request_hash="h")
    cost = ledger.settle(reservation, usage)
    assert decimals(cost) <= COST_DECIMALS
    written = [json.loads(x) for x in path.read_text().splitlines()]
    settled = [e for e in written if "cost_usd" in e]
    assert [e["cost_usd"] for e in settled] == [cost]
    assert decimals(settled[0]["cost_usd"]) <= COST_DECIMALS


def test_a_cost_value_never_trips_the_pan_check() -> None:
    """The regression behind the change: a raw float's long digit run can pass Luhn. Only the
    cost field is scanned here (ledger IDs are random hex, a separate known false positive)."""
    rng = random.Random(7)  # noqa: S311 - test data
    for _ in range(3000):
        usage = {"input_tokens": rng.randrange(1, 90_000), "output_tokens": rng.randrange(1, 3000)}
        cost = cost_usd("claude-sonnet-5-5", usage)
        assert scan_text(f'{{"cost_usd": {cost!r}}}') == [], cost


def test_cassettes_written_with_a_cost_stay_scanner_clean(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    cost = cost_usd("claude-sonnet-5-5", {"input_tokens": 3333, "output_tokens": 777})
    store.put(cassette(cost=cost))
    assert scan_paths([tmp_path / "cassettes"]) == []
    assert (
        json.loads(next((tmp_path / "cassettes" / "s").glob("*.json")).read_text())["cost_usd"]
        == cost
    )


def test_every_committed_cassette_cost_is_at_fixed_precision() -> None:
    repo = Path(__file__).resolve().parents[3]
    files = sorted((repo / "cassettes" / "eval-answers").glob("*.json"))
    assert files
    for file in files:
        value = json.loads(file.read_text())["cost_usd"]
        assert value == round(value, COST_DECIMALS), file.name
