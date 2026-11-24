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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

Audience = Literal["shopper", "staff"]

AGENT_AUDIENCE: Mapping[str, Audience] = {
    "shopper-assistant": "shopper",
    # Row #16: the gateway reading a shopper's cart for the chat panel. Never a model.
    "shopper-portal": "shopper",
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

AGENT_TOOLS: Mapping[str, Mapping[str, frozenset[str]]] = {
    "shopper-assistant": {
        "northwind-rag": frozenset({"retrieve_context"}),
        # PRD §7, decision D1 at #10b; attached at #12 (RAG-09).
        "northwind-catalog": CATALOG_READ_TOOLS | CART_TOOLS,  # cart: row #16
    },
    "shopper-portal": {"northwind-rag": frozenset(), "northwind-catalog": frozenset({GET_CART})},
    # PRD §7 gives it catalog writes, not reads, and no write tool exists yet.
    "catalog-subagent": {"northwind-rag": frozenset(), "northwind-catalog": frozenset()},
}

# The assistant gets no resource (S0 item 2): what it can read is what its one tool returns.
AGENT_RESOURCES: Mapping[str, Mapping[str, frozenset[str]]] = {
    "shopper-assistant": {"northwind-rag": frozenset()},
    "shopper-portal": {"northwind-rag": frozenset()},
    "catalog-subagent": {"northwind-rag": frozenset({"northwind-rag://index/manifest"})},
}

# PRD §7 "Explicitly not allowed", as fragments no tool name of that agent may contain.
FORBIDDEN_FRAGMENTS: Mapping[str, tuple[str, ...]] = {
    "shopper-assistant": ("refund", "capture", "assign"),
    "shopper-portal": ("refund", "capture", "assign"),
}


def agents() -> frozenset[str]:
    return frozenset(AGENT_AUDIENCE)


def allowed_tools(agent: str, server: str) -> frozenset[str]:
    return AGENT_TOOLS.get(agent, {}).get(server, frozenset())


def allowed_resources(agent: str, server: str) -> frozenset[str]:
    return AGENT_RESOURCES.get(agent, {}).get(server, frozenset())
