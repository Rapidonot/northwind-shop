"""RAG-04 (row #11, decision 0022), hard rule 1: card data never reaches the Haiku request and never
rides in a query-plan token.

Written from decision 0022 section 2 ("a message the first search would refuse never goes to Haiku:
empty, NUL or card data") and section 3 (a part is never card-shaped), not from the code. All card
numbers are synthetic test numbers. No cassette, no LLM window: Haiku is a recording fake, the
assistant's model is `ScriptedLLM`.

A test named `..._on_its_own` calls a public surface without the gate that normally runs before it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from northwind_assistant.caller import Caller
from northwind_assistant.catalog_client import CatalogEndpoint
from northwind_assistant.rag_client import RagEndpoint
from northwind_assistant.turn import TurnError, TurnEvent, stream_answer
from northwind_assistant.understand import (
    Understander,
    mint_planned,
    plan_claim,
    query_ok,
)
from northwind_common.cards import contains_card_data
from northwind_common.llm import ROUTINE_MODEL, agent_runtime
from northwind_common.mcp_token import RAG_SERVER, verify_for
from northwind_llm_replay.scripted import ScriptedLLM
from northwind_mcp_catalog import testing as catalog_testing
from northwind_mcp_rag.testing import KEY, serve_retriever
from northwind_rag.query import Filters, Part, QueryPlan, rules_plan
from northwind_rag.retrieval import RetrievedChunk

pytestmark = pytest.mark.req("RAG-04")

US = Caller(region="US")
SIGNING_KEY = b"k" * 40
UNUSED = "http://unused.invalid"

# Synthetic test numbers (Visa, Mastercard, Amex test ranges), in the forms a shopper might type.
PAN_FORMS = {
    "plain": "4111111111111111",
    "spaced": "4111 1111 1111 1111",
    "dashed": "4111-1111-1111-1111",
    "dotted": "4111.1111.1111.1111",
    "slashed": "4111/1111/1111/1111",
    "newlines": "4111\n1111\n1111\n1111",
    "fullwidth": "４１１１ １１１１ １１１１ １１１１",
    "mastercard": "5555 5555 5555 4444",
    "amex": "3782 822463 10005",
}
CVV_FORMS = {
    "named": "my cvv is 737",
    "after": "737 is the security code",
    "spaced": "cvc 7 3 7",
}
CARD_FORMS = {**PAN_FORMS, **CVV_FORMS}

# Messages that the rules send to Haiku when nothing else stops them (multi-part, follow-up).
CARRIERS = {
    "multi_part": "A black fleece in M and a queen duvet under $200. Pay with {card}",
    "multi_part_leading": "{card} is my card. A black fleece and a queen duvet?",
    "follow_up": "in blue? {card}",
    "follow_up_what_about": "what about the navy one, paid by {card}",
}


def carriers() -> list[tuple[str, str]]:
    return [
        (f"{kind}-{form}", template.format(card=card))
        for kind, template in CARRIERS.items()
        for form, card in CARD_FORMS.items()
    ]


class Recorder:
    """A Haiku client that records every request body and answers with a fixed reply."""

    def __init__(self, reply: str = '{"parts": []}') -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **body: Any) -> Any:
        self.requests.append(body)
        return SimpleNamespace(
            stop_reason="end_turn",
            model=ROUTINE_MODEL,
            usage=None,
            content=[SimpleNamespace(type="text", text=self.reply)],
        )


def plan_of(message: str, recorder: Recorder, region: str = "US") -> Any:
    understand = Understander(live=False, client_for=lambda _: recorder)
    return asyncio.run(understand.plan(message, region, UNUSED))


def part(text: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "text": text,
        "colours": [],
        "sizes": [],
        "price_kind": "none",
        "amount": "",
        "amount_to": "",
        "currency": "none",
        "in_stock_only": False,
    }
    base.update(over)
    return base


# ---- the gate: query_ok ---------------------------------------------------------------------


@pytest.mark.parametrize("form", sorted(CARD_FORMS))
def test_query_ok_refuses_every_card_form(form: str) -> None:
    card = CARD_FORMS[form]
    assert contains_card_data(card), "the fixture itself must be card data"
    assert not query_ok(card)
    assert not query_ok(f"Do you have this in blue? {card}")


@pytest.mark.parametrize("message", ["", "   ", "\n\t", "a\x00b", None, 7, ["x"]])
def test_query_ok_refuses_what_the_first_search_refuses(message: Any) -> None:
    assert not query_ok(message)


@pytest.mark.parametrize(
    "message",
    [
        "A fleece in 10, 12 or 14 and a duvet with 600 fill power",
        "Is P0011 or P0012 better for rain?",
        "Pants under $50 in black",
    ],
)
def test_query_ok_does_not_refuse_ordinary_numbers(message: str) -> None:
    assert query_ok(message)


# ---- Haiku's request -----------------------------------------------------------------------------


def test_the_carriers_really_are_routed_to_haiku_by_the_rules() -> None:
    """The precondition that gives the next tests their teeth: without a guard these go to Haiku."""
    plain = [m for _, m in carriers() if not contains_card_data(m)]
    assert plain == [], "every carrier must hold card data"
    unrouted = [
        name
        for name, message in carriers()
        if name.startswith("multi_part") and not rules_plan(message, "US").needs_model
    ]
    assert unrouted == [], "every multi-part carrier must be routed to the model by the rules"


def test_a_message_the_gate_passes_reaches_haiku_with_no_card_data() -> None:
    recorder = Recorder(json.dumps({"parts": [part("black fleece"), part("queen duvet")]}))
    message = "A black fleece in M and a queen duvet under $200, 600 fill power"
    assert query_ok(message) and rules_plan(message, "US").needs_model
    plan_of(message, recorder)
    [request] = recorder.requests
    for text in _strings(request):
        assert not contains_card_data(text)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def test_the_planner_on_its_own_never_sends_card_text_to_haiku() -> None:
    """`Understander.plan` called directly, without `query_ok` before it (the eval planner does
    this). Decision 0022 section 2: card data never goes to Haiku. Every offender is listed."""
    offenders = []
    for name, message in carriers():
        recorder = Recorder()
        plan_of(message, recorder)
        sent = [s for request in recorder.requests for s in _strings(request)]
        if any(contains_card_data(s) for s in sent):
            offenders.append(name)
    assert offenders == []


# ---- the turn: card text stops before any model --------------------------------------------------


class Spy:
    def __init__(self) -> None:
        self.plain: list[str] = []
        self.planned: list[Any] = []

    def search(self, query: str, caller: Caller) -> list[RetrievedChunk]:
        self.plain.append(query)
        return []

    def search_planned(self, parts: Any, caller: Caller) -> list[RetrievedChunk]:
        self.planned.append(list(parts))
        return []


def ask(llm: ScriptedLLM, spy: Spy, message: str, root: Path) -> list[TurnEvent]:
    runtime = agent_runtime(base_url=llm.url, root=root)
    understand = Understander(live=False)

    async def collect(rag: RagEndpoint, catalog: CatalogEndpoint) -> list[TurnEvent]:
        return [e async for e in stream_answer(message, US, rag, runtime, catalog, understand)]

    with (
        serve_retriever(spy, Caller) as (url, _),
        catalog_testing.running(catalog_testing.server(key=KEY)) as catalog_url,
    ):
        return asyncio.run(collect(RagEndpoint(url, KEY), CatalogEndpoint(catalog_url, KEY)))


@pytest.mark.parametrize(
    "message",
    [
        f"A black fleece and a queen duvet? Pay with {PAN_FORMS['dotted']}",
        f"in blue? {PAN_FORMS['fullwidth']}",
        f"A black fleece in M and a queen duvet under $200, {CVV_FORMS['named']}",
        f"what about the navy one {PAN_FORMS['newlines']}",
    ],
)
def test_a_turn_with_card_text_makes_no_model_call_and_no_search(
    message: str, tmp_path: Path
) -> None:
    spy = Spy()
    with ScriptedLLM() as llm:
        events = ask(llm, spy, message, tmp_path)
    assert llm.requests == []  # neither Haiku nor the assistant's model saw it
    assert spy.plain == [] and spy.planned == []
    assert events == [TurnError("internal")]


# ---- a hostile reply: card text in a part -----------------------------------------------------

MULTI = "A black fleece in M and a queen duvet under $200"


@pytest.mark.parametrize("form", sorted(CARD_FORMS))
def test_a_reply_with_card_text_in_a_part_is_refused(form: str) -> None:
    """Whichever form, a part carrying card data refuses the whole reply: the rules plan stands."""
    card = CARD_FORMS[form]
    reply = json.dumps({"parts": [part("black fleece"), part(f"queen duvet, card {card}")]})
    understood = plan_of(MULTI, Recorder(reply))
    assert understood.path == "invalid"
    assert understood.plan == rules_plan(MULTI, "US")
    assert not any(contains_card_data(p.text) for p in understood.plan.parts)


def _qf_texts(token: str) -> list[str]:
    payload = token.split(".")[1]
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return [p["q"] for p in body.get("qf", {}).get("parts", []) if "q" in p]


@pytest.mark.parametrize("form", sorted(CARD_FORMS))
def test_no_minted_token_carries_card_text_from_a_hostile_reply(form: str) -> None:
    """A hostile reply -> the planner -> the claim -> the token: card text stops on the way."""
    card = CARD_FORMS[form]
    reply = json.dumps({"parts": [part("black fleece"), part(f"queen duvet {card}")]})
    understood = plan_of(MULTI, Recorder(reply))
    claim = asyncio.run(plan_claim(understood.plan, MULTI, US, None))
    if claim is not None:
        token = mint_planned(SIGNING_KEY, US, str(uuid.uuid4()), claim)
        if token is not None:
            assert not any(contains_card_data(t) for t in _qf_texts(token))
            assert not any(card in t for t in _qf_texts(token))


def test_the_clean_reply_is_the_positive_control() -> None:
    """The same chain with clean parts mints a token whose texts are the parts: the test above is
    not passing only because nothing is ever minted."""
    reply = json.dumps({"parts": [part("black fleece"), part("queen duvet")]})
    understood = plan_of(MULTI, Recorder(reply))
    assert understood.path == "model"
    claim = asyncio.run(plan_claim(understood.plan, MULTI, US, None))
    assert claim is not None
    turn = str(uuid.uuid4())
    token = mint_planned(SIGNING_KEY, US, turn, claim)
    assert token is not None
    assert _qf_texts(token) == ["black fleece", "queen duvet"]
    claims = verify_for(token, key=SIGNING_KEY, server=RAG_SERVER)
    assert claims.turn == turn and claims.aud == "shopper" and claims.region == "US"


def test_mint_planned_on_its_own_never_mints_card_text() -> None:
    """`plan_claim` and `mint_planned` called directly with a plan whose part holds card text
    (decision 0022 section 3: no part is card-shaped): no token carries it. Offenders are listed."""
    offenders = []
    for form, card in CARD_FORMS.items():
        plan = QueryPlan(parts=(Part(f"black fleece {card}", Filters(colour=("Black",))),))
        claim = asyncio.run(plan_claim(plan, "black fleece", US, None))
        token = None if claim is None else mint_planned(SIGNING_KEY, US, str(uuid.uuid4()), claim)
        if token is not None and any(contains_card_data(t) for t in _qf_texts(token)):
            offenders.append(form)
    assert offenders == []
