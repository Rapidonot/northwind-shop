import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CART_EMPTY, CART_UNAVAILABLE } from "./Cart";
import type { Cart } from "./cartApi";
import { ChatPanel } from "./ChatPanel";

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

const CART: Cart = {
  region: "UK",
  lines: [
    {
      variant_id: "P0001-BLK-REG-M",
      product_id: "P0001",
      name: "Tidewick Harbor Rain Jacket",
      colour: "Black",
      size: "M",
      fit: "regular",
      quantity: 2,
      unit_price: "89.00",
      line_total: "178.00",
      currency: "GBP",
      on_hand: 4,
      in_stock: true,
    },
  ],
  item_count: 2,
  subtotal: "178.00",
  currency: "GBP",
};

type Route = { cart: () => Response; chat?: () => Response };

function serve(route: Route) {
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve(url === "/api/cart" ? route.cart() : (route.chat ?? (() => new Response("", { status: 500 })))()),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const cartCalls = (fetchMock: ReturnType<typeof serve>) =>
  fetchMock.mock.calls.filter(([url]) => url === "/api/cart").length;

function send(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[CHK-01] cart in the chat panel", () => {
  it("shows an empty cart", async () => {
    serve({ cart: () => Response.json({ cart: { ...CART, lines: [], item_count: 0, subtotal: null, currency: null } }) });
    render(<ChatPanel />);
    expect(await screen.findByText(CART_EMPTY)).toBeInTheDocument();
  });

  it("shows each line and the subtotal exactly as the gateway sent them", async () => {
    serve({ cart: () => Response.json({ cart: CART }) });
    render(<ChatPanel />);
    const items = await screen.findByRole("list", { name: "Cart items" });
    expect(within(items).getByText(/Tidewick Harbor Rain Jacket \(Black, M, regular\) × 2: 178.00 GBP/)).toBeInTheDocument();
    expect(screen.getByTestId("cart-subtotal")).toHaveTextContent("Subtotal: 178.00 GBP");
  });

  it("marks a line that has gone out of stock", async () => {
    const line = { ...CART.lines[0], on_hand: 0, in_stock: false };
    serve({ cart: () => Response.json({ cart: { ...CART, lines: [line] } }) });
    render(<ChatPanel />);
    expect(await screen.findByText(/\(out of stock\)/)).toBeInTheDocument();
  });

  it.each([
    ["a 503", () => Response.json({ isError: true }, { status: 503 })],
    ["a body that is not a cart", () => Response.json({ cart: { lines: "x" } })],
    ["a body that is not JSON", () => new Response("<html>", { status: 200 })],
  ])("shows 'Cart unavailable', never an empty cart, for %s", async (_name, cart) => {
    serve({ cart });
    render(<ChatPanel />);
    expect(await screen.findByText(CART_UNAVAILABLE)).toBeInTheDocument();
    expect(screen.queryByText(CART_EMPTY)).not.toBeInTheDocument();
  });

  it("shows 'Cart unavailable' when the request fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("network down"))));
    render(<ChatPanel />);
    expect(await screen.findByText(CART_UNAVAILABLE)).toBeInTheDocument();
  });

  it("reads the cart again after a turn ends with done", async () => {
    const fetchMock = serve({
      cart: () => Response.json({ cart: CART }),
      chat: () =>
        new Response(frame("token", { text: "Added." }) + frame("citations", { source_ids: [] }) + frame("done", {}), {
          headers: { "Content-Type": "text/event-stream" },
        }),
    });
    render(<ChatPanel />);
    await screen.findByRole("list", { name: "Cart items" });
    expect(cartCalls(fetchMock)).toBe(1);
    send("add the black jacket in M");
    await waitFor(() => expect(cartCalls(fetchMock)).toBe(2));
  });

  it("reads the cart again after a turn ends with an error", async () => {
    const fetchMock = serve({
      cart: () => Response.json({ cart: CART }),
      chat: () =>
        new Response(frame("error", { code: "model_unavailable" }), { headers: { "Content-Type": "text/event-stream" } }),
    });
    render(<ChatPanel />);
    await screen.findByRole("list", { name: "Cart items" });
    send("add the black jacket in M");
    await waitFor(() => expect(cartCalls(fetchMock)).toBe(2));
  });

  it("never shows a cart the model wrote: answer text does not reach the panel", async () => {
    serve({
      cart: () => Response.json({ cart: { ...CART, lines: [], item_count: 0, subtotal: null, currency: null } }),
      chat: () =>
        new Response(frame("token", { text: "Your cart: 5 × Free Jacket, 0.00 GBP" }) + frame("done", {}), {
          headers: { "Content-Type": "text/event-stream" },
        }),
    });
    render(<ChatPanel />);
    await screen.findByText(CART_EMPTY);
    send("what's in my cart");
    await screen.findByText(/Free Jacket/);
    const panel = screen.getByRole("region", { name: "Cart" });
    expect(within(panel).queryByText(/Free Jacket/)).not.toBeInTheDocument();
    expect(within(panel).getByText(CART_EMPTY)).toBeInTheDocument();
  });
});
