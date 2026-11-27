import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { CARD_REFUSED, mightBeCardData } from "./cardGuard";
import { parseHash } from "./router";
import { fetchProduct, fetchProducts } from "./shopApi";

// SA-01 PR 1, security review F1 (hard rule 1: no PAN in a URL, so none in a log) and F3 (one
// session on a first visit).

const PANS = [
  "4242424242424242",
  "4242 4242 4242 4242",
  "4242-4242-4242-4242",
  "4242.4242.4242.4242",
  "378282246310005",
  "4242/4242/4242/4242", // re-review N2: the server's [\W_] separators
  "4242,4242,4242,4242",
  "4242:4242:4242:4242",
];

function serve() {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      urls.push(url);
      if (url === "/api/cart") return Promise.resolve(Response.json({ cart: { region: "US", lines: [], item_count: 0, subtotal: null, currency: null } }));
      if (url === "/api/departments") return Promise.resolve(Response.json({ departments: ["Bath"] }));
      if (url.startsWith("/api/products")) return Promise.resolve(Response.json({ region: "US", total: 0, products: [] }));
      return Promise.resolve(new Response("", { status: 503 }));
    }),
  );
  return urls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("[SA-01] [NFR-04] card data never goes into a URL", () => {
  it.each(PANS)("%s might be card data", (pan) => {
    expect(mightBeCardData(pan)).toBe(true);
    expect(mightBeCardData(`rain jacket ${pan}`)).toBe(true);
  });

  it.each(["rain jacket", "P0001", "size 10", "2024 merino 200gsm", "12345678901"])("%s is not", (text) => {
    expect(mightBeCardData(text)).toBe(false);
  });

  it.each(PANS.flatMap((pan) => [`#/search?q=${encodeURIComponent(pan)}`, `#/d/${encodeURIComponent(pan)}`, `#/search?q=${pan.replaceAll(" ", "+")}`]))(
    "the route %s is not found",
    (hash) => {
      expect(parseHash(hash)).toEqual({ page: "not-found" });
    },
  );

  it("the API client never sends one, whatever it is asked", async () => {
    const urls = serve();
    expect(await fetchProducts({ q: "4242 4242 4242 4242" })).toEqual({ kind: "unavailable" });
    expect(await fetchProducts({ department: "4242424242424242" })).toEqual({ kind: "unavailable" });
    expect(await fetchProduct("4242-4242-4242-4242")).toEqual({ kind: "unavailable" });
    expect(urls).toEqual([]);
  });

  it("the search box refuses it, says why, and navigates nowhere", async () => {
    const urls = serve();
    render(<App />);
    const box = screen.getByRole("searchbox", { name: "Search products" });
    fireEvent.change(box, { target: { value: "4242 4242 4242 4242" } });
    fireEvent.submit(box.closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent(CARD_REFUSED);
    expect(box).toHaveValue("");
    expect(window.location.hash).toBe("");
    await waitFor(() => expect(urls).toContain("/api/departments"));
    for (const url of urls) expect(url).not.toMatch(/4242/);
  });

  it("a crafted link with a card number makes no request with it", async () => {
    const urls = serve();
    window.location.hash = "#/d/4242424242424242";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "We couldn't find that page." })).toBeInTheDocument();
    for (const url of urls) expect(url).not.toMatch(/4242/);
  });
});

describe("[SA-01] a first visit opens one session", () => {
  it("reads the cart before any other session path", async () => {
    const urls = serve();
    window.location.hash = "#/d/Bath";
    render(<App />);
    // SA-01 PR 2: one page only (only the products on screen are priced).
    await waitFor(() => expect(urls).toContain("/api/products?department=Bath&limit=24"));
    const sessionPaths = urls.filter((u) => u !== "/api/healthz");
    expect(sessionPaths[0]).toBe("/api/cart");
  });
});
