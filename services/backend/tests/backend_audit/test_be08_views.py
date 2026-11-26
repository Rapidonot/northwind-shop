"""BE-08 and hard rules 8 and 9: every port call through a view is audited, views are scoped per
caller and share one fake's state (FOUND-3 open medium 1, closed here), and nothing under `src` but
the fakes and the views reaches a money call (L2, N3, R7)."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from northwind_backend.audit import InMemoryAuditSink
from northwind_backend.errors import ErrorCategory, Ok, ToolError
from northwind_backend.fakes._base import CARD_DATA_REFUSED, NOT_PERMITTED
from northwind_backend.fakes.carrier import CarrierFake
from northwind_backend.fakes.fraud import FraudFake
from northwind_backend.fakes.payment import (
    PAYMENT_SCOPES,
    SANDBOX_APPROVED,
    SANDBOX_SOFT_DECLINE,
    PaymentFake,
)
from northwind_backend.ports import CarrierPort, FraudPort, Money, PaymentPort
from northwind_backend.views import (
    CARRIER_READ_ONLY,
    FRAUD_READ_ONLY,
    PAYMENT_READ_ONLY,
    AuditedCarrierPort,
    AuditedFraudPort,
    AuditedPaymentPort,
)

from ._confirm import confirm, ref_of
from ._driver import FULL_CARRIER, FULL_FRAUD, fixed_clock

pytestmark = [pytest.mark.req("BE-08")]

SRC = Path(__file__).resolve().parents[2] / "src" / "northwind_backend"
# Every product source root the pins walk. The orchestrator joins when #24 creates it (L6).
ROOTS = [SRC, *Path(__file__).resolve().parents[3].glob("orchestrator/src")]
MONEY_NAMES = frozenset({"authorize", "capture", "void", "refund"})
# The audit's own action names. An exact match never confuses "payment.capture" with "capture", so
# this list only says out loud what is allowed (N3).
AUDIT_ACTION_NAMES = frozenset({f"payment.{name}" for name in MONEY_NAMES})
EXEMPT = ("fakes", "views.py")
USD = Money(5000, "USD")


def ctx(sink: InMemoryAuditSink, actor: str = "tester") -> Any:
    from northwind_backend.audit import AuditContext

    return AuditContext(sink, correlation_id="corr-1", actor=actor, clock=fixed_clock)


def _calls(view: Any) -> dict[str, Callable[[], Any]]:
    """One call per protocol member, with valid arguments."""
    if isinstance(view, AuditedPaymentPort):
        return {
            "authorize": lambda: view.authorize(
                idempotency_key="k-auth",
                payment_token=SANDBOX_APPROVED,
                amount=USD,
                order_id="O000001",
                confirmation_ref=confirm(view, "O000001", USD, "k-auth"),
            ),
            "capture": lambda: view.capture(
                idempotency_key="k-cap",
                intent_id="pi_000001",
                confirmation_ref=ref_of(view, "pi_000001"),
            ),
            "void": lambda: view.void(idempotency_key="k-void", intent_id="pi_000001"),
            "refund": lambda: view.refund(
                idempotency_key="k-ref", intent_id="pi_000001", amount=USD
            ),
            "status": lambda: view.status("pi_000001"),
        }
    if isinstance(view, AuditedFraudPort):
        return {
            "assess": lambda: view.assess(
                idempotency_key="k-fa", order_id="O000001", shopper_id="S0001"
            ),
            "assessments_for": lambda: view.assessments_for("O000001"),
        }
    return {
        "create_shipment": lambda: view.create_shipment(
            idempotency_key="k-sh", order_id="O000001", region="US", postal_code="US-1234"
        ),
        "track": lambda: view.track("NLP-ABCDEFGH"),
    }


def _protocol_members(protocol: type) -> set[str]:
    return {
        name
        for name, member in vars(protocol).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }


VIEWS: list[tuple[type, type, Callable[[], Any], frozenset[str]]] = [
    (PaymentPort, AuditedPaymentPort, PaymentFake, PAYMENT_SCOPES),
    (FraudPort, AuditedFraudPort, FraudFake, FULL_FRAUD),
    (CarrierPort, AuditedCarrierPort, CarrierFake, FULL_CARRIER),
]


@pytest.mark.parametrize(("protocol", "view_type", "fake_type", "scopes"), VIEWS)
def test_every_protocol_member_writes_one_attempt_and_one_outcome(
    protocol: type, view_type: type, fake_type: Callable[[], Any], scopes: frozenset[str]
) -> None:
    sink = InMemoryAuditSink()
    view = view_type(fake_type(), ctx(sink), allowed=scopes)
    assert isinstance(view, protocol)
    calls = _calls(view)
    assert set(calls) == _protocol_members(protocol), "a port method has no audited call here"
    for name, call in calls.items():
        before = len(sink.read_all())
        call()
        written = sink.read_all()[before:]
        assert [e.phase for e in written] == ["attempt", "outcome"], name
        assert written[0].action_id == written[1].action_id
        assert written[0].action.split(".")[0] in ("payment", "fraud", "carrier")


def test_dry_runs_replays_and_refusals_are_audited_too() -> None:
    sink = InMemoryAuditSink()
    fake = PaymentFake()
    view = AuditedPaymentPort(fake, ctx(sink), allowed=PAYMENT_SCOPES)
    kwargs: dict[str, Any] = dict(
        idempotency_key="k1",
        payment_token=SANDBOX_APPROVED,
        amount=USD,
        order_id="O000001",
        confirmation_ref=confirm(fake, "O000001", USD, "k1"),
    )
    view.authorize(**kwargs, dry_run=True)
    view.authorize(**kwargs)
    view.authorize(**kwargs)  # an idempotent replay
    view.authorize(**{**kwargs, "idempotency_key": "4111111111111111"})
    view.authorize(
        **{
            **kwargs,
            "payment_token": SANDBOX_SOFT_DECLINE,
            "idempotency_key": "k2",
            "confirmation_ref": confirm(fake, "O000001", USD, "k2"),
        }
    )
    events = [e for e in sink.read_all() if e.phase == "outcome"]
    outcomes = [dict(e.outcome or {}) for e in events]
    assert len(outcomes) == 5
    assert events[0].inputs["dry_run"] is True and outcomes[0]["dry_run"] is True
    assert outcomes[1] == outcomes[2]
    assert outcomes[3]["code"] == CARD_DATA_REFUSED.code
    assert outcomes[4]["code"] == "soft_decline"
    assert len(fake.effects) == 1


@pytest.mark.parametrize(
    ("view_type", "fake_type", "allowed", "refused"),
    [
        (
            AuditedPaymentPort,
            PaymentFake,
            PAYMENT_READ_ONLY,
            ["authorize", "capture", "void", "refund"],
        ),
        (AuditedFraudPort, FraudFake, FRAUD_READ_ONLY, ["assess"]),
        (AuditedCarrierPort, CarrierFake, CARRIER_READ_ONLY, ["create_shipment"]),
    ],
)
def test_a_read_only_view_refuses_every_other_operation_and_audits_it(
    view_type: type, fake_type: Callable[[], Any], allowed: frozenset[str], refused: list[str]
) -> None:
    sink = InMemoryAuditSink()
    fake = fake_type()
    view = view_type(fake, ctx(sink, "orders-subagent"), allowed=allowed)
    calls = _calls(view)
    for name in refused:
        assert calls[name]() is NOT_PERMITTED
    assert fake.effects == ()
    outcomes = [dict(e.outcome or {}) for e in sink.read_all() if e.phase == "outcome"]
    assert outcomes and all(o["code"] == "not_permitted" for o in outcomes)
    assert all(o["errorCategory"] == ErrorCategory.PERMISSION.value for o in outcomes)


@pytest.mark.req("BE-05")
def test_the_placement_sets_are_exactly_these() -> None:
    """Row #21: no void, no refund, no carrier, no catalog write (hard rule 8, PRD §7)."""
    from northwind_backend.views import CATALOG_READ, FRAUD_PLACE, ORDERS_PLACE, PAYMENT_PLACE

    # Narrowed to what the code calls (F6) and the claim added (F1); both approved by the user.
    assert PAYMENT_PLACE == {"authorize", "capture"}
    assert FRAUD_PLACE == {"assess"}
    assert CATALOG_READ == {"price", "stock"}
    assert ORDERS_PLACE == {"get", "use_address_on_file", "confirm", "escalate", "claim_payment"}


def test_the_inventory_placement_set_is_exactly_these() -> None:
    """Row #25 (BE-04): placement holds, commits and releases stock, by code; no `available` read
    and no other write."""
    from northwind_backend.views import INVENTORY_PLACE

    assert INVENTORY_PLACE == {"reserve", "commit", "release"}


def test_the_read_only_sets_are_reads() -> None:
    assert PAYMENT_READ_ONLY == {"status"}
    assert FRAUD_READ_ONLY == {"read"}
    assert CARRIER_READ_ONLY == {"track"}


def test_views_share_one_instance_so_the_one_payment_rule_holds_across_callers() -> None:
    """FOUND-3 open medium (1): per-caller views over one fake share intents, store and caps."""
    fake = PaymentFake()
    sink = InMemoryAuditSink()
    first = AuditedPaymentPort(fake, ctx(sink, "caller-a"), allowed=PAYMENT_SCOPES)
    second = AuditedPaymentPort(fake, ctx(sink, "caller-b"), allowed=PAYMENT_SCOPES)
    reader = AuditedPaymentPort(fake, ctx(sink, "orders-subagent"), allowed=PAYMENT_READ_ONLY)
    made = first.authorize(
        idempotency_key="a1",
        payment_token=SANDBOX_APPROVED,
        amount=USD,
        order_id="O000001",
        confirmation_ref=confirm(first, "O000001", USD, "a1"),
    )
    assert isinstance(made, Ok)
    again = second.authorize(
        idempotency_key="b1",
        payment_token=SANDBOX_APPROVED,
        amount=USD,
        order_id="O000001",
        confirmation_ref=confirm(second, "O000001", USD, "b1"),
    )
    assert isinstance(again, ToolError) and again.code == "order_already_authorized"
    intent_id = made.value.intent_id
    assert isinstance(
        first.capture(
            idempotency_key="a2", intent_id=intent_id, confirmation_ref=ref_of(first, intent_id)
        ),
        Ok,
    )
    capped = Money(15_000, "USD")
    big = PaymentFake()
    one = AuditedPaymentPort(big, ctx(sink, "caller-a"), allowed=PAYMENT_SCOPES)
    two = AuditedPaymentPort(big, ctx(sink, "caller-b"), allowed=PAYMENT_SCOPES)
    auth = one.authorize(
        idempotency_key="c1",
        payment_token=SANDBOX_APPROVED,
        amount=Money(30_000, "USD"),
        order_id="O000002",
        confirmation_ref=confirm(one, "O000002", Money(30_000, "USD"), "c1"),
    )
    assert isinstance(auth, Ok)
    assert isinstance(
        one.capture(
            idempotency_key="c2",
            intent_id=auth.value.intent_id,
            confirmation_ref=ref_of(one, auth.value.intent_id),
        ),
        Ok,
    )
    assert isinstance(
        one.refund(idempotency_key="c3", intent_id=auth.value.intent_id, amount=capped), Ok
    )
    over = two.refund(idempotency_key="c4", intent_id=auth.value.intent_id, amount=Money(1, "USD"))
    assert isinstance(over, ToolError) and over.code == "refund_over_cap"
    status = reader.status(intent_id)
    assert isinstance(status, Ok) and status.value.status.value == "captured"
    actors = {e.actor for e in sink.read_all()}
    assert actors == {"caller-a", "caller-b", "orders-subagent"}


def test_the_view_writes_its_own_actor_and_correlation() -> None:
    sink = InMemoryAuditSink()
    AuditedFraudPort(
        FraudFake(), ctx(sink, "orders-subagent"), allowed=FRAUD_READ_ONLY
    ).assessments_for("O000001")
    assert {(e.actor, e.correlation_id) for e in sink.read_all()} == {("orders-subagent", "corr-1")}


# AST pins (L2, N3, R7) ----------------------------------------------------------------------------


def _src_modules() -> list[Path]:
    paths = sorted(p for root in ROOTS for p in root.rglob("*.py"))
    assert paths, ROOTS
    return paths


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix() if path.is_relative_to(SRC) else path.as_posix()


def _exempt(path: Path) -> bool:
    rel = _rel(path)
    return rel.split("/")[0] == "fakes" or rel == "views.py"


def money_routes(tree: ast.AST) -> list[str]:
    """Exact matches only: an attribute or name that is a money method, or a string constant that is
    one, or an imported name that is one. Known limits (R7): `import x as c` then `c.capture` is
    still an attribute and caught, but a string built at runtime is not a constant, and a call made
    inside `views.py` itself is exempt by design."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in MONEY_NAMES:
            found.append(f"attribute {node.attr}")
        elif isinstance(node, ast.Name) and node.id in MONEY_NAMES:
            found.append(f"name {node.id}")
        elif isinstance(node, ast.alias) and node.name.rsplit(".", 1)[-1] in MONEY_NAMES:
            found.append(f"import {node.name}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in MONEY_NAMES
            and node.value not in AUDIT_ACTION_NAMES
        ):
            found.append(f"string {node.value!r}")
    return found


# Row #21 (the user's approval of its plan, stop 5): the Orders agent's placement task may reach
# `authorize` and `capture`, as attributes of its payment view, and nothing else on the money list.
PLACEMENT_MODULE = "agents/orders_place.py"
CARE_MODULE = "agents/care.py"
PLACEMENT_ROUTES = frozenset({"attribute authorize", "attribute capture"})


@pytest.mark.req("BE-02")
@pytest.mark.req("BE-05")
def test_nothing_under_src_but_fakes_and_views_reaches_a_money_call() -> None:
    offenders = {
        _rel(path): routes
        for path in _src_modules()
        if not _exempt(path)
        and (routes := money_routes(ast.parse(path.read_text(encoding="utf-8"))))
        and not (_rel(path) == PLACEMENT_MODULE and set(routes) <= PLACEMENT_ROUTES)
    }
    assert offenders == {}


@pytest.mark.req("BE-05")
def test_the_placement_module_reaches_only_authorize_and_capture() -> None:
    """The one exception above, pinned exactly: both routes are used, and `void` and `refund`
    stay out of it (hard rule 8, PRD §7)."""
    path = SRC / PLACEMENT_MODULE
    routes = set(money_routes(ast.parse(path.read_text(encoding="utf-8"))))
    assert routes == PLACEMENT_ROUTES


@pytest.mark.req("BE-05")
@pytest.mark.parametrize(
    "source",
    [
        "self.payment.void(idempotency_key='k', intent_id='pi_000001')",
        "self.payment.refund(idempotency_key='k', intent_id='pi_000001', amount=m)",
        "getattr(self.payment, 'capture')()",
    ],
)
def test_the_placement_exception_admits_no_other_route(source: str) -> None:
    assert not set(money_routes(ast.parse(source))) <= PLACEMENT_ROUTES


@pytest.mark.parametrize(
    "source",
    [
        "view.capture(idempotency_key='k', intent_id='pi_000001')",
        "call_with_recovery(partial(view.capture, idempotency_key='k'))",
        "getattr(view, 'capture')()",
        "f = view.refund",
        "allowed = frozenset({'status', 'authorize'})",
        "from northwind_backend.views import void",
    ],
)
def test_the_pin_catches_every_route(source: str) -> None:
    """One mutant per route (N3)."""
    assert money_routes(ast.parse(source))


def test_the_pin_lets_the_audits_action_names_through() -> None:
    assert money_routes(ast.parse("ACTION = 'payment.capture'")) == []


def test_every_view_built_under_src_uses_a_read_only_constant() -> None:
    """Known limits (re-review N4): a view class renamed on import, passed to `partial`, or bound
    to another name and called is not checked here. The money-name pin above still catches any
    money call outside `views.py`, and each view refuses what its allow-list lacks at runtime."""
    view_names = {
        "AuditedPaymentPort",
        "AuditedFraudPort",
        "AuditedCarrierPort",
        "AuditedCatalogPort",
        "AuditedOrderStore",
        # Row #26 (stop 5, approved by the user 2026-11-24): the case queue's view is checked too.
        "AuditedCaseQueue",
        # Row #25 (BE-04; stop 5, awaiting the user's approval): the inventory view is checked too.
        "AuditedInventoryPort",
    }
    # Advisor finding 8: CATALOG_READ is admitted in the placement module only, as approved.
    read_only = {"PAYMENT_READ_ONLY", "FRAUD_READ_ONLY", "CARRIER_READ_ONLY"}
    # Row #21 (stop 5, approved): the placement sets, in the placement module only.
    placement = {"PAYMENT_PLACE", "FRAUD_PLACE", "CATALOG_READ", "ORDERS_PLACE", "INVENTORY_PLACE"}
    # Row #26 (stop 5, approved): the Care agent's sets, in the Care module only. No catalog read.
    care = {"CARE_READ", "CASE_CREATE"}
    built = 0
    for path in _src_modules():
        if _rel(path) == "views.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            # A bare name or a module attribute (`views.AuditedPaymentPort(...)`), L6.
            called = (
                node.func.id
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                else None
            )
            if called in view_names:
                assert isinstance(node, ast.Call)
                built += 1
                allowed = [k.value for k in node.keywords if k.arg == "allowed"]
                assert allowed and isinstance(allowed[0], ast.Name), path
                constants = (
                    placement
                    if _rel(path) == PLACEMENT_MODULE
                    else care
                    if _rel(path) == CARE_MODULE
                    else read_only
                )
                assert allowed[0].id in constants, (path, allowed[0].id)
    assert built >= 2  # the Orders subagent's payment and fraud views


def test_the_care_sets_are_exactly_these() -> None:
    """Row #26: the values, not only the names, so a set can't grow silently (security review,
    pin comment 1). `CASE_CREATE` is the Care module's one write, made by code, never its model."""
    from northwind_backend import views

    assert frozenset({"get"}) == views.CARE_READ
    assert frozenset({"create_case"}) == views.CASE_CREATE
    assert not hasattr(views, "CARE_PRICES")


def test_nothing_under_src_imports_the_test_driver() -> None:
    for path in _src_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                assert "backend_audit" not in module and "_driver" not in module, path
                assert not module.startswith("tests") and ".tests" not in module, path
