import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

// Row #16: the chat panel also reads /api/cart on mount, so the stub answers the health check only
// and gives the cart its own failure. The assertions are unchanged.
function mockFetch(response: Response | Error) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url !== "/api/healthz") return Promise.resolve(new Response("", { status: 503 }));
      return response instanceof Error ? Promise.reject(response) : Promise.resolve(response);
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[FOUND-0] App shell", () => {
  it("shows gateway ok and the pgvector version", async () => {
    mockFetch(Response.json({ status: "ok", db: "ok", pgvector: "0.8.1" }));
    render(<App />);
    expect(await screen.findByText("gateway: ok · pgvector 0.8.1")).toBeInTheDocument();
  });

  it("shows db unavailable when the gateway reports it", async () => {
    mockFetch(Response.json({ status: "ok", db: "unavailable", pgvector: null }));
    render(<App />);
    expect(await screen.findByText("gateway: ok · db unavailable")).toBeInTheDocument();
  });

  it("shows gateway unavailable when the request fails", async () => {
    mockFetch(new TypeError("network down"));
    render(<App />);
    expect(await screen.findByText("gateway unavailable")).toBeInTheDocument();
  });
});
