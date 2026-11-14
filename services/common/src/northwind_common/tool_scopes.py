"""Which agent may use which MCP tool and resource (PRD §7 "Tool scoping by agent"; NFR-01,
hard rule 8).

A server lists and serves only what the token's agent is given here; anything else is refused
with a `permission` error. Each agent has one audience, and a token claiming another is refused.
Only what exists is listed: tools join their agent's row in the row that builds them (catalog at
#10b, cart at #16). Scoping is not attaching: at #10b the assistant is scoped to the catalog tools,
as PRD §7 lists, but it is given no catalog server or token until #12 (Q1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

Audience = Literal["shopper", "staff"]

AGENT_AUDIENCE: Mapping[str, Audience] = {
    "shopper-assistant": "shopper",
    # PRD §7: the catalog subagent owns "northwind-rag index refresh"; here it may read the
    # index manifest, nothing else. No such agent runs yet.
    "catalog-subagent": "staff",
}

CATALOG_READ_TOOLS = frozenset(
    {"search_products", "get_product", "get_variant", "check_stock", "get_price"}
)

AGENT_TOOLS: Mapping[str, Mapping[str, frozenset[str]]] = {
    "shopper-assistant": {
        "northwind-rag": frozenset({"retrieve_context"}),
        # PRD §7, decision D1 at #10b: scoped, not attached (Q1).
        "northwind-catalog": CATALOG_READ_TOOLS,
    },
    # PRD §7 gives it catalog writes, not reads, and no write tool exists yet.
    "catalog-subagent": {"northwind-rag": frozenset(), "northwind-catalog": frozenset()},
}

# The assistant gets no resource (S0 item 2): what it can read is what its one tool returns.
AGENT_RESOURCES: Mapping[str, Mapping[str, frozenset[str]]] = {
    "shopper-assistant": {"northwind-rag": frozenset()},
    "catalog-subagent": {"northwind-rag": frozenset({"northwind-rag://index/manifest"})},
}

# PRD §7 "Explicitly not allowed", as fragments no tool name of that agent may contain.
FORBIDDEN_FRAGMENTS: Mapping[str, tuple[str, ...]] = {
    "shopper-assistant": ("refund", "capture", "assign"),
}


def agents() -> frozenset[str]:
    return frozenset(AGENT_AUDIENCE)


def allowed_tools(agent: str, server: str) -> frozenset[str]:
    return AGENT_TOOLS.get(agent, {}).get(server, frozenset())


def allowed_resources(agent: str, server: str) -> frozenset[str]:
    return AGENT_RESOURCES.get(agent, {}).get(server, frozenset())
