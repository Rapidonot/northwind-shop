"""SA-04 and SA-03's 70 messages rank their first search on frozen vectors (rows #30 and #22), so a
Linux replay of `assistant-intents` cannot miss on a near tie (decision 0012). So do the part texts
Haiku rewrote the routed ones into, read from W1's cassettes (the user's change 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from northwind_evals.cli import ITEMS, _frozen_queries
from northwind_evals.items import load_items
from northwind_evals.query_plans import part_texts
from northwind_rag.embedding import (
    FROZEN_DIR,
    FastEmbedEmbedder,
    frozen_key,
    frozen_path,
    load_frozen,
)

pytestmark = [pytest.mark.req("SA-03"), pytest.mark.req("SA-04")]

REPO = Path(__file__).resolve().parents[2]
SETS = (("sa04_prompts", "prompt", 20), ("sa03_conversations", "query", 50))


@pytest.fixture(autouse=True)
def _from_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)  # the CLI's paths are relative to the repo root


def messages() -> list[str]:
    out: list[str] = []
    for name, key, count in SETS:
        lines = (REPO / f"evals/fixtures/{name}.jsonl").read_text("utf-8").splitlines()
        assert len(lines) == count
        out += [json.loads(line)[key] for line in lines]
    return out


def test_every_labelled_message_is_in_the_frozen_list_once() -> None:
    frozen = _frozen_queries(load_items(ITEMS))
    assert len(set(messages())) == 70
    assert all(frozen.count(message) == 1 for message in messages())


def recorded_part_texts() -> list[str]:
    """The texts Haiku rewrote the routed messages into, replayed from W1's cassettes."""
    asked = [
        (json.loads(line)[key], json.loads(line)["region"])
        for name, key, _ in SETS
        for line in (REPO / f"evals/fixtures/{name}.jsonl").read_text("utf-8").splitlines()
    ]
    return part_texts(asked, suite="assistant-intents")


def test_the_labelled_messages_then_their_part_texts_come_after_every_earlier_query() -> None:
    """Appended, so the list the earlier vectors were vouched against keeps its order."""
    frozen = _frozen_queries(load_items(ITEMS))
    parts = recorded_part_texts()
    assert len(parts) == 5 and not set(parts) & set(messages())
    assert frozen[-75:] == [*messages(), *parts]


def test_every_recorded_part_text_has_a_committed_vector() -> None:
    embedder = FastEmbedEmbedder()
    frozen = load_frozen(frozen_path(embedder.id, FROZEN_DIR), embedder.dim)
    assert [t for t in recorded_part_texts() if frozen_key(t) not in frozen] == []


def test_every_labelled_message_has_a_committed_vector() -> None:
    embedder = FastEmbedEmbedder()
    frozen = load_frozen(frozen_path(embedder.id, FROZEN_DIR), embedder.dim)
    assert [m for m in messages() if frozen_key(m) not in frozen] == []
