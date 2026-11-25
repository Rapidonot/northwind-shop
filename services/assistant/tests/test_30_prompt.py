"""Row #30 (SA-04, D8's remainder): what the system prompt says. Failing first. These pin the rules'
text; whether the model follows them is the recorded `assistant-intents` suite and W6."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import pytest

from northwind_assistant.output_check import hits
from northwind_assistant.prompt import system_prompt

REGIONS: tuple[Literal["US", "CA", "UK", "EU"], ...] = ("US", "CA", "UK", "EU")


def rule(start: str) -> str:
    [found] = [line for line in system_prompt("US").split("\n") if line.startswith(start)]
    return found


@pytest.mark.req("SA-04")
def test_the_prompt_asks_before_recommending() -> None:
    clarify = rule("- If the question could mean more than one product or option")
    assert "ask which one the shopper means" in clarify  # the old rule stays
    for words in (
        "size, budget or what it is for",
        "do not recommend yet",
        "one short question",
        "name no product",
        "no figures",
    ):
        assert words in clarify, words


@pytest.mark.req("SA-04")
def test_the_prompt_says_when_not_to_ask() -> None:
    clarify = rule("- If the question could mean more than one product or option")
    assert "no preference" in clarify
    assert "answer from the sources" in clarify


@pytest.mark.req("SA-04")
@pytest.mark.req("RAG-08")
def test_a_clarifying_question_is_not_an_offer_and_the_d8_rule_is_unchanged() -> None:
    d8 = rule("- Do not describe yourself, your tools or the website")
    assert "End when the question is answered." in d8
    assert "Never say what you will or could do with the shopper's reply" in d8
    assert "or list what you cannot do next" in d8  # EV-221
    assert "If you decline a request, do it in one sentence" in d8  # EV-297
    assert "except after a refused add, offer nothing else" in d8  # the cart rule's exception holds


@pytest.mark.req("RAG-08")
def test_the_prompt_counts_nothing_and_adds_no_policy_conclusion() -> None:
    prompt = system_prompt("US")
    assert "never state how many products Northwind has" in prompt  # EV-039
    assert "State a policy only as far as its source states it" in prompt  # EV-241, EV-242


@pytest.mark.req("SA-04")
@pytest.mark.parametrize("region", REGIONS)
def test_the_clarify_rule_holds_no_figure_and_the_prompt_stays_one_block(
    region: Literal["US", "CA", "UK", "EU"],
) -> None:
    prompt = system_prompt(region)
    assert prompt.endswith(f"The shopper is in the {region} region.")
    clarify = rule("- If the question could mean more than one product or option")
    assert not re.search(r"\d|[$£€]", clarify)
    assert "stock" not in hits(clarify) and "money" not in hits(clarify)


@pytest.mark.req("SA-04")
def test_the_labelled_prompts_are_not_in_the_system_prompt() -> None:
    """The rule is general: no SA-04 message, and no run of five words from one, is in it."""
    repo = Path(__file__).resolve().parents[3]
    words = re.findall(r"[a-z']+", system_prompt("US").lower())
    runs = {tuple(words[i : i + 5]) for i in range(len(words) - 4)}
    for line in (repo / "evals/fixtures/sa04_prompts.jsonl").read_text("utf-8").splitlines():
        asked = re.findall(r"[a-z']+", json.loads(line)["prompt"].lower())
        assert not runs & {tuple(asked[i : i + 5]) for i in range(len(asked) - 4)}
