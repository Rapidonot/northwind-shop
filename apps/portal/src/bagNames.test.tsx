import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CartState } from "./cartApi";
import { CartPage } from "./pages";

// SA-01 PR 1, advisor-reviewer's second read, finding 1: trial-build rule 4 on the bag too. A cart
// line carries no brand (get_cart is the assistant's tool, unchanged), so the bag reads the brand
// from the portal's own product list.

const CART: CartState = {
  kind: "ready",
  cart: {
    region: "US",
    item_count: 1,
    subtotal: "89.00",
    currency: "USD",
    lines: [
      {
        variant_id: "P0001-BLK-REG-M",
        product_id: "P0001",
        name: "Tidewick Harbor Rain Jacket",
        colour: "Black",
        size: "M",
        fit: "regular",
        quantity: 1,
        unit_price: "89.00",
        line_total: "89.00",
        currency: "USD",
        on_hand: 3,
        in_stock: true,
      },
    ],
  },
};

afterEach(() => vi.unstubAllGlobals());

function serve(products: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(products === null ? new Response("", { status: 503 }) : Response.json({ region: "US", total: 1, products }))),
  );
}

describe("[SA-01] the bag shows the brand line once and the name without it", () => {
  it("splits the catalog name using the product's brand", async () => {
    serve([{ product_id: "P0001", name: "Tidewick Harbor Rain Jacket", brand: "Tidewick", department: "Jackets", from_price: null, photo: false }]);
    render(<CartPage cart={CART} />);
    const line = screen.getByRole("listitem");
    expect(await within(line).findByText("Harbor Rain Jacket")).toBeInTheDocument();
    expect(within(line).getAllByText("Tidewick")).toHaveLength(1);
    expect(within(line).queryByText("Tidewick Harbor Rain Jacket")).toBeNull();
  });

  it("falls back to the full name, never a guess, when the brand can't be read", async () => {
    serve(null);
    render(<CartPage cart={CART} />);
    expect(await screen.findByText("Tidewick Harbor Rain Jacket")).toBeInTheDocument();
  });
});
