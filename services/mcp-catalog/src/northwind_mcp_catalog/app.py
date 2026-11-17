"""Run the northwind-catalog MCP server: `python -m northwind_mcp_catalog.app` (#10b).

The signing key comes only from `NORTHWIND_MCP_TOKEN_KEY` (hard rule 4). The server refuses to start
without it, and on fixtures that do not match their manifest (Q4: `FixtureCatalog` verifies before
it seeds). It listens on 127.0.0.1 only, one worker, on this worktree's port
(`northwind_common.localenv.mcp_catalog_port`). `make dev` starts it beside mcp-rag, and the
gateway's turns reach it at `localenv.mcp_catalog_url` (row #12).
"""

from __future__ import annotations

import uvicorn

from northwind_common.localenv import mcp_catalog_port
from northwind_common.mcp_token import signing_key
from northwind_mcp_catalog.live import FixtureCatalog
from northwind_mcp_catalog.server import CatalogServer
from northwind_mcp_catalog.tools import TOOLS


def build() -> CatalogServer:
    key = signing_key()
    return CatalogServer(FixtureCatalog(), key=key, tools=TOOLS)


def main() -> None:
    uvicorn.run(
        build().app(), host="127.0.0.1", port=mcp_catalog_port(), workers=1, log_level="warning"
    )


if __name__ == "__main__":
    main()
