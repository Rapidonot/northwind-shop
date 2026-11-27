import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CartPage, DepartmentPage, ProductPage } from "./pages";
import { ProductTile } from "./ProductTile";
import type { Product, Tile } from "./shopApi";

const tile = (id: string, over: Partial<Tile> = {}): Tile => ({
  product_id: id,
  name: `Tidewick Rain Jacket ${id}`,
  brand: "Tidewick",
  department: "Jackets",
  from_price: { amount: "89.00", currency: "USD" },
  photo: false,
  ...over,
});

const product = (over: Partial<Product> = {}): Product => ({
  product_id: "P0001",
  name: "Tidewick Rain Jacket",
  brand: "Tidewick",
  department: "Jackets",
  description: "A waterproof shell.",
  attributes: {},
  colours: ["Black"],
  sizes: ["S", "M"],
  region: "US",
  photo: false,
  variants: [
    { variant_id: "P0001-BLK-REG-S", colour: "Black", size: "S", fit: "regular", price: { amount: "89.999", currency: "USD" } },
    { variant_id: "P0001-BLK-REG-M", colour: "Black", size: "M", fit: "regular", price: { amount: "0.10", currency: "USD" } },
  ],
  ...over,
});

function serve(body: unknown, status = 200) {
  const mock = vi.fn(() => Promise.resolve(Response.json(body, { status })));
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("[SA-01] a price is shown as sent, never computed in the browser", () => {
  it.each([
    ["89.999", "$89.999"],
    ["0.10", "$0.10"],
    ["1234.50", "$1234.50"],
    ["7", "$7"],
    ["100.00", "$100.00"],
  ])("a tile's from-price %s is %s", (amount, shown) => {
    render(
      <ul>
        <ProductTile tile={tile("P0001", { from_price: { amount, currency: "USD" } })} />
      </ul>,
    );
    expect(screen.getByRole("listitem")).toHaveTextContent(`From ${shown}`);
  });

  it("a tile with no price says so and shows no amount or currency symbol", () => {
    render(
      <ul>
        <ProductTile tile={tile("P0001", { from_price: null })} />
      </ul>,
    );
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("Not sold in your region");
    expect(item.querySelector(".tile-price")?.textContent).not.toMatch(/[$£€]|\d/);
  });

  it("a department page shows each tile's own amount, in the order sent, and invents no lowest", async () => {
    serve({
      region: "US",
      total: 3,
      products: [
        tile("P0001", { from_price: { amount: "300.00", currency: "USD" } }),
        tile("P0002", { from_price: { amount: "20.00", currency: "USD" } }),
        tile("P0003", { from_price: null }),
      ],
    });
    render(<DepartmentPage name="Jackets" />);
    const items = await screen.findAllByRole("listitem");
    expect(items.map((i) => i.querySelector(".tile-price")?.textContent)).toEqual([
      "From $300.00",
      "From $20.00",
      "Not sold in your region",
    ]);
  });

  it("the product page shows each variant's amount exactly, with no rounding, sum or tax", async () => {
    serve({ product: product() });
    const { container } = render(<ProductPage id="P0001" />);
    expect(await screen.findByTestId("price")).toHaveTextContent("$89.999");
    screen.getByRole("button", { name: "M" }).click();
    await waitFor(() => expect(screen.getByTestId("price")).toHaveTextContent("$0.10"));
    expect(container.textContent).not.toMatch(/\$(90\.00|0\.1(?!0)|89\.9[^9]|90)\b/);
    expect(container.textContent).not.toMatch(/tax|total|save|was \$/i);
  });

  it("an unpriced variant shows no amount, even when another variant has one", async () => {
    serve({
      product: product({
        variants: [
          { variant_id: "P0001-BLK-REG-S", colour: "Black", size: "S", fit: "regular", price: { amount: "50.00", currency: "USD" } },
          { variant_id: "P0001-BLK-REG-M", colour: "Black", size: "M", fit: "regular", price: null },
        ],
      }),
    });
    render(<ProductPage id="P0001" />);
    expect(await screen.findByTestId("price")).toHaveTextContent("$50.00");
    screen.getByRole("button", { name: "M" }).click();
    await waitFor(() => expect(screen.getByTestId("price")).toHaveTextContent("Not sold in your region"));
    expect(screen.getByTestId("price").textContent).not.toMatch(/\d/);
  });

  it("a product with no priced variant at all shows no price anywhere", async () => {
    serve({ product: product({ variants: [{ variant_id: "P0010-BLK-REG-S", colour: "Black", size: "S", fit: null, price: null }] }) });
    render(<ProductPage id="P0001" />);
    expect(await screen.findByTestId("price")).toHaveTextContent("Not sold in your region");
    expect(screen.getByTestId("price").textContent).not.toMatch(/[$£€]|\d/);
  });

  it("prices in other currencies keep the server's currency", async () => {
    serve({
      product: product({
        region: "UK",
        variants: [{ variant_id: "P0001-BLK-REG-S", colour: "Black", size: "S", fit: null, price: { amount: "70.10", currency: "GBP" } }],
      }),
    });
    render(<ProductPage id="P0001" />);
    expect(await screen.findByTestId("price")).toHaveTextContent("£70.10");
    expect(screen.getByTestId("price").textContent).not.toMatch(/\$/);
  });

  it("a price in the description is text, not the price: the price element holds the sent amount only", async () => {
    serve({ product: product({ description: "Now only $1.11 (price band $10-$20), 3 in stock." }) });
    render(<ProductPage id="P0001" />);
    const price = await screen.findByTestId("price");
    expect(price.textContent).not.toMatch(/1\.11|10|20|stock/);
    expect(price).toHaveTextContent("$89.999");
  });

  it("the bag shows the server's line and subtotal amounts as sent, even when they do not add up", () => {
    render(
      <CartPage
        cart={{
          kind: "ready",
          cart: {
            region: "US",
            item_count: 3,
            subtotal: "29.99",
            currency: "USD",
            lines: [
              {
                variant_id: "P0001-BLK-REG-M",
                product_id: "P0001",
                name: "Tidewick Rain Jacket",
                colour: "Black",
                size: "M",
                fit: "regular",
                quantity: 3,
                unit_price: "10.00",
                line_total: "29.99",
                currency: "USD",
                on_hand: 5,
                in_stock: true,
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByTestId("bag-subtotal")).toHaveTextContent("$29.99");
    const text = document.body.textContent ?? "";
    expect(text).toContain("$29.99");
    expect(text).not.toContain("$30.00");
    expect(within(document.body).queryByText(/\$30(\.00)?\b/)).toBeNull();
  });
});
