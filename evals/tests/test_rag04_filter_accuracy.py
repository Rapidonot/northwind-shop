"""RAG-04's criterion (row #11): filter extraction is at least 95% correct on 100 labelled queries.

The labels (`evals/fixtures/rag04_queries.jsonl`) were frozen before any extractor existed
(`test_rag04_labels_frozen`). Scored with the guide's rule (`northwind_rag.query.filters_match`) on
the pipeline the assistant runs: the rules, and Haiku for the messages they mark `needs_model`.
Haiku's replies are recorded in the `query-understanding` suite (`make record
SUITE=query-understanding`), which also holds the plans for every eval item's first message, so
`make eval-gate` and `make eval-snapshot` replay them (`northwind_evals.query_plans`).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from northwind_assistant.understand import Understander
from northwind_evals.items import load_items
from northwind_rag.query import filters_match, rules_plan

pytestmark = [pytest.mark.req("RAG-04"), pytest.mark.llm_suite("query-understanding")]

REPO = Path(__file__).resolve().parents[2]
LABELS = REPO / "evals/fixtures/rag04_queries.jsonl"
ITEMS = REPO / "evals/fixtures/starter_set.jsonl"
REPORT = REPO / ".llm-proxy-log/rag04-filter-accuracy.json"
PASS_MARK = 95
FIELDS = ("availability", "colour", "price", "size")


def labelled() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines()]


def _field_ok(predicted: list[dict[str, Any]], gold: list[dict[str, Any]], name: str) -> bool:
    def only(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"filters": {name: p["filters"][name]}} for p in parts]

    return filters_match(only(predicted), only(gold))


def test_filter_extraction_is_at_least_95_of_100(llm_proxy: Any) -> None:
    understand = Understander(live=False)
    items = labelled()
    assert len(items) == 100
    correct, paths, per_field, split = 0, Counter[str](), Counter[str](), 0
    misses: list[str] = []
    model_ms: list[int] = []  # NFR-13 input: wall time of each Haiku plan, from this client
    for item in items:
        understood = asyncio.run(understand.plan(item["query"], item["region"], llm_proxy.base_url))
        predicted = understood.plan.label_parts()
        paths[understood.path] += 1
        if understood.path == "model":
            model_ms.append(understood.ms)
        if filters_match(predicted, item["parts"]):
            correct += 1
        else:
            misses.append(item["id"])
        per_field.update(name for name in FIELDS if _field_ok(predicted, item["parts"], name))
        split += len(predicted) == len(item["parts"])
    rules_only = sum(
        filters_match(rules_plan(i["query"], i["region"]).label_parts(), i["parts"]) for i in items
    )
    report = {
        "correct": correct,
        "of": len(items),
        "misses": misses,
        "paths": dict(sorted(paths.items())),
        "per_field": {name: per_field[name] for name in FIELDS},
        "split_correct": split,
        "rules_only_correct": rules_only,
        "mode": llm_proxy.mode,
        "model_ms": sorted(model_ms),
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert "error" not in paths, report  # a failed or missed call is never scored as a rules plan
    assert correct >= PASS_MARK, report


def test_every_eval_first_message_that_needs_the_model_has_a_plan(llm_proxy: Any) -> None:
    """Records (and replays) Haiku's plan for each eval item's first message the rules route, so the
    gate and the snapshot never call a model."""
    understand = Understander(live=False)
    routed = [i for i in load_items(ITEMS) if rules_plan(i.query, i.region).needs_model]
    paths = Counter(
        asyncio.run(understand.plan(i.query, i.region, llm_proxy.base_url)).path for i in routed
    )
    assert set(paths) <= {"model", "invalid"}, paths
