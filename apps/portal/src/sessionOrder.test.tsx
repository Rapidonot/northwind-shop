import { render, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchCart } from "./cartApi";
import { ChatPanel } from "./ChatPanel";
import { requestHandoff, sendMessage } from "./chatStream";
import { fetchProducts } from "./shopApi";

// SA-01 PR 1, the browser run at 61ea21e: the gateway mints a session for every session-path
// request that arrives without a cookie, so two such requests in flight split the shopper's session:
// the cart journey added to one cart and read another, and the handoff journey got a second
// reference. The first session-path request must go alone; every other waits for its response, so
// the browser holds one cookie before a second request leaves.

type Pending = { url: string; resolve: (r: Response) => void };

function deferredFetch() {
  const pending: Pending[] = [];
  const mock = vi.fn((url: string) => new Promise<Response>((resolve) => pending.push({ url, resolve })));
  vi.stubGlobal("fetch", mock);
  return { mock, pending };
}

const emptyCart = () => Response.json({ cart: { region: "US", lines: [], item_count: 0, subtotal: null, currency: null } });

afterEach(() => vi.unstubAllGlobals());

describe("[SA-01] [CHK-01] [SA-06] one session on a first visit", () => {
  it("a second session-path request waits for the first one's response", async () => {
    const { mock, pending } = deferredFetch();
    const first = fetchCart();
    const second = fetchCart();
    const products = fetchProducts({ department: "Bath" });
    await Promise.resolve();
    expect(mock).toHaveBeenCalledTimes(1);
    pending[0]!.resolve(emptyCart());
    await first;
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
    pending[1]!.resolve(emptyCart());
    pending[2]!.resolve(Response.json({ region: "US", total: 0, products: [] }));
    expect((await second).kind).toBe("ready");
    expect((await products).kind).toBe("ready");
  });

  it("a chat message or a handoff sent before the first response waits for it", async () => {
    const { mock, pending } = deferredFetch();
    void fetchCart();
    void sendMessage("hello", () => {});
    void requestHandoff([], "button");
    await Promise.resolve();
    expect(mock.mock.calls.map(([url]) => url)).toEqual(["/api/cart"]);
    pending[0]!.resolve(emptyCart());
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
    expect(mock.mock.calls.map(([url]) => url).sort()).toEqual(["/api/cart", "/api/chat", "/api/handoff"]);
  });

  it("StrictMode's double mount sends one cart read before the first response", async () => {
    const { mock, pending } = deferredFetch();
    render(
      <StrictMode>
        <ChatPanel />
      </StrictMode>,
    );
    await Promise.resolve();
    expect(mock).toHaveBeenCalledTimes(1);
    pending[0]!.resolve(emptyCart());
    // Then the held second mount's read leaves, with the cookie the first response set.
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    expect(mock.mock.calls.map(([url]) => url)).toEqual(["/api/cart", "/api/cart"]);
  });

  it("a failed first request does not hold the others back", async () => {
    const mock = vi.fn((url: string) => (url === "/api/cart" ? Promise.reject(new TypeError("down")) : Promise.resolve(Response.json({ region: "US", total: 0, products: [] }))));
    vi.stubGlobal("fetch", mock);
    expect(await fetchCart()).toEqual({ kind: "unavailable" });
    expect((await fetchProducts({})).kind).toBe("ready");
  });
});
