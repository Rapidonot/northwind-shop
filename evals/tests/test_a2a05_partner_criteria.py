"""A2A-05 prep (row #15): criteria-level tests added beside `test_a2a05_partner.py`.

They load that module by path, as `test_cli.py` loads `promote_rig.py`, and test the frozen script
and the nine recorded fixtures against the plan's criteria (C1-C7, checks 1 and 3-8, change 2)
without a proxy, a database or an embedder. Nothing here edits a fixture, script or cassette.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from northwind_assistant import output_check

pytestmark = pytest.mark.req("A2A-05")

_PATH = Path(__file__).with_name("test_a2a05_partner.py")
_spec = importlib.util.spec_from_file_location("a2a05_partner_under_test", _PATH)
assert _spec is not None and _spec.loader is not None
partner: ModuleType = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = partner  # the module defines dataclasses, which look it up here
_spec.loader.exec_module(partner)

NAMES: list[str] = partner.NAMES
USES = {
    "product_question": "answer",
    "out_of_scope": "never_served",
    "scoped_order": "narrative_only",
}
CURRENT = "assistant:v1:claude-sonnet-5-5:prompt-aaaaaaaa:check-bbbbbbbb"


def fixture_of(name: str) -> dict[str, Any]:
    fixture: dict[str, Any] = partner.partner_fixture(name)
    return fixture


def test_script_shape_and_per_kind_counts() -> None:
    """C1: 7 product questions, one out-of-scope and one scoped-order turn, in that order."""
    turns = partner.TURNS
    kinds = Counter(t["kind"] for t in turns)
    assert kinds == {"product_question": 7, "out_of_scope": 1, "scoped_order": 1}
    assert [t["kind"] for t in turns[:7]] == ["product_question"] * 7
    assert [t["kind"] for t in turns[7:]] == ["out_of_scope", "scoped_order"]
    assert set(partner.SCRIPT_DATA) >= {
        "script",
        "version",
        "decision",
        "frozen",
        "region",
        "turns",
    }
    for turn in turns:
        assert set(turn) == {"name", "kind", "a2a05_use", "product_id", "message"}, turn["name"]
        assert turn["a2a05_use"] == USES[turn["kind"]]
    assert len({t["message"] for t in turns}) == len(turns)


def test_the_script_is_frozen_by_its_exact_bytes() -> None:
    """C2: the digest file is the sha256 of the script's raw bytes (whitespace counts)."""
    digest = hashlib.sha256(partner.SCRIPT.read_bytes()).hexdigest()
    assert partner.DIGEST.read_text(encoding="utf-8").split() == [digest, "script.json"]


def test_every_fixture_is_identified_by_name_message_and_digests() -> None:
    """C2/C3/C4: each fixture's own name, kind, use, message and both digests match its turn."""
    digest = partner.script_sha256()
    for turn in partner.TURNS:
        fixture = fixture_of(turn["name"])
        assert partner.check_identity(fixture, turn, digest) == []
        assert fixture["name"] == turn["name"] == (partner.ANSWERS / f"{turn['name']}.json").stem
        assert fixture["message_sha256"] == hashlib.sha256(turn["message"].encode()).hexdigest()
        assert fixture["provisional"] is True and fixture["suite"] == partner.SUITE


def test_a_fixture_whose_name_does_not_match_its_file_is_refused(tmp_path: Path) -> None:
    """C4: a copy of one fixture saved under another turn's file name is not served."""
    source = fixture_of(NAMES[1])
    (tmp_path / f"{NAMES[2]}.json").write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError):
        partner.partner_fixture(NAMES[2], answers=tmp_path)
    # the identity check also refuses it, so a hand-copied file cannot pass check 1
    copied = {**source, "name": NAMES[2]}
    assert partner.check_identity(copied, partner.turn_named(NAMES[2]), partner.script_sha256())


@pytest.mark.parametrize("name", NAMES)
def test_recorded_disclosure_matches_what_the_cassettes_say(name: str) -> None:
    """Check 8: `disclosure` is derived from the cassettes and the shown text, not asserted by hand,
    and no fixture has a disclosure the shopper never saw."""
    fixture = fixture_of(name)
    text = partner.model_text(name)
    assert text.strip(), f"no cassette text for {name}"
    in_model = bool(partner.DISCLOSURE.search(text))
    in_shown = bool(partner.DISCLOSURE.search(fixture["answer"]["shown"]))
    assert fixture["disclosure"] == {"in_model_text": in_model, "in_shown": in_shown}
    assert not (in_model and not in_shown)
    if name == NAMES[0]:
        assert in_shown


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_was_answered_by_one_assistant_model_within_the_turn_cap(name: str) -> None:
    """C5: recorded calls are the assistant model's, within the cap, matching the cassettes."""
    from northwind_assistant.turn import MAX_TURNS
    from northwind_common.llm import ASSISTANT_MODEL

    fixture = fixture_of(name)
    calls = fixture["upstream_calls"]
    assert 1 <= len(calls) <= MAX_TURNS
    assert {c["model"] for c in calls} == {ASSISTANT_MODEL}
    assert len(partner.recorded_calls(name)) == len(calls)
    assert calls[-1]["stop_reason"] == "end_turn"
    assert fixture["answer"]["outcome"] in ("answered", "insufficient_evidence")
    assert (
        fixture["answer"]["resets"] == 0 or fixture["answer"]["shown"] != fixture["answer"]["sent"]
    )


@pytest.mark.parametrize("name", NAMES)
def test_every_fixture_names_what_it_may_be_used_for(name: str) -> None:
    """A2A-05 note: `never_served` and `narrative_only` fixtures carry a note that says so."""
    fixture = fixture_of(name)
    use = fixture["a2a05_use"]
    assert use == USES[fixture["kind"]]
    note = fixture.get("note")
    assert isinstance(note, str) and note.strip()
    if use == "never_served":
        assert "never load" in note and "scope check" in note
    elif use == "narrative_only":
        assert "cart/order tools" in note
    else:
        assert "assistant_id" in note


def test_the_unserved_and_narrative_turns_are_the_last_two() -> None:
    assert [fixture_of(n)["a2a05_use"] for n in NAMES[-2:]] == ["never_served", "narrative_only"]
    assert Counter(fixture_of(n)["a2a05_use"] for n in NAMES) == {
        "answer": 7,
        "never_served": 1,
        "narrative_only": 1,
    }


# Row #18 W11 (the user's stop-5 yes, 2026-11-27): the one answer that may carry no citation.
# Partner turns have no order tools (option B), so the scoped-order turn can answer with this
# capability refusal alone. An exact-text allowlist, not a claim heuristic: no existing rule
# detects product claims, so any other uncited answer still fails check 3.
UNCITED_REFUSALS = frozenset({"Orders can't be placed in chat."})


def citation_problems(fixture: dict[str, Any]) -> list[str]:
    """Check 3 (hard rule 5): the citations list is the markers of `shown`, in order of first
    appearance, and each is a source the turn retrieved. It may be empty only when `shown` is
    exactly an allowlisted capability refusal and the output check flags nothing in it."""
    shown = fixture["answer"]["shown"]
    order: list[str] = []
    for group in partner.CITATION.findall(shown):
        for part in (p.strip() for p in group.split(",")):
            if part not in order:
                order.append(part)
    cited = fixture["answer"]["citations"]
    problems: list[str] = []
    if not cited and not (shown in UNCITED_REFUSALS and output_check.hits(shown) == {}):
        problems.append("no citation, and not an allowlisted refusal")
    if [c for c in order if c in cited] != cited:
        problems.append("citations are not the markers of shown, in order")
    problems += partner.check_citations(fixture)
    if not set(cited) <= {c["source_id"] for c in partner.chunks_in(fixture)}:
        problems.append("a citation is not a source of this turn")
    return problems


@pytest.mark.parametrize("name", NAMES)
def test_citations_are_marked_in_shown_in_order_and_come_from_this_turn(name: str) -> None:
    """Check 3 (hard rule 5), on every recorded fixture."""
    assert citation_problems(fixture_of(name)) == [], name


def test_the_uncited_scoped_order_answer_is_exactly_the_allowlisted_refusal() -> None:
    """Row #18 W11 pin (a): order-01's answer is the refusal, word for word, and passes check 3."""
    fixture = fixture_of("order-01-breakwater")
    assert fixture["answer"]["shown"] == "Orders can't be placed in chat."
    assert fixture["answer"]["citations"] == []
    assert output_check.hits(fixture["answer"]["shown"]) == {}
    assert citation_problems(fixture) == []
    assert UNCITED_REFUSALS == {"Orders can't be placed in chat."}


@pytest.mark.parametrize(
    "shown",
    [
        "The Corvane Breakwater Shell is fully waterproof, with a membrane rated to 15,000 mm.",
        "Orders can't be placed in chat. The Breakwater Shell runs true to size.",
        "Orders can't be placed in chat. ",
        "orders can't be placed in chat.",
        "I can't place orders in chat.",
    ],
)
def test_an_uncited_answer_that_is_not_the_exact_refusal_fails_check_3(shown: str) -> None:
    """Row #18 W11 mutant (b): an uncited product claim, or any text other than the exact refusal,
    still fails check 3."""
    for name in ("order-01-breakwater", "pq-01-breakwater-waterproofing"):
        fixture = json.loads(json.dumps(fixture_of(name)))
        fixture["answer"].update(shown=shown, sent=shown, citations=[])
        assert "no citation, and not an allowlisted refusal" in citation_problems(fixture), name


def test_the_stale_skip_boundaries() -> None:
    """Change 2: an id that differs only in the check part, or only in the model, still skips; the
    same id never does; the skip reason is exactly STALE."""
    parts = CURRENT.split(":")
    other_check = ":".join([*parts[:4], "check-cccccccc"])
    other_model = ":".join([parts[0], parts[1], "claude-haiku-5-5", *parts[3:]])
    other_prompt = ":".join([*parts[:3], "prompt-dddddddd", parts[4]])
    for stale in (other_check, other_model, other_prompt):
        with pytest.raises(pytest.skip.Exception) as raised:
            partner.skip_if_stale({"name": "x", "assistant_id": stale}, CURRENT)
        assert raised.value.msg == partner.STALE
    partner.skip_if_stale({"name": "x", "assistant_id": CURRENT}, CURRENT)
    assert partner.STALE == (
        "stale: assistant changed since the provisional recording; RECORD-ALL (#18) re-records"
    )
    with pytest.raises(pytest.fail.Exception):
        partner.skip_if_stale({"name": "x"}, CURRENT)  # missing id fails, never skips


def test_every_recorded_assistant_id_is_well_formed_and_shared() -> None:
    """Change 2: all nine were recorded by one assistant, so they go stale together."""
    ids = {fixture_of(n)["assistant_id"] for n in NAMES}
    assert len(ids) == 1
    assert re.fullmatch(r"assistant:v1:[^:]+:prompt-[0-9a-f]{8}:check-[0-9a-f]{8}", ids.pop())


@pytest.mark.parametrize("name", NAMES)
def test_checks_1_and_3_to_8_run_whatever_the_assistant_version(name: str) -> None:
    """Change 2: a fixture with a stale assistant_id still gets every always-on check, and none
    of them skips."""
    fixture = {
        **fixture_of(name),
        "assistant_id": "assistant:v1:old:prompt-00000000:check-00000000",
    }
    path = partner.ANSWERS / f"{name}.json"
    problems = partner.all_problems(fixture, path, partner.guard_inputs(), partner.model_text(name))
    assert problems == []


def test_no_recorded_answer_claims_a_price_stock_or_completed_action() -> None:
    """Checks 6 and 7 over all nine fixtures, also on `sent` (hard rules 2 and 8)."""
    for name in NAMES:
        fixture = fixture_of(name)
        assert partner.check_money(fixture) == [], name
        assert partner.check_done_claims(fixture) == [], name
