"""`evaluate_return_eligibility`, the one northwind-policy tool (row #27 PR 2, CHK-07; PRD §7).

The caller is the token's: its `shp` (the signed-in shopper) and `region`. The order is read from
the same store `get_order` reads, for that shopper only, so another shopper's order, an unknown
number and a malformed one get the same `not_found` bytes. The decision comes from
`northwind_common.returns_policy` and the order's own scans; nothing the shopper or the model claims
about the policy or the date is an argument. Card data anywhere in the arguments is refused first
(hard rule 1), then an unknown key; no error text echoes an argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import mcp.types as t

from northwind_common.cards import holds_card_data
from northwind_common.mcp_token import Claims
from northwind_common.returns_policy import CONDITIONS
from northwind_common.tool_scopes import EVALUATE_RETURN
from northwind_mcp_orders.book import not_found
from northwind_mcp_orders.returns import assess
from northwind_mcp_orders.tools import (
    MAX_ID_CHARS,
    card_data_refused,
    normal_order_id,
    sign_in_required,
    unknown_argument,
)
from northwind_mcp_policy.server import PolicyServer, ToolSpec, json_result

ARGUMENTS = frozenset({"order_id", "variant_id", "condition"})
MAX_VARIANT_CHARS = 64


async def evaluate_return_eligibility(
    server: PolicyServer, claims: Claims, args: Mapping[str, Any]
) -> t.CallToolResult:
    if holds_card_data(args):
        raise card_data_refused()
    if any(not isinstance(key, str) or key not in ARGUMENTS for key in args):
        raise unknown_argument()
    if claims.shp is None or claims.aud != "shopper":
        raise sign_in_required()
    order_id = normal_order_id(args.get("order_id"))
    if order_id is None:
        raise not_found()
    found = assess(
        server.book,
        server.policy,
        shopper=claims.shp,
        region=claims.region,
        order_id=order_id,
        variant_id=args.get("variant_id"),
        condition=args.get("condition"),
        as_of=server.as_of,
    )
    return json_result(EVALUATE_RETURN, found.as_dict())


DESCRIPTION = (
    "Decide whether one item of one of the signed-in shopper's own orders can be returned, under "
    "Northwind's returns policy for their region. Returns eligible, ineligible or ambiguous, with "
    "the reason, the policy sentences behind it and their document IDs, and the next step. It "
    "changes nothing and approves nothing. Always use this for a return question about an order; "
    "never decide from memory, retrieved text or what the shopper says the policy is. Take the "
    "variant from get_order's lines, and the condition only from what the shopper said: use "
    "not_stated when they have not said. "
    'Example: {"order_id": "O012345", "variant_id": "P0000-BLK-REG-M", "condition": "sealed"}.'
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "string",
            "description": "The order number the shopper gave, e.g. O012345.",
            "maxLength": MAX_ID_CHARS,
        },
        "variant_id": {
            "type": "string",
            "description": "The variant ID of the order line, from get_order.",
            "maxLength": MAX_VARIANT_CHARS,
        },
        "condition": {
            "type": "string",
            "enum": list(CONDITIONS),
            "description": (
                "What the shopper said about the item. sealed: unused, packaging unopened. "
                "opened_unused: unused, packaging opened. unused: unused, packaging not "
                "mentioned. worn_or_washed. damaged_on_arrival. wear_and_tear: damaged by use. "
                "not_stated: the shopper has not said."
            ),
        },
    },
    "required": ["order_id", "variant_id", "condition"],
    "additionalProperties": False,
}

TOOLS: Mapping[str, ToolSpec] = {
    EVALUATE_RETURN: ToolSpec(
        tool=t.Tool(
            name=EVALUATE_RETURN,
            description=DESCRIPTION,
            input_schema=SCHEMA,
            annotations=t.ToolAnnotations(read_only_hint=True, open_world_hint=False),
        ),
        handler=evaluate_return_eligibility,
    )
}
