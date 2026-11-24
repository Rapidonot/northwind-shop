import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Cart, CART_EMPTY, CART_UNAVAILABLE } from "./Cart";
import { fetchCart } from "./cartApi";
import type { Cart as CartData } from "./cartApi";

const CART: CartData = {
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
      unit_price: "19.99",
      line_total: "39.98",
      currency: "GBP",
      on_hand: 0,
      in_stock: false,
    },
  ],
  item_count: 2,
  subtotal: "39.98",
  currency: "GBP",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[CHK-01] Cart component", () => {
  it("shows the server's figures and marks a line that is out of stock", () => {
    render(<Cart state={{ kind: "ready", cart: CART }} />);
    expect(screen.getByText(/× 2: 39.98 GBP \(out of stock\)/)).toBeInTheDocument();
    expect(screen.getByTestId("cart-subtotal")).toHaveTextContent("Subtotal: 39.98 GBP");
  });

  it("offers no control that places an order or pays", () => {
    render(<Cart state={{ kind: "ready", cart: CART }} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByText(/check ?out|place (an )?order|pay/i)).toBeNull();
  });

  it("shows unavailable, never an empty cart, for an unavailable state", () => {
    render(<Cart state={{ kind: "unavailable" }} />);
    expect(screen.getByText(CART_UNAVAILABLE)).toBeInTheDocument();
    expect(screen.queryByText(CART_EMPTY)).toBeNull();
  });

  it("fetchCart reads GET /api/cart with the cookie and treats every failure as unavailable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ cart: CART }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchCart()).toEqual({ kind: "ready", cart: CART });
    expect(fetchMock).toHaveBeenCalledWith("/api/cart", { credentials: "same-origin" });
    for (const bad of [
      () => Promise.resolve(new Response("{}", { status: 503 })),
      () => Promise.resolve(Response.json({ cart: { lines: "x" } })),
      () => Promise.resolve(Response.json({})),
      () => Promise.reject(new Error("offline")),
    ]) {
      vi.stubGlobal("fetch", vi.fn(bad));
      expect(await fetchCart()).toEqual({ kind: "unavailable" });
    }
  });
});
