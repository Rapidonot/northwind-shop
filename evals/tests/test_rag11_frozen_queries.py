"""RAG-11 (row #9): the frozen-query list covers every red-team query, once."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_evals.cli import ITEMS, RED_TEAM, _frozen_queries
from northwind_evals.items import load_items

pytestmark = pytest.mark.req("RAG-11")

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _from_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)  # the CLI's paths are relative to the repo root


def red_team_queries() -> list[str]:
    lines = (REPO / RED_TEAM).read_text("utf-8").splitlines()
    return [json.loads(line)["query"] for line in lines]


def test_every_red_team_query_is_in_the_frozen_list() -> None:
    queries = red_team_queries()
    assert len(queries) == 28
    assert set(queries) <= set(_frozen_queries(load_items(ITEMS)))


def test_the_frozen_list_has_no_repeats_even_for_the_starter_set_repeats() -> None:
    frozen = _frozen_queries(load_items(ITEMS))
    assert len(frozen) == len(set(frozen))
    starter = {item.query for item in load_items(ITEMS)}
    repeats = [q for q in red_team_queries() if q in starter]
    assert repeats, "the corpus repeats starter queries (EV-094..EV-097)"
    assert all(frozen.count(q) == 1 for q in repeats)


def test_the_starter_queries_keep_their_place_ahead_of_the_red_team_ones() -> None:
    items = load_items(ITEMS)
    starter = list(dict.fromkeys(item.query for item in items))
    assert _frozen_queries(items)[: len(starter)] == starter
