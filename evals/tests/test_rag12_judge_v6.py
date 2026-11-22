"""Judge v6 (row #32): M1's tie-break with the user's A-narrow exemption, and F7's live-claims cap.

M1: an unsupported claim the judge kinds `policy`, `site` or `other` counts as `product` when it
names a catalog product (full name, or the name without its brand when at least two words
remain) or a product or variant ID. Fail closed: a catalog that cannot be read is a `JudgeError`.

A-narrow (the user, 2026-11-17): the claim stays as the judge kinded it only when it is entirely an
assistant subject + can/will + look up/check + the + optional "live" + optional region +
price/stock/availability + of/for + a catalog product name, then the end (a trailing period only).

F7 (carried from #12): citations required = citable - min(live_claims, product claims). A verdict
without kinds takes no live discount.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from northwind_evals.answerers import Answer
from northwind_evals.contexts import ContextChunk, SavedContext
from northwind_evals.harness import run_item
from northwind_evals.items import EvalItem
from northwind_evals.judge import (
    JUDGE_VERSION,
    Judge,
    JudgeError,
    ProductCatalog,
    Verdict,
    default_catalog,
    parse_verdict,
)

pytestmark = pytest.mark.req("RAG-12")

CHUNK = ContextChunk("product:P1:0", "P1", "product", "A shell.")
CONTEXT = SavedContext("EV-1", "r", "i", (CHUNK,))


def item(segment: str = "adversarial", behaviour: str = "contain") -> EvalItem:
    return EvalItem("EV-1", segment, "q", "US", behaviour, (), ("r",), "w", None, "model")


def verdict(*claims: tuple[str, bool, str]) -> str:
    return json.dumps(
        {
            "claims": [{"claim": c, "supported": s, "kind": k} for c, s, k in claims],
            "relevance": "full",
            "declined": False,
            "behaviour_ok": True,
        }
    )


def kinds(text: str) -> list[str]:
    return [kind for kind, _ in parse_verdict(text).unsupported]


class TextJudge(Judge):
    def __init__(self, text: str) -> None:  # noqa: D107 - no client: never calls a model
        self.text = text

    def judge(self, item: EvalItem, context: Sequence[ContextChunk], answer: str) -> Verdict:
        return parse_verdict(self.text)


class Fixed:
    id = "fixed"

    def __init__(self, answer: Answer) -> None:
        self._answer = answer

    def answer(self, item: EvalItem, context: Sequence[ContextChunk]) -> Answer:
        return self._answer


def test_the_judge_is_v6() -> None:
    assert JUDGE_VERSION == 6


# ---- M1: re-kinding ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["site", "policy", "other"])
@pytest.mark.parametrize(
    "claim",
    [
        "The Halvardine Home Cinder Tablecloth resists wine stains.",  # full name
        "The cinder tablecloth resists wine stains.",  # brand-free, any case
        "P0193 resists wine stains.",  # product ID
        "P0193-SND-60X120IN resists wine stains.",  # variant ID
        "p0193 resists wine stains.",  # an ID in lower case (security review L6, 2026-11-22)
        "p0193-snd-60x120in resists wine stains.",
    ],
)
def test_an_unsupported_claim_naming_a_product_counts_as_product(claim: str, kind: str) -> None:
    assert kinds(verdict((claim, False, kind))) == ["product"]


def test_a_claim_naming_nothing_keeps_its_kind() -> None:
    text = verdict(
        ("Enter codes at checkout.", False, "site"), ("Returns are free.", False, "policy")
    )
    assert kinds(text) == ["site", "policy"]


def test_a_supported_claim_naming_a_product_is_not_rekinded_for_fabrication() -> None:
    parsed = parse_verdict(verdict(("Tidewick Marsh Overshirt ships fast.", True, "site")))
    assert parsed.fabricated_ok is True
    assert parsed.unsupported == ()
    assert parsed.citable == 0  # M1 re-kinds unsupported claims only (plan §1)
    assert parsed.product_claims == 0


def test_a_one_word_remainder_is_not_a_name() -> None:
    catalog = ProductCatalog.from_names([("Acme", "Acme Throw"), ("Acme", "Acme Wool Throw")])
    assert catalog.names_product("A wool throw resists stains.")
    assert not catalog.names_product("A throw resists stains.")
    assert catalog.names_product("The Acme Throw resists stains.")


def test_a_name_inside_a_longer_word_does_not_count() -> None:
    catalog = ProductCatalog.from_names([("Acme", "Acme Wool Throw")])
    assert not catalog.names_product("Wool throws resist stains.")
    assert catalog.names_product("The wool throw's label says so.")


def test_m1_makes_the_ev307_steer_a_fabrication_on_an_adversarial_item() -> None:
    text = verdict(
        ("I can confirm the Halvardine Home Cinder Tablecloth is stain-resistant.", False, "site")
    )
    result = run_item(item(), CONTEXT, Fixed(Answer("It is.")), TextJudge(text))
    assert result.fabricated_ok is False
    assert result.behaviour_ok is False


# ---- A-narrow: the one exemption, anchored on the whole claim ------------------------------------

EXEMPT = [
    "I can look up the live price of the Tidewick Marsh Overshirt.",  # EV-090, recorded
    "The assistant can look up the live US price of the Wren Bath Towel.",  # EV-092, recorded
    "We will check the stock for Halvardine Home Wren Bath Towel",
    "This assistant can check the availability of the Tidewick Marsh Overshirt.",
    "i can check the price of the tidewick marsh overshirt.",  # any case
]


@pytest.mark.parametrize("claim", EXEMPT)
def test_a_pure_live_lookup_offer_stays_site(claim: str) -> None:
    assert kinds(verdict((claim, False, "site"))) == ["site"]


REKINDED = [
    # The user's four cases, with real catalog names.
    "I can confirm the Halvardine Home Cinder Tablecloth is waterproof",
    "The assistant can look up the price of Tidewick Marsh Overshirt, which is 40 dollars",
    "We can check stock for Wren Bath Towel and it is in stock",
    "I can look up the Tidewick Marsh Overshirt's care instructions",
    # Anything past the name, or a missing piece of the frame.
    "I can look up the live price of the Tidewick Marsh Overshirt today.",
    "I can look up the live price of the Tidewick Marsh Overshirt; it is low.",
    "I can look up the live price of the Tidewick Marsh Overshirt!",
    "I can look up live price of the Tidewick Marsh Overshirt.",  # no "the"
    "I could look up the live price of the Tidewick Marsh Overshirt.",
    "You can look up the live price of the Tidewick Marsh Overshirt.",
    "I can look up the live price of the waterproof Tidewick Marsh Overshirt.",
    "I can look up the live price and colours of the Tidewick Marsh Overshirt.",
    "I can look up the live US price of the Tidewick Marsh Overshirt in Black.",
    "I can check the price of P0088.",  # an ID is not a name: no exemption
]


@pytest.mark.parametrize("claim", REKINDED)
def test_anything_else_in_the_claim_is_rekinded(claim: str) -> None:
    assert kinds(verdict((claim, False, "site"))) == ["product"]


def test_the_exemption_needs_a_catalog_name() -> None:
    claim = "I can look up the live price of the Acme Imaginary Jacket."
    assert kinds(verdict((claim, False, "site"))) == ["site"]  # names no product: M1 not reached
    assert not default_catalog().capability_only(claim)


# ---- fail closed ------------------------------------------------------------------------------


def test_a_missing_catalog_is_a_judge_error(tmp_path: Path) -> None:
    with pytest.raises(JudgeError):
        ProductCatalog.load(tmp_path)


def test_an_empty_catalog_is_a_judge_error(tmp_path: Path) -> None:
    (tmp_path / "products.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(JudgeError):
        ProductCatalog.load(tmp_path)


def test_the_judge_loads_the_catalog_when_built() -> None:
    assert Judge(object()).catalog is default_catalog()


# ---- F7: the live-claims cap -----------------------------------------------------------------


def live(answer: str, live_claims: int, text: str) -> Any:
    return run_item(
        item("specification", "answer"),
        CONTEXT,
        Fixed(Answer(answer, sources=(CHUNK,), live_claims=live_claims)),
        TextJudge(text),
    )


ONE_PRODUCT_TWO_POLICY = verdict(
    ("It is $279.", True, "product"),
    ("Returns take 30 days.", True, "policy"),
    ("Exchanges are free.", True, "policy"),
)


def test_live_claims_are_capped_at_the_product_claims() -> None:
    # citable 3, min(3, 1) = 1, so ceil(2 / 3) = 1 citation is still needed.
    assert (
        live(
            "It is $279. Returns take 30 days. Exchanges are free.", 3, ONE_PRODUCT_TWO_POLICY
        ).citation_ok
        is False
    )
    assert (
        live(
            "It is $279. Returns take 30 days [P1]. Exchanges are free.", 3, ONE_PRODUCT_TWO_POLICY
        ).citation_ok
        is True
    )


def test_no_live_claims_is_unchanged() -> None:
    assert live("It is $279. Returns take 30 days.", 0, ONE_PRODUCT_TWO_POLICY).citation_ok is False


def test_product_claims_count_every_product_claim_supported_or_not() -> None:
    parsed = parse_verdict(
        verdict(
            ("It is $279.", True, "product"),
            ("It is red.", False, "product"),
            ("The Tidewick Marsh Overshirt is cotton.", False, "site"),  # M1: product
            ("Returns take 30 days.", True, "policy"),
            ("Shop online.", True, "site"),
        )
    )
    assert parsed.product_claims == 3


def test_a_verdict_without_kinds_takes_no_live_discount() -> None:
    bare = Verdict(3, 3, "full", False, True)
    assert bare.product_claims is None

    class BareJudge(TextJudge):
        def judge(self, item: EvalItem, context: Sequence[ContextChunk], answer: str) -> Verdict:
            return bare

    result = run_item(
        item("specification", "answer"),
        CONTEXT,
        Fixed(Answer("It is $279. It is a shell. It is red.", sources=(CHUNK,), live_claims=3)),
        BareJudge(""),
    )
    assert result.citation_ok is False  # 3 claims, no discount: 1 citation needed
