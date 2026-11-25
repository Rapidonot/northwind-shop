"""Run the northwind-policy MCP server: `python -m northwind_mcp_policy.app` (row #27 PR 2, CHK-07).

The signing key comes only from `NORTHWIND_MCP_TOKEN_KEY` (hard rule 4). The server refuses to start
without it, on data files that do not match their manifests, and on a policy document that no longer
holds a fact eligibility relies on. It listens on 127.0.0.1 only, one worker, on this worktree's
port (`northwind_common.policy_tool.mcp_policy_port`)."""

from __future__ import annotations

import uvicorn

from northwind_common.mcp_token import signing_key
from northwind_common.policy_tool import mcp_policy_port
from northwind_mcp_orders.book import OrderBook
from northwind_mcp_orders.policy_data import PolicyData
from northwind_mcp_policy.server import PolicyServer
from northwind_mcp_policy.tools import TOOLS


def build() -> PolicyServer:
    key = signing_key()
    return PolicyServer(OrderBook.from_data(), PolicyData.from_fixtures(), key=key, tools=TOOLS)


def main() -> None:
    uvicorn.run(
        build().app(), host="127.0.0.1", port=mcp_policy_port(), workers=1, log_level="warning"
    )


if __name__ == "__main__":
    main()
