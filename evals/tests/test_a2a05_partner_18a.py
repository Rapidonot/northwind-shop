"""Row 18a, part 6 (A2A-05; option B of `docs/reviews/2026-11-25-a2a05-surface-memo.md`, the user's
answers of 2026-11-25; decision 0026). The recorder gives a partner turn an anonymous shopper's
surface: mcp-rag, mcp-catalog and the cart tools, with a fresh random cart session per turn, and no
query understanding, orders or policy server. P3-1: an order turn is one whose every call was
offered northwind-catalog's exact cart tools and that called one of them. The order narrative goes
beside its artifact only when it plainly agrees with it, else the artifact alone. Failing first.
The 9 fixtures and cassettes are not touched: the suite stays stale until #18 records it."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from northwind_common.catalog_tool import CART_TOOL_NAMES
from northwind_common.mcp_token import SESSION_ID
from northwind_llm_replay.scripted import ScriptedLLM

pytestmark = pytest.mark.req("A2A-05")

_PATH = Path(__file__).with_name("test_a2a05_partner.py")
_spec = importlib.util.spec_from_file_location("a2a05_partner_18a_under_test", _PATH)
assert _spec is not None and _spec.loader is not None
partner: ModuleType = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = partner
_spec.loader.exec_module(partner)

ORDER_TURN = 8  # order-01-breakwater: narrative_only, product P0003
RAG = "mcp__northwind-rag__retrieve_context"
ADD = "mcp__northwind-catalog__add_to_cart"
CART = tuple(sorted(CART_TOOL_NAMES))
VARIANT = "P0003-SLT-REL-M"
AGREEING = (
    "I'm an AI assistant. I added one Corvane Breakwater Shell in Slate, size M, to your cart "
    f"[{VARIANT}]."
)
ARTIFACT = {"order": {"lines": [{"variant_id": VARIANT, "quantity": 1}]}}


def order_setup(
    tmp_path: Path,
    *,
    offered: tuple[str, ...] = (RAG, *CART),
    called: tuple[str, ...] = (ADD,),
    shown: str = AGREEING,
) -> tuple[str, Path, Path]:
    """The order turn's fixture, current, with one cassette whose request offered `offered` and
    whose response called `called`."""
    fixture = partner.good_fixture()
    partner._as_turn(ORDER_TURN)(fixture)
    fixture["assistant_id"] = partner.CURRENT
    fixture["answer"] = {**fixture["answer"], "shown": shown, "sent": shown}
    answers, cassettes = tmp_path / "answers", tmp_path / "cassettes"
    answers.mkdir()
    cassettes.mkdir()
    name = str(fixture["name"])
    (answers / f"{name}.json").write_text(partner.dump(fixture), encoding="utf-8")
    starts = "".join(
        "data: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": n,
                "content_block": {"type": "tool_use", "id": f"t{n}", "name": tool, "input": {}},
            }
        )
        + "\n"
        for n, tool in enumerate(called)
    )
    body = starts + 'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n'
    request = {"tools": [{"name": tool} for tool in offered], "messages": []}
    record = {
        "hash": "a" * 64,
        "test_id": partner.recorder_test_id(name),
        "kind": "sse",
        "body": body,
        "model": "m",
        "seq": 0,
        "match": {"body": request},
    }
    (cassettes / f"{'a' * 64}.json").write_text(json.dumps(record), encoding="utf-8")
    return name, answers, cassettes


def served(name: str, answers: Path, cassettes: Path, artifact: Any = ARTIFACT) -> Any:
    return partner.served_fixture(
        name, partner.CURRENT, order_artifact=artifact, answers=answers, cassettes=cassettes
    )


# ---- P3-1: exact cart tools, offered and used ---------------------------------------------------


def test_an_agreeing_narrative_from_a_cart_turn_is_served_with_its_artifact(tmp_path: Path) -> None:
    text, artifact = served(*order_setup(tmp_path))
    assert text == AGREEING and artifact is ARTIFACT


@pytest.mark.parametrize(
    ("offered", "called"),
    [
        ((RAG, "mcp__northwind-orders__get_order"), ("mcp__northwind-orders__get_order",)),
        ((RAG, "mcp__northwind-cart__add_to_cart"), ("mcp__northwind-cart__add_to_cart",)),
        ((RAG, "mcp__northwind-catalog__get_order_history"), ()),
        ((RAG, ADD), (ADD,)),  # one cart tool offered, not northwind-catalog's whole set
        ((RAG, *CART), ()),  # offered, never used
        ((RAG, *CART), ("mcp__northwind-catalog__get_price",)),  # used, but no cart tool
    ],
)
def test_a_turn_without_the_exact_cart_tools_offered_and_used_is_no_order_turn(
    tmp_path: Path, offered: tuple[str, ...], called: tuple[str, ...]
) -> None:
    name, answers, cassettes = order_setup(tmp_path, offered=offered, called=called)
    assert not partner.offered_order_tools(name, cassettes)
    with pytest.raises(partner.NotServable):
        served(name, answers, cassettes)


@pytest.mark.parametrize("tool", CART)
def test_any_cart_tool_used_counts(tmp_path: Path, tool: str) -> None:
    name, _answers, cassettes = order_setup(tmp_path, called=(tool,))
    assert partner.offered_order_tools(name, cassettes)


# ---- the artifact alone when the text does not plainly agree ------------------------------------


@pytest.mark.parametrize(
    "shown",
    [
        "I can't place orders in chat, so I can't order the Corvane Breakwater Shell for you here. "
        f"It comes in Slate, size M, relaxed fit [{VARIANT}].",
        f"I'm unable to add it to your cart [{VARIANT}].",
        # another variant
        "I added one Corvane Breakwater Shell in Slate, size L [P0003-SLT-REL-L].",
        f"I added the shell [{VARIANT}] and the Brindlemoor Cairn [P0011-SAG-REG-M].",
        "I added one Corvane Breakwater Shell in Slate, size M.",  # cites nothing
    ],
)
def test_a_narrative_that_does_not_plainly_agree_leaves_the_artifact_alone(
    tmp_path: Path, shown: str
) -> None:
    text, artifact = served(*order_setup(tmp_path, shown=shown))
    assert text is None and artifact is ARTIFACT


@pytest.mark.parametrize(
    "artifact",
    [{"order": "x"}, {"order": {"lines": [{"variant_id": "P0011-SAG-REG-M"}]}}],
)
def test_an_artifact_that_names_no_variant_of_the_turns_product_cannot_be_agreed_with(
    tmp_path: Path, artifact: Any
) -> None:
    text, served_artifact = served(*order_setup(tmp_path), artifact=artifact)
    assert text is None and served_artifact is artifact


def test_an_artifact_for_another_product_is_not_agreed_with_even_when_cited(
    tmp_path: Path,
) -> None:
    other = {"order": {"lines": [{"variant_id": "P0011-SAG-REG-M"}]}}
    shown = "I added the Breakwater Shell [P0003] as the Cairn jacket [P0011-SAG-REG-M]."
    text, artifact = served(*order_setup(tmp_path, shown=shown), artifact=other)
    assert text is None and artifact is other


# ---- the recorder's surface -------------------------------------------------------------------


def scripted_turn(tmp_path: Path) -> list[dict[str, Any]]:
    """`run_partner_turn` for the order turn against a scripted model: the requests it made."""
    turn = partner.TURNS[ORDER_TURN]
    with ScriptedLLM() as llm:
        llm.reply_stream("I'm an AI assistant. Here is the shell [P0003].")
        partner.run_partner_turn(turn, [], {}, None, None, llm.url, tmp_path / "rt")
        return [r for r in llm.requests if r["path"].endswith("/v1/messages")]


def test_the_recorder_offers_an_anonymous_shoppers_surface_exactly(tmp_path: Path) -> None:
    requests = scripted_turn(tmp_path)
    assert requests
    offered = {str(tool["name"]) for tool in requests[0]["body"]["tools"]}
    assert CART_TOOL_NAMES <= offered
    assert RAG in offered
    servers = {name.split("__")[1] for name in offered if name.startswith("mcp__")}
    assert servers == {"northwind-rag", "northwind-catalog"}  # no orders, no policy


def test_each_partner_turn_gets_its_own_cart_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import northwind_assistant.turn as turn_module

    seen: list[tuple[Any, ...]] = []

    async def capture(*args: Any, **kwargs: Any) -> Any:
        seen.append((args, kwargs))
        return
        yield  # an async generator that yields nothing

    monkeypatch.setattr(turn_module, "stream_answer", capture)
    turn = partner.TURNS[ORDER_TURN]
    for n in range(2):
        partner.run_partner_turn(turn, [], {}, None, None, "http://127.0.0.1:9", tmp_path / f"{n}")
    from northwind_assistant.catalog_client import CatalogEndpoint

    sessions = []
    for args, kwargs in seen:
        assert kwargs == {}  # no understander, no orders, no policy endpoint
        message, caller, _rag, _runtime, catalog = args
        assert message == turn["message"]
        assert isinstance(catalog, CatalogEndpoint)
        assert caller.audience == "shopper" and caller.region == "US"
        assert SESSION_ID.fullmatch(caller.session)
        sessions.append(caller.session)
    assert len(set(sessions)) == 2


# ---- security review L6: any negation of the action, not a list of them -------------------------


@pytest.mark.parametrize(
    "shown",
    [
        f"I couldn't add the shell to your cart [{VARIANT}].",
        f"I didn't add the shell [{VARIANT}].",
        f"I wasn't able to add the shell [{VARIANT}].",
        f"I can not place an order for the shell [{VARIANT}].",
        f"I failed to add the shell [{VARIANT}].",
        f"I have not added the shell [{VARIANT}] yet.",
        f"The shell [{VARIANT}] was never added to the cart.",
    ],
)
def test_any_denial_of_the_order_leaves_the_artifact_alone(tmp_path: Path, shown: str) -> None:
    text, artifact = served(*order_setup(tmp_path, shown=shown))
    assert text is None and artifact is ARTIFACT


@pytest.mark.parametrize(
    "shown",
    [
        AGREEING,
        f"I'm an AI assistant. Your cart now holds one Corvane Breakwater Shell [{VARIANT}].",
    ],
)
def test_a_plain_confirmation_of_the_cart_is_still_served(tmp_path: Path, shown: str) -> None:
    text, _artifact = served(*order_setup(tmp_path, shown=shown))
    assert text == shown


# ---- security re-review N8: a confirmation is required, not only no denial ---------------------


@pytest.mark.parametrize(
    "shown",
    [
        f"Adding the shell to your cart was unsuccessful [{VARIANT}].",
        f"The shell [{VARIANT}] was declined at checkout.",
        f"Your cart is still empty [{VARIANT}].",
        f"The Corvane Breakwater Shell comes in Slate, size M [{VARIANT}].",
    ],
)
def test_a_text_that_confirms_no_cart_action_leaves_the_artifact_alone(
    tmp_path: Path, shown: str
) -> None:
    text, artifact = served(*order_setup(tmp_path, shown=shown))
    assert text is None and artifact is ARTIFACT


# ---- the user's D5 (c): one sentence cites the variant and confirms it in the cart ------------


@pytest.mark.parametrize(
    "shown",
    [
        f"Your cart has nothing in it [{VARIANT}].",  # R6
        f"I added nothing; the shell [{VARIANT}] was declined.",  # R6
        f"I added the shell [{VARIANT}] to your wishlist.",  # R6: not the cart
        # two sentences: the confirmation and the variant are apart
        f"I added it to your cart. It is the Corvane Breakwater Shell [{VARIANT}].",
        f"Your cart now holds the shell, but it was removed again [{VARIANT}].",
        f"The shell [{VARIANT}] is in your cart, without the hood.",
    ],
)
def test_without_one_plain_cart_confirmation_of_the_variant_the_artifact_goes_alone(
    tmp_path: Path, shown: str
) -> None:
    text, artifact = served(*order_setup(tmp_path, shown=shown))
    assert text is None and artifact is ARTIFACT


@pytest.mark.parametrize(
    "shown",
    [
        AGREEING,
        f"Your cart now holds one Corvane Breakwater Shell [{VARIANT}].",
        f"I'm an AI assistant. The Corvane Breakwater Shell [{VARIANT}] is now in your cart.",
        f"I've added the Slate shell, size M, to your cart [{VARIANT}]. Anything else?",
    ],
)
def test_one_plain_cart_confirmation_of_the_variant_is_served(tmp_path: Path, shown: str) -> None:
    text, _artifact = served(*order_setup(tmp_path, shown=shown))
    assert text == shown
