"""The per-turn MCP token (CC-10; NFR-01, NFR-02, hard rules 4 and 8)."""

from __future__ import annotations

import ast
import base64
import inspect
import json
from pathlib import Path

import pytest

from northwind_common import mcp_token
from northwind_common.mcp_token import TokenError, TokenKeyError, mint, signing_key

# Private since row #12 (#10b re-review P1): `verify_for` is the public check. These tests reach the
# server-blind check on purpose, to test the token itself.
verify = mcp_token._verify

pytestmark = [pytest.mark.req("CC-10"), pytest.mark.req("NFR-02")]

KEY = b"k" * 40
OTHER = b"o" * 40
NOW = 1_800_000_000.0


def shopper(**kw: object) -> str:
    args: dict[str, object] = {
        "audience": "shopper",
        "region": "UK",
        "agent": "shopper-assistant",
        "key": KEY,
        "now": NOW,
        "server": "northwind-rag",
    }
    args.update(kw)
    return mint(**args)[0]  # type: ignore[arg-type]


def shopper_claims() -> mcp_token.Claims:
    return mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=KEY,
        now=NOW,
        server="northwind-rag",
    )[1]


def b64(obj: object) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forge(body: dict[str, object], key: bytes = KEY) -> str:
    payload = b64(body)
    return f"v1.{payload}.{mcp_token._sign(key, payload)}"


def good_body(**kw: object) -> dict[str, object]:
    body: dict[str, object] = {
        "aud": "shopper",
        "region": "UK",
        "agent": "shopper-assistant",
        # #10b (L4): a valid body names its server, so each bad-claim case fails on its own claim.
        "srv": "northwind-rag",
        "turn": "1b4e28ba-2fa1-4d2e-883f-0016d3cca427",
        "exp": int(NOW) + 60,
    }
    body.update(kw)
    return body


def test_a_minted_token_verifies_to_its_claims() -> None:
    token, claims = mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=KEY,
        now=NOW,
        server="northwind-rag",
    )
    assert verify(token, key=KEY, now=NOW + 1) == claims
    assert claims.aud == "shopper" and claims.region == "UK" and claims.agent == "shopper-assistant"
    assert claims.exp == int(NOW) + mcp_token.TTL_SECONDS == int(NOW) + 120


def test_every_mint_is_a_new_turn() -> None:
    a = shopper_claims()
    b = shopper_claims()
    assert a.turn != b.turn


def test_a_forged_signature_is_refused() -> None:
    token = shopper()
    with pytest.raises(TokenError):
        verify(token, key=OTHER, now=NOW)
    head, payload, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(TokenError):
        verify(f"{head}.{payload}.{flipped}", key=KEY, now=NOW)


def test_changing_a_claim_breaks_the_signature() -> None:
    _, _, sig = shopper().split(".")
    staff = b64(good_body(aud="staff", agent="catalog-subagent", region=None))
    with pytest.raises(TokenError):
        verify(f"v1.{staff}.{sig}", key=KEY, now=NOW)


def test_an_expired_token_is_refused() -> None:
    token = shopper()
    verify(token, key=KEY, now=NOW + 119)
    with pytest.raises(TokenError):
        verify(token, key=KEY, now=NOW + 120)


@pytest.mark.parametrize(
    "body",
    [
        good_body(exp=int(NOW) + 3600),  # signed, but lives too long
        good_body(aud="staff"),  # the assistant is a shopper agent
        good_body(agent="catalog-subagent"),  # a staff agent with a shopper audience
        good_body(agent="refund-bot"),
        good_body(region=None),
        good_body(region="uk"),
        good_body(region="UK<script>"),
        good_body(turn="not-a-uuid"),
        good_body(turn="1b4e28ba-2fa1-1d2e-883f-0016d3cca427"),  # uuid1, not uuid4
        good_body(exp=True),
        good_body(exp=str(int(NOW) + 60)),
        {**good_body(), "extra": 1},
        {k: v for k, v in good_body().items() if k != "turn"},
    ],
)
def test_a_signed_token_with_bad_claims_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(TokenError):
        verify(forge(body), key=KEY, now=NOW)


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "v1",
        "v1..",
        "v2.a.b",
        "v1.a.b.c",
        "v1.!!!.sig",
        "v1.é.sig",
        "x" * 3000,
        42,
    ],
)
def test_a_malformed_token_is_refused(token: object) -> None:
    with pytest.raises(TokenError):
        verify(token, key=KEY, now=NOW)


def test_a_refusal_says_nothing_about_why() -> None:
    reasons = []
    for token in (None, shopper(now=NOW - 500), forge(good_body(aud="staff")), "v1.a.b"):
        with pytest.raises(TokenError) as caught:
            verify(token, key=KEY, now=NOW)
        reasons.append(str(caught.value))
    assert set(reasons) == {"invalid token"}


def test_a_staff_token_may_have_no_region() -> None:
    token, claims = mint(
        audience="staff",
        region=None,
        agent="catalog-subagent",
        key=KEY,
        now=NOW,
        server="northwind-rag",
    )
    assert verify(token, key=KEY, now=NOW).region is None and claims.aud == "staff"


@pytest.mark.parametrize(
    "kw",
    [
        {"audience": "staff"},  # the assistant is a shopper agent
        {"agent": "nobody"},
        {"region": None},
        {"ttl": 0},
        {"ttl": 121},
    ],
)
def test_minting_refuses_what_verify_would_refuse(kw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        shopper(**kw)


def test_the_key_comes_only_from_the_environment_and_must_be_long() -> None:
    assert signing_key({mcp_token.KEY_ENV: "z" * 32}) == b"z" * 32
    for env in ({}, {mcp_token.KEY_ENV: "short"}):
        with pytest.raises(TokenKeyError) as caught:
            signing_key(env)
        assert "short" not in str(caught.value) and mcp_token.KEY_ENV in str(caught.value)


def test_the_key_is_not_in_the_token() -> None:
    token = shopper()
    payload = token.split(".")[1]
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    assert KEY.decode() not in token and KEY.decode() not in decoded
    assert set(json.loads(decoded)) == mcp_token.CLAIMS


def test_bearer_header() -> None:
    assert mcp_token.bearer("abc") == {"Authorization": "Bearer abc"}


# ---- L4 (#10b): the token is bound to a server ---------------------------------------------------


def for_server(server: str) -> str:
    return mint(
        audience="shopper",
        region="UK",
        agent="shopper-assistant",
        key=KEY,
        now=NOW,
        server=server,
    )[0]


def test_the_servers_are_rag_catalog_and_orders() -> None:
    # Row #27 (CHK-06) adds northwind-orders, exactly.
    assert mcp_token.SERVERS == {"northwind-rag", "northwind-catalog", "northwind-orders"}
    assert (mcp_token.RAG_SERVER, mcp_token.CATALOG_SERVER, mcp_token.ORDERS_SERVER) == (
        "northwind-rag",
        "northwind-catalog",
        "northwind-orders",
    )
    assert "srv" in mcp_token.CLAIMS


@pytest.mark.req("RAG-09")
def test_mint_has_no_default_server() -> None:
    """Row #12 removes #10b's `northwind-rag` default: a minter that forgets the server gets a
    `TypeError`, never a token for the wrong server."""
    with pytest.raises(TypeError):
        mint(audience="shopper", region="UK", agent="shopper-assistant", key=KEY, now=NOW)  # type: ignore[call-arg]
    assert inspect.signature(mint).parameters["server"].default is inspect.Parameter.empty


@pytest.mark.req("RAG-09")
def test_verify_is_not_a_public_name() -> None:
    """Row #12 (#10b re-review P1): `verify_for` is the only public check."""
    assert not hasattr(mcp_token, "verify")
    assert "verify" not in getattr(mcp_token, "__all__", ())
    public = {name for name in vars(mcp_token) if not name.startswith("_")}
    assert "verify_for" in public and not {n for n in public if n.startswith("verify")} - {
        "verify_for"
    }


@pytest.mark.parametrize("server", ["northwind-rag", "northwind-catalog"])
def test_srv_is_signed_into_the_token(server: str) -> None:
    token = for_server(server)
    payload = token.split(".")[1]
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert body["srv"] == server
    assert verify(token, key=KEY, now=NOW).srv == server
    # editing srv without re-signing breaks the signature
    other = "northwind-catalog" if server == "northwind-rag" else "northwind-rag"
    body["srv"] = other
    forged = f"v1.{b64(body)}.{token.split('.')[2]}"
    with pytest.raises(TokenError):
        verify(forged, key=KEY, now=NOW)


def test_a_token_without_srv_is_refused() -> None:
    body = {k: v for k, v in good_body().items() if k != "srv"}
    with pytest.raises(TokenError):
        verify(forge(body), key=KEY, now=NOW)
    with pytest.raises(TokenError):
        mcp_token.verify_for(forge(body), key=KEY, server="northwind-rag", now=NOW)


@pytest.mark.parametrize(
    # Row #27: northwind-orders is a server now, so the unknown one is named for what it is.
    "srv",
    ["northwind-unknown", "", "NORTHWIND-RAG", None, 7, ["northwind-rag"]],
)
def test_an_unknown_srv_is_refused(srv: object) -> None:
    with pytest.raises(TokenError):
        verify(forge(good_body(srv=srv)), key=KEY, now=NOW)


@pytest.mark.parametrize(
    ("minted", "asked"),
    [("northwind-rag", "northwind-catalog"), ("northwind-catalog", "northwind-rag")],
)
def test_verify_for_refuses_the_other_server(minted: str, asked: str) -> None:
    token = for_server(minted)
    assert mcp_token.verify_for(token, key=KEY, server=minted, now=NOW).srv == minted
    with pytest.raises(TokenError) as caught:
        mcp_token.verify_for(token, key=KEY, server=asked, now=NOW)
    assert str(caught.value) == str(TokenError())  # the same bare refusal


def test_verify_for_refuses_an_unknown_server_and_still_checks_the_token() -> None:
    with pytest.raises(TokenError):
        mcp_token.verify_for(for_server("northwind-rag"), key=KEY, server="nope", now=NOW)
    with pytest.raises(TokenError):
        mcp_token.verify_for(
            for_server("northwind-rag"), key=OTHER, server="northwind-rag", now=NOW
        )
    with pytest.raises(TokenError):
        mcp_token.verify_for(
            for_server("northwind-rag"), key=KEY, server="northwind-rag", now=NOW + 1000
        )


def test_minting_for_an_unknown_server_raises() -> None:
    with pytest.raises(ValueError):
        mint(
            audience="shopper",
            region="UK",
            agent="shopper-assistant",
            key=KEY,
            now=NOW,
            server="northwind-unknown",
        )


SKIP_DIRS = {".git", ".venv", "node_modules", "tests", "__pycache__", ".claude", "cassettes"}


def _non_test_sources(repo: Path) -> list[Path]:
    """Every non-test Python file in the repository, whatever its source root (services, data,
    evals and later roots such as agents-external), not just `services/*/src` (security review
    L4, #10b)."""
    return [
        p
        for p in repo.rglob("*.py")
        if not SKIP_DIRS & set(p.relative_to(repo).parts[:-1]) and not p.name.startswith("test_")
    ]


def _bare_verify_uses(tree: ast.AST) -> list[int]:
    """Lines that reach bare `verify`: a call named `verify`; an import of `verify` (any alias) or
    of `*` from `mcp_token`; `.verify` on any name bound to the `mcp_token` module, called or not;
    `getattr(<that name>, "verify")` (re-review N6)."""
    modules = {"mcp_token"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("mcp_token"):
                    modules.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "mcp_token":
                    modules.add(alias.asname or alias.name)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in ("verify", "_verify"):
                lines.append(node.lineno)
            elif (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in modules
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in ("verify", "_verify")
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").endswith("mcp_token"):
            if any(alias.name in ("verify", "_verify", "*") for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in ("verify", "_verify"):
            receiver = node.value
            if isinstance(receiver, ast.Name) and receiver.id in modules:
                lines.append(node.lineno)
            elif isinstance(receiver, ast.Attribute) and receiver.attr == "mcp_token":
                lines.append(node.lineno)
    return sorted(set(lines))


def test_no_source_reaches_bare_verify() -> None:
    """A server's gate uses `verify_for`; a bare `verify` would accept a token for any server.
    Every source root, not only `services/*/src`."""
    repo = Path(__file__).resolve().parents[3]
    own = Path(mcp_token.__file__).resolve()
    sources = [p for p in _non_test_sources(repo) if p.resolve() != own]
    assert len(sources) > 40
    assert any(p.parts[-3:-1] == ("src", "northwind_data") for p in sources)
    offenders = [
        f"{path.relative_to(repo)}:{line}"
        for path in sources
        for line in _bare_verify_uses(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []


def test_the_bare_verify_pin_sees_an_aliased_import_and_an_attribute() -> None:
    code = (
        "from northwind_common.mcp_token import verify as check\n"
        "from northwind_common import mcp_token\n"
        "x = mcp_token.verify\n"
        "import northwind_common.mcp_token as tok\n"
        "y = tok.verify\n"
        "z = getattr(tok, 'verify')\n"
        "from northwind_common.mcp_token import *\n"
        "w = northwind_common.mcp_token.verify\n"
        "ok = tok.verify_for\n"
    )
    assert _bare_verify_uses(ast.parse(code)) == [1, 3, 5, 6, 7, 8]


def test_the_pin_also_sees_the_private_name() -> None:
    """Row #12: `verify` is now `_verify`; reaching it from a non-test source is the same bypass."""
    code = (
        "from northwind_common.mcp_token import _verify\n"
        "from northwind_common import mcp_token\n"
        "x = mcp_token._verify\n"
        "y = getattr(mcp_token, '_verify')\n"
        "ok = mcp_token.verify_for\n"
    )
    assert _bare_verify_uses(ast.parse(code)) == [1, 3, 4]
