import { randomBytes } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

// E2E_MODE=replay (the row's smoke test) serves a recorded cassette through the proxy.
// E2E_MODE=scripted serves a canned model, so the browser path runs with no recording at all.
const scripted = process.env.E2E_MODE === "scripted";
const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const modelPort = 18431;
// The URL too: it beats the port, and a shell may export one for a dev proxy that spends.
const modelEnv = {
  LLM_MODE: "replay",
  LLM_PROXY_PORT: String(modelPort),
  LLM_PROXY_URL: `http://127.0.0.1:${modelPort}`,
};

// A fresh signing key per run, never written anywhere; the gateway, mcp-rag and mcp-catalog share it
// through their environments.
const tokenKey = randomBytes(32).toString("hex");

// mcp-rag (CC-10): the gateway's searches and the model's tool calls go to it.
// Frozen vectors (decision 0012) so the first search ranks as the recording did.
const ragPort = 18432;
const ragEnv = {
  NORTHWIND_MCP_TOKEN_KEY: tokenKey,
  MCP_RAG_PORT: String(ragPort),
  MCP_RAG_URL: `http://127.0.0.1:${ragPort}/mcp`,
  MCP_RAG_EMBEDDER: "cached",
};

// mcp-catalog (row #12, RAG-09): the gateway always attaches it, so the model's request offers its
// five tools, as the recorded smoke turn did. Without it the turn's request differs from the
// recording and replay misses.
const catalogPort = 18433;
const catalogEnv = {
  NORTHWIND_MCP_TOKEN_KEY: tokenKey,
  MCP_CATALOG_PORT: String(catalogPort),
  MCP_CATALOG_URL: `http://127.0.0.1:${catalogPort}/mcp`,
};

// mcp-orders (row #27, CHK-06): the gateway attaches it only to a signed-in turn, and the browser
// journeys are anonymous, so no request here reaches it. Started anyway, so every server the gateway
// can attach runs in the browser tests (#12's lesson), and a signed-in journey can follow.
const ordersPort = 18434;
const ordersEnv = {
  NORTHWIND_MCP_TOKEN_KEY: tokenKey,
  MCP_ORDERS_PORT: String(ordersPort),
  MCP_ORDERS_URL: `http://127.0.0.1:${ordersPort}/mcp`,
};

// mcp-policy (row #27 PR 2, CHK-07): as mcp-orders.
const policyPort = 18435;
const policyEnv = {
  NORTHWIND_MCP_TOKEN_KEY: tokenKey,
  MCP_POLICY_PORT: String(policyPort),
  MCP_POLICY_URL: `http://127.0.0.1:${policyPort}/mcp`,
};

const model = scripted
  ? `uv run python services/assistant/e2e/scripted_model.py --port ${modelPort}`
  : "uv run python -m northwind_llm_replay.cli serve --suite assistant-smoke";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.e2e.ts",
  timeout: 90_000,
  reporter: "list",
  // SA-01 PR 2: the checkout journey counts the canned model's requests across Pay (demo), so no
  // other journey may talk to the model meanwhile: it runs after every other journey has finished
  // (`src/e2eConfig.test.ts`). In the user's run with both together, a parallel chat journey moved
  // the count from 2 to 3 during Pay.
  projects: [
    { name: "journeys", testIgnore: /checkout\.e2e\.ts$/ },
    { name: "checkout", testMatch: /checkout\.e2e\.ts$/, dependencies: ["journeys"] },
  ],
  use: {
    baseURL: "http://localhost:5173",
    channel: process.env.E2E_BROWSER_CHANNEL || undefined,
  },
  webServer: [
    // `port` only opens a socket: an HTTP probe would use up a scripted reply.
    { command: model, port: modelPort, cwd: repo, env: modelEnv, timeout: 60_000 },
    {
      command: "uv run python -m northwind_mcp_rag.app",
      port: ragPort,
      cwd: repo,
      env: ragEnv,
      timeout: 120_000,
    },
    {
      command: "uv run python -m northwind_mcp_catalog.app",
      port: catalogPort,
      cwd: repo,
      env: catalogEnv,
      timeout: 120_000,
    },
    {
      command: "uv run python -m northwind_mcp_orders.app",
      port: ordersPort,
      cwd: repo,
      env: ordersEnv,
      timeout: 120_000,
    },
    {
      command: "uv run python -m northwind_mcp_policy.app",
      port: policyPort,
      cwd: repo,
      env: policyEnv,
      timeout: 120_000,
    },
    {
      command: "uv run uvicorn northwind_gateway.app:app --port 8000",
      port: 8000,
      cwd: repo,
      // SA-01 PR 2: the demo sign-in is on, as in `make dev`, for the checkout journey; every other
      // journey stays anonymous (sign-in happens only on its own POST).
      env: { ...modelEnv, ...ragEnv, ...catalogEnv, ...ordersEnv, ...policyEnv, NORTHWIND_DEMO_SIGNIN: "1" },
      timeout: 60_000,
    },
    { command: "./node_modules/.bin/vite --port 5173 --strictPort", port: 5173, cwd: here },
  ],
});
