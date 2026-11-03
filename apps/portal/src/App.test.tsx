import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function mockFetch(response: Response | Error) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => (response instanceof Error ? Promise.reject(response) : Promise.resolve(response))),
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
