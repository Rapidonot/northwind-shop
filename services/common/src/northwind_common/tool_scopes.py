"""Which agent may use which MCP tool and resource (PRD §7 "Tool scoping by agent"; NFR-01,
hard rule 8).

A server lists and serves only what the token's agent is given here; anything else is refused
with a `permission` error. Each agent has one audience, and a token claiming another is refused.
Only what exists is listed: tools join their agent's row in the row that builds them (catalog at
#10b, cart at #16). Scoping is not attaching: #10b scoped the assistant to the catalog tools, as PRD
§7 lists, and #12 attached them (the gateway's turns get a catalog server and token, RAG-09).

Row #16 (CHK-01, decision 0019): the cart tools live on northwind-catalog, beside the live store
whose stock they check, but in their own set. The assistant gets them (PRD §7 "cart tools"); the
portal's own agent, `shopper-portal`, gets `get_cart` only, to show the cart in the chat panel; the
catalog subagent gets none. No cart tool places an order.

Row #27 (CHK-06, decision 0023): northwind-orders and its one read tool, `get_order`, for the
assistant only (PRD §7: "get_order (own orders)"). The server answers only for the signed-in
shopper in the token (`shp`), so a session with no sign-in gets no order. No other agent has it.

Row #27 PR 2 (CHK-07, decision 0024): northwind-policy and its one read tool,
`evaluate_return_eligibility` (PRD §7), for the assistant only. Its policy resources are for
`shopper-portal` (the gateway, never a model); the assistant still gets no resource (D14).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

Audience = Literal["shopper", "staff"]

AGENT_AUDIENCE: Mapping[str, Audience] = {
    "shopper-assistant": "shopper",
    # Row #16: the gateway reading a shopper's cart for the chat panel. Never a model.
    "shopper-portal": "shopper",
    # Row #11 (RAG-04, decision 0022): the assistant server resolving a shopper's price or stock
    # filter against the live store before its first search. Never a model.
    "shopper-query": "shopper",
    # PRD §7: the catalog subagent owns "northwind-rag index refresh"; here it may read the
    # index manifest, nothing else. No such agent runs yet.
    "catalog-subagent": "staff",
}

CATALOG_READ_TOOLS = frozenset(
    {"search_products", "get_product", "get_variant", "check_stock", "get_price"}
)

# Row #16 (CHK-01). Reads and reversible writes of the caller's own cart; none places an order.
GET_CART = "get_cart"
CART_WRITE_TOOLS = frozenset({"add_to_cart", "remove_from_cart", "set_cart_quantity"})
CART_TOOLS = frozenset({GET_CART, *CART_WRITE_TOOLS})

# Row #11 (RAG-04): the live filter behind a price or availability filter; IDs only.
ELIGIBLE_PRODUCTS = "eligible_products"
# Each region's own currency: a price filter in another currency is never converted.
REGION_CURRENCY: Mapping[str, str] = {"US": "USD", "CA": "CAD", "UK": "GBP", "EU": "EUR"}

# Row #27 (CHK-06). One read of the signed-in shopper's own order; no write, no list.
GET_ORDER = "get_order"
ORDER_TOOLS = frozenset({GET_ORDER})

# Row #27 PR 2 (CHK-07). One read: is one line of the shopper's own order returnable.
EVALUATE_RETURN = "evaluate_return_eligibility"
POLICY_TOOLS = frozenset({EVALUATE_RETURN})
# One resource per kind and region; the server gives a caller only its token's region.
POLICY_KINDS = ("returns", "shipping")
POLICY_RESOURCES = frozenset(
    f"northwind-policy://{kind}/{region}" for kind in POLICY_KINDS for region in REGION_CURRENCY
)

AGENT_TOOLS: Mapping[str, Mapping[str, frozenset[str]]] = {
    "shopper-assistant": {
        "northwind-rag": frozenset({"retrieve_context"}),
        # PRD §7, decision D1 at #10b; attached at #12 (RAG-09).
        "northwind-catalog": CATALOG_READ_TOOLS | CART_TOOLS,  # cart: row #16
        "northwind-orders": ORDER_TOOLS,  # row #27 (CHK-06), attached only when signed in
        "northwind-policy": POLICY_TOOLS,  # row #27 PR 2 (CHK-07), likewise; PRD §7
    },
    "shopper-portal": {"northwind-rag": frozenset(), "northwind-catalog": frozenset({GET_CART})},
    # Row #11: one read-only tool that returns product IDs only (RAG-04, hard rule 2).
    "shopper-query": {
        "northwind-rag": frozenset(),
        "northwind-catalog": frozenset({ELIGIBLE_PRODUCTS}),
    },
    # PRD §7 gives it catalog writes, not reads, and no write tool exists yet.
    "catalog-subagent": {"northwind-rag": frozenset(), "northwind-catalog": frozenset()},
}

# The assistant gets no resource (S0 item 2): what it can read is what its one tool returns.
AGENT_RESOURCES: Mapping[str, Mapping[str, frozenset[str]]] = {
    "shopper-assistant": {"northwind-rag": frozenset()},
    # Row #27 PR 2 (D14): the public returns and shipping policies. Only tests read them so far.
    "shopper-portal": {"northwind-rag": frozenset(), "northwind-policy": POLICY_RESOURCES},
    "shopper-query": {"northwind-rag": frozenset()},
    "catalog-subagent": {"northwind-rag": frozenset({"northwind-rag://index/manifest"})},
}

# PRD §7 "Explicitly not allowed", as fragments no tool name of that agent may contain.
FORBIDDEN_FRAGMENTS: Mapping[str, tuple[str, ...]] = {
    "shopper-assistant": ("refund", "capture", "assign"),
    "shopper-portal": ("refund", "capture", "assign"),
    "shopper-query": ("refund", "capture", "assign"),
}


def agents() -> frozenset[str]:
    return frozenset(AGENT_AUDIENCE)


def allowed_tools(agent: str, server: str) -> frozenset[str]:
    return AGENT_TOOLS.get(agent, {}).get(server, frozenset())


def allowed_resources(agent: str, server: str) -> frozenset[str]:
    return AGENT_RESOURCES.get(agent, {}).get(server, frozenset())
