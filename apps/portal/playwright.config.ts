import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

// E2E_MODE=replay (the row's smoke test) serves a recorded cassette through the proxy.
// E2E_MODE=scripted serves a canned model, so the browser path runs with no recording at all.
const scripted = process.env.E2E_MODE === "scripted";
const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const modelPort = 18431;
const modelEnv = { LLM_MODE: "replay", LLM_PROXY_PORT: String(modelPort) };

const model = scripted
  ? `uv run python services/assistant/e2e/scripted_model.py --port ${modelPort}`
  : "uv run python -m northwind_llm_replay.cli serve --suite assistant-smoke";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.e2e.ts",
  timeout: 90_000,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    channel: process.env.E2E_BROWSER_CHANNEL || undefined,
  },
  webServer: [
    // `port` only opens a socket: an HTTP probe would use up a scripted reply.
    { command: model, port: modelPort, cwd: repo, env: modelEnv, timeout: 60_000 },
    {
      command: "uv run uvicorn northwind_gateway.app:app --port 8000",
      port: 8000,
      cwd: repo,
      env: modelEnv,
      timeout: 60_000,
    },
    { command: "./node_modules/.bin/vite --port 5173 --strictPort", port: 5173, cwd: here },
  ],
});
