"""Secret/PAN scanner (FOUND-2; hard rules 1 and 4; user changes [8] and [9]).

Every key-like value here is fake and built at runtime, so this file itself holds no
scannable secret.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_common.cards import luhn_ok
from northwind_llm_replay.scan import KEY_FRAGMENT_LEN, scan_paths, scan_text

FAKE_KEY = "sk-ant-" + "api03-" + "Zq7" * 20  # fake, never a real key
OPAQUE_KEY = "nwfake" + "0123456789abcdefghijKLMNOP" * 2  # a key format no regex knows
TEST_PAN = "4111" + "1111" * 3  # Luhn-valid sandbox test card


def kinds(text: str, secret: str | None = None) -> set[str]:
    return {f.kind for f in scan_text(text, secret=secret)}


@pytest.mark.req("FOUND-2")
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f'{{"note": "{FAKE_KEY}"}}', "anthropic-api-key"),
        ("token sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2", "generic-sk-key"),
        ("Authorization: Bearer " + "abcDEF123456ghiJKL789", "bearer-token"),
        ("x-api-key: " + "whatever-value", "x-api-key-header"),
        ('{"x-api-key": "' + "whatever" + '"}', "x-api-key-header"),
    ],
)
def test_key_shapes_are_caught(text: str, kind: str) -> None:
    assert kind in kinds(text)


@pytest.mark.req("FOUND-2")
def test_header_name_alone_is_not_a_finding() -> None:
    assert kinds('{"header_names": ["accept", "x-api-key", "x-app"]}') == set()


@pytest.mark.req("FOUND-2")
def test_any_16_char_piece_of_the_real_key_is_caught_even_in_unknown_format() -> None:
    piece = OPAQUE_KEY[10 : 10 + KEY_FRAGMENT_LEN]
    assert "api-key-fragment" in kinds(f"log line ...{piece}... more", secret=OPAQUE_KEY)
    assert "api-key-fragment" in kinds(OPAQUE_KEY, secret=OPAQUE_KEY)
    shorter = OPAQUE_KEY[10 : 10 + KEY_FRAGMENT_LEN - 1]
    assert "api-key-fragment" not in kinds(f"log line ...{shorter}... more", secret=OPAQUE_KEY)


@pytest.mark.req("FOUND-2")
def test_luhn_valid_pan_is_caught_with_non_digits_on_both_sides() -> None:
    assert luhn_ok(TEST_PAN)
    assert "luhn-valid-pan" in kinds(TEST_PAN)
    assert "luhn-valid-pan" in kinds(f'card "{TEST_PAN}" here')
    assert "luhn-valid-pan" in kinds(f"pan={TEST_PAN};")
    assert "luhn-valid-pan" in kinds("4111 1111 1111 1111")
    assert "luhn-valid-pan" in kinds("4111-1111-1111-1111")


@pytest.mark.req("FOUND-2")
def test_digits_inside_a_longer_run_or_failing_luhn_are_not_pans() -> None:
    assert kinds(f"9{TEST_PAN}") == set()  # 17 digits: the 16-digit PAN is not delimited
    assert not luhn_ok(TEST_PAN[:-1] + "2")
    assert kinds(TEST_PAN[:-1] + "2") == set()
    assert kinds("order 1730678400123") == set()  # 13 digits, Luhn-invalid
    assert kinds("x" * 20 + "12345" + "y") == set()  # too short


def write_cassette(root: Path, name: str, recorded_at: object, body: str = "ok") -> Path:
    d = root / "cassettes" / "suite"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({"recorded_at": recorded_at, "body": body}), encoding="utf-8")
    return p


@pytest.mark.req("FOUND-2")
def test_cassette_recorded_at_must_be_iso8601(tmp_path: Path) -> None:
    write_cassette(tmp_path, "good.json", "2026-11-04T01:21:22Z")
    write_cassette(tmp_path, "bad.json", "4 Nov 2026")
    write_cassette(tmp_path, "missing.json", None)
    found = {(Path(f.path).name, f.kind) for f in scan_paths([tmp_path / "cassettes"])}
    assert found == {
        ("bad.json", "recorded_at-not-iso8601"),
        ("missing.json", "recorded_at-missing"),
    }


@pytest.mark.req("FOUND-2")
def test_ledger_and_proxy_log_are_scanned(tmp_path: Path) -> None:
    log = tmp_path / ".llm-proxy-log" / "spike.jsonl"
    log.parent.mkdir()
    log.write_text(json.dumps({"body": {"messages": [{"content": TEST_PAN}]}}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"note": OPAQUE_KEY[:20]}) + "\n")
    found = {
        (Path(f.path).name, f.kind) for f in scan_paths([log.parent, ledger], secret=OPAQUE_KEY)
    }
    assert ("spike.jsonl", "luhn-valid-pan") in found
    assert ("ledger.jsonl", "api-key-fragment") in found


@pytest.mark.req("FOUND-2")
def test_committed_cassettes_are_clean() -> None:
    repo = Path(__file__).resolve().parents[3]
    assert scan_paths([repo / "cassettes"]) == []


@pytest.mark.req("FOUND-2")
def test_findings_never_contain_the_secret() -> None:
    findings = scan_text(f"x {OPAQUE_KEY} {TEST_PAN}", secret=OPAQUE_KEY)
    assert findings
    for f in findings:
        assert OPAQUE_KEY[:KEY_FRAGMENT_LEN] not in str(f)
        assert TEST_PAN not in str(f)
