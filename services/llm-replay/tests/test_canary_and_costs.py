"""Canary scanning and fixed-precision costs, both pulled forward into RAG-12.

Canaries come from the answer key's `internal_only[].canary` (hard rule 7): an internal document's
marker must never reach a cassette, a saved eval context or a log. Costs are rounded to fixed
precision so a long float digit run cannot pass the scanner's Luhn check and refuse a recording
already paid for. Everything here is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from northwind_llm_replay import cli
from northwind_llm_replay.cassette import Cassette, CassetteStore, DirtyCassetteError
from northwind_llm_replay.ledger import Ledger, new_id
from northwind_llm_replay.pricing import COST_DECIMALS, cost_usd, worst_case_usd
from northwind_llm_replay.proxy import LOG_HASH_CHARS
from northwind_llm_replay.scan import (
    ANSWER_KEY,
    LEGACY_LEDGER_VALUES,
    load_canaries,
    luhn_ok,
    scan_paths,
    scan_text,
    without_ledger_ids,
)
from northwind_llm_replay.scrub import request_hash

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


# --- generated IDs vs the PAN check (`make ci` failed on a ledger ID) ---------------------------

# 32 hex characters holding a Luhn-valid 14-digit run, the shape that failed on the shared ledger.
RUN = next(f"{n:014d}" for n in range(10**13, 10**13 + 100) if luhn_ok(f"{n:014d}"))
HEX_ID = f"abcdef{RUN}abcdefabcdef"
HEX_HASH = (HEX_ID * 2)[:64]
PAN = "4242424242424242"
PADDED_PAN = f"{PAN}abcdefabcdefabcd"  # a card number padded to the exact shape of an ID


def records_file(tmp_path: Path, record: dict[str, object], name: str = "ledger.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_the_fixture_id_really_trips_the_content_check() -> None:
    assert len(HEX_ID) == 32
    assert [f.kind for f in scan_text(HEX_ID)] == ["luhn-valid-pan"]


@pytest.mark.parametrize(
    "record",
    [
        {"id": LEGACY_LEDGER_VALUES["id"], "type": "settle", "cost_usd": 0.0001},
        {"request_hash": LEGACY_LEDGER_VALUES["request_hash"], "type": "reserve"},
    ],
)
def test_the_legacy_values_pass_in_the_ledger_file(
    tmp_path: Path, record: dict[str, object]
) -> None:
    ledger = records_file(tmp_path, record)
    assert scan_paths([ledger], ledger=ledger) == []


def test_the_legacy_values_really_need_the_exemption() -> None:
    assert all(scan_text(value) for value in LEGACY_LEDGER_VALUES.values())


def test_a_padded_card_number_never_passes_even_in_the_ledger_file(tmp_path: Path) -> None:
    ledger = records_file(tmp_path, {"id": PADDED_PAN})
    assert [f.kind for f in scan_paths([ledger], ledger=ledger)] == ["luhn-valid-pan"]


def test_a_legacy_value_gets_no_exemption_in_another_field(tmp_path: Path) -> None:
    ledger = records_file(tmp_path, {"note": LEGACY_LEDGER_VALUES["id"]})
    assert [f.kind for f in scan_paths([ledger], ledger=ledger)] == ["luhn-valid-pan"]


def test_new_ledger_ids_hold_no_digits_and_hashes_are_prefixes(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, budget_usd=1000.0, run_budget_usd=None, run_id="r", suite="s")
    reservation = ledger.reserve(
        model="claude-sonnet-5-5", worst_case_usd=0.01, request_hash="ab" * 32
    )
    ledger.settle(reservation, {"input_tokens": 10, "output_tokens": 5})
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert all(not any(c.isdigit() for c in r["id"]) for r in records)
    assert records[0]["id"] == records[1]["id"]
    assert records[0]["request_hash"] == "ab" * 6


def test_ids_stay_unique() -> None:
    assert len({new_id() for _ in range(2000)}) == 2000


@pytest.mark.parametrize(
    "record",
    [
        {"id": PAN},  # a card number is not the shape of an ID
        {"id": HEX_ID},  # right shape, but not one of the legacy values
        {"id": f"{HEX_ID}0"},
        {"id": HEX_ID.upper()},
        {"request_hash": HEX_ID},  # right shape for another field
        {"hash": HEX_HASH},  # not a ledger field
        {"note": HEX_ID},  # the same value outside an ID field
        {"usage": {"id": HEX_ID}},  # nested: not a top-level ID
        {"id": LEGACY_LEDGER_VALUES["id"], "note": PAN},  # no excuse for the rest of its line
        {"note": f"card {PAN} ok"},
    ],
)
def test_everything_else_in_a_ledger_record_stays_content(
    tmp_path: Path, record: dict[str, object]
) -> None:
    ledger = records_file(tmp_path, record)
    assert {f.kind for f in scan_paths([ledger], ledger=ledger)} == {"luhn-valid-pan"}


@pytest.mark.parametrize("name", ["contexts.jsonl", "starter_set.jsonl", "eval-answers.jsonl"])
@pytest.mark.parametrize("value", [HEX_ID, PADDED_PAN])
def test_only_the_ledger_file_gets_the_exemption(tmp_path: Path, name: str, value: str) -> None:
    ledger = records_file(tmp_path, {"id": "a" * 32})
    other = records_file(tmp_path, {"id": value}, name)
    assert [f.kind for f in scan_paths([other], ledger=ledger)] == ["luhn-valid-pan"]


def test_without_a_named_ledger_nothing_is_exempt(tmp_path: Path) -> None:
    path = records_file(tmp_path, {"id": HEX_ID})
    assert [f.kind for f in scan_paths([path])] == ["luhn-valid-pan"]


def test_a_card_number_in_a_long_hex_looking_string_is_still_found() -> None:
    assert [f.kind for f in scan_text(f"abcdef{PAN}abcdefabcdefabcdefabcdef")] == ["luhn-valid-pan"]


def test_without_ledger_ids_keeps_every_other_byte() -> None:
    legacy = LEGACY_LEDGER_VALUES["id"]
    document = json.dumps({"id": legacy, "note": "x", "cost": 0.1})
    assert without_ledger_ids(document) == json.dumps({"id": "", "note": "x", "cost": 0.1})
    assert without_ledger_ids("not json") == "not json"
    assert without_ledger_ids("[1, 2]") == "[1, 2]"


def test_a_real_ledger_never_fails_the_scan(tmp_path: Path) -> None:
    """Reserve and settle pairs written by the real ledger, through the real scan."""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, budget_usd=1000.0, run_budget_usd=None, run_id="r", suite="s")
    for n in range(400):
        digest = hashlib.sha256(str(n).encode()).hexdigest()
        reservation = ledger.reserve(
            model="claude-sonnet-5-5", worst_case_usd=0.01, request_hash=digest
        )
        ledger.settle(reservation, {"input_tokens": 10, "output_tokens": 5})
    assert scan_paths([path], ledger=path) == []


def request_with_a_luhn_run() -> tuple[dict[str, object], str]:
    """A real request whose real SHA-256 holds a digit run the PAN check flags."""
    for n in range(200_000):
        match: dict[str, object] = {"method": "POST", "path": "/v1/messages", "body": {"n": n}}
        digest = request_hash(match)
        if scan_text(digest):
            return match, digest
    raise AssertionError("no such digest found")


def test_a_cassette_whose_real_hash_holds_a_luhn_run_is_written_and_scans_clean(
    tmp_path: Path,
) -> None:
    match, digest = request_with_a_luhn_run()
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    assert store.put(replace(cassette(), hash=digest, match=match)).exists()
    assert scan_paths([tmp_path / "cassettes"]) == []


def test_a_cassette_hash_that_is_not_its_requests_digest_stays_content(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    forged = (PADDED_PAN * 2)[:64]
    with pytest.raises(DirtyCassetteError):
        store.put(replace(cassette(), hash=forged))


def test_a_cassette_with_a_real_hash_still_refuses_a_card_number_in_the_body(
    tmp_path: Path,
) -> None:
    match, digest = request_with_a_luhn_run()
    store = CassetteStore(tmp_path / "cassettes", "s", canaries=frozenset())
    with pytest.raises(DirtyCassetteError):
        store.put(replace(cassette(body=f"card {PAN}"), hash=digest, match=match))


def test_every_committed_cassette_hash_is_its_requests_digest() -> None:
    root = Path(__file__).resolve().parents[3] / "cassettes"
    paths = sorted(root.rglob("*.json"))
    assert paths
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["hash"] == request_hash(record["match"]) == path.stem


def test_the_proxy_log_hash_is_too_short_to_hold_a_flagged_run() -> None:
    assert LOG_HASH_CHARS < 13
