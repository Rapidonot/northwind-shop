"""Run the northwind-orders MCP server: `python -m northwind_mcp_orders.app` (row #27, CHK-06).

The signing key comes only from `NORTHWIND_MCP_TOKEN_KEY` (hard rule 4). The server refuses to start
without it, on `data/backend` files that do not match their manifest, and on policy documents that
no longer hold a returns fact (D14). It listens on 127.0.0.1
only, one worker, on this worktree's port (`northwind_common.order_tool.mcp_orders_port`).
`make dev` starts it beside mcp-rag and mcp-catalog; the gateway's signed-in turns reach it at
`order_tool.mcp_orders_url`.
"""

from __future__ import annotations

import uvicorn

from northwind_common.mcp_token import signing_key
from northwind_common.order_tool import mcp_orders_port
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.server import OrdersServer
from northwind_mcp_orders.tools import TOOLS


def build() -> OrdersServer:
    key = signing_key()
    return OrdersServer(OrderBook.from_data(), key=key, tools=TOOLS)


def main() -> None:
    uvicorn.run(
        build().app(), host="127.0.0.1", port=mcp_orders_port(), workers=1, log_level="warning"
    )


if __name__ == "__main__":
    main()
