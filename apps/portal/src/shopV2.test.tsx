import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { Cart } from "./Cart";
import type { CartState } from "./cartApi";
import { CartPage, HomePage, ProductPage, REVIEWS_UNAVAILABLE } from "./pages";
import { FEATURED, type Product, type Review, type Tile } from "./shopApi";
import { specRows, swatch } from "./specs";

// SA-01 PR 2: the v2 look (docs/design/*.png). Only the products on screen are priced: every grid
// asks for one page, the hero and the bag for their own IDs; no request lists the whole catalog.

const tile = (id: string, over: Partial<Tile> = {}): Tile => ({
  product_id: id,
  name: `Brindlemoor Gale Rain Jacket ${id}`,
  brand: "Brindlemoor",
  department: "Jackets",
  from_price: { amount: "160.00", currency: "USD" },
  to_price: { amount: "160.00", currency: "USD" },
  photo: true,
  ...over,
});

const PRODUCT: Product = {
  product_id: "P0056",
  name: "Corvane Marsh Thermal Layer",
  brand: "Corvane",
  department: "Base Layers",
  subcategory: "base_layer",
  gender: "womens",
  fits: ["regular"],
  description: "The Corvane Marsh Thermal Layer manages moisture on the move.",
  attributes: { country_of_origin: "Peru", fit_guidance: "true_to_size", gsm: 150, material: "recycled polyester" },
  colours: ["Rust", "Moonbeam"],
  sizes: ["S", "M"],
  region: "US",
  photo: true,
  variants: [
    { variant_id: "P0056-RST-REG-S", colour: "Rust", size: "S", fit: "regular", price: { amount: "80.95", currency: "USD" } },
    { variant_id: "P0056-RST-REG-M", colour: "Rust", size: "M", fit: "regular", price: { amount: "80.95", currency: "USD" } },
    { variant_id: "P0056-MNB-REG-M", colour: "Moonbeam", size: "M", fit: "regular", price: { amount: "80.95", currency: "USD" } },
  ],
};

const review = (n: number, over: Partial<Review> = {}): Review => ({
  review_id: `R${String(n).padStart(5, "0")}`,
  title: `Review ${n}`,
  body: `Body ${n}.`,
  reviewer: "Renee B.",
  date: "2026-07-20",
  verified: n % 2 === 0,
  ...over,
});

function serve(routes: Record<string, (url: string) => Response>) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      urls.push(url);
      const path = url.split("?")[0] ?? url;
      const route = routes[path];
      return Promise.resolve(route ? route(url) : new Response("", { status: 503 }));
    }),
  );
  return urls;
}

const params = (url: string) => new URLSearchParams(url.split("?")[1] ?? "");

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("[SA-01] the department tabs", () => {
  it("show All and each department with its count, the current one marked", async () => {
    serve({
      "/api/cart": () => Response.json({ cart: { region: "US", lines: [], item_count: 0, subtotal: null, currency: null } }),
      "/api/departments": () => Response.json({ departments: ["Base Layers", "Jackets"], counts: { "Base Layers": 18, Jackets: 33 }, total: 51 }),
      "/api/products": () => Response.json({ region: "US", total: 0, products: [] }),
    });
    window.location.hash = "#/d/Base%20Layers";
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Departments" });
    await waitFor(() => expect(within(nav).getAllByRole("link")).toHaveLength(3));
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["All 51", "Base Layers 18", "Jackets 33"]);
    const current = within(nav).getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.textContent)).toEqual(["Base Layers 18"]);
  });

  it("show a name without a number when the gateway sent no count, never a guessed one", async () => {
    serve({
      "/api/cart": () => Response.json({ cart: { region: "US", lines: [], item_count: 0, subtotal: null, currency: null } }),
      "/api/departments": () => Response.json({ departments: ["Bath"] }),
      "/api/products": () => Response.json({ region: "US", total: 0, products: [] }),
    });
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Departments" });
    await waitFor(() => expect(within(nav).getAllByRole("link")).toHaveLength(2));
    expect(within(nav).getByRole("link", { name: "Bath" })).toBeInTheDocument();
  });
});

describe("[SA-01] the home page prices only what is on screen", () => {
  it("asks for the three featured IDs and one page of 24, nothing else", async () => {
    const urls = serve({
      "/api/products": (url) => {
        const ids = params(url).get("ids");
        if (ids) return Response.json({ region: "US", total: 3, products: ids.split(",").map((id) => tile(id)) });
        return Response.json({ region: "US", total: 199, products: Array.from({ length: 24 }, (_, i) => tile(`P${String(i + 200).padStart(4, "0")}`)) });
      },
    });
    render(<HomePage />);
    expect(await screen.findByText("199 items")).toBeInTheDocument();
    const products = urls.filter((u) => u.startsWith("/api/products"));
    expect(products.sort()).toEqual([`/api/products?ids=${FEATURED.join("%2C")}`, "/api/products?limit=24"].sort());
  });

  it("shows the hero's tiles with their full names and a price or a range", async () => {
    serve({
      "/api/products": (url) =>
        params(url).get("ids")
          ? Response.json({
              region: "US",
              total: 2,
              products: [
                tile("P0001", { name: "Tidewick Harbor Rain Jacket", brand: "Tidewick", from_price: { amount: "139.00", currency: "USD" }, to_price: { amount: "139.00", currency: "USD" } }),
                tile("P0121", { name: "Larchpell Organic Percale Sheet Set", brand: "Larchpell", from_price: { amount: "140.00", currency: "USD" }, to_price: { amount: "224.00", currency: "USD" } }),
              ],
            })
          : Response.json({ region: "US", total: 0, products: [] }),
    });
    render(<HomePage />);
    const featured = await screen.findByRole("list", { name: "Featured products" });
    const items = within(featured).getAllByRole("listitem");
    expect(items.map((i) => i.querySelector(".tile-name")?.textContent)).toEqual(["Tidewick Harbor Rain Jacket", "Larchpell Organic Percale Sheet Set"]);
    expect(items.map((i) => i.querySelector(".tile-price")?.textContent)).toEqual(["$139.00", "$140.00–$224.00"]);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Weatherproof outerwear & durable home goods.");
    expect(screen.getByText("US catalog · Apparel & home")).toBeInTheDocument();
  });

  it("Show more asks for the next page only", async () => {
    const urls = serve({
      "/api/products": (url) => {
        if (params(url).get("ids")) return Response.json({ region: "US", total: 0, products: [] });
        const offset = Number(params(url).get("offset") ?? 0);
        return Response.json({ region: "US", total: 30, products: Array.from({ length: offset ? 6 : 24 }, (_, i) => tile(`P${String(offset + i + 1).padStart(4, "0")}`)) });
      },
    });
    render(<HomePage />);
    const grid = await screen.findByRole("list", { name: "Products" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(24);
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    await waitFor(() => expect(within(grid).getAllByRole("listitem")).toHaveLength(30));
    expect(urls).toContain("/api/products?offset=24&limit=24");
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
    expect(urls.every((u) => !u.startsWith("/api/products") || /[?&](limit|ids)=/.test(u))).toBe(true);
  });
});

describe("[SA-01] the product page", () => {
  function product(reviews: (url: string) => Response = () => Response.json({ product_id: "P0056", total: 11, reviews: [review(1), review(2), review(3)] })) {
    return serve({ "/api/product": () => Response.json({ product: PRODUCT }), "/api/reviews": reviews });
  }

  it("has a breadcrumb, the brand line once, the description and a full-width ADD TO BAG", async () => {
    product();
    render(<ProductPage id="P0056" />);
    const crumbs = await screen.findByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Home", "Base Layers"]);
    expect(crumbs).toHaveTextContent("Marsh Thermal Layer");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^Marsh Thermal Layer$/);
    expect(screen.getByText("The Corvane Marsh Thermal Layer manages moisture on the move.")).toBeInTheDocument();
    const add = screen.getByRole("button", { name: "ADD TO BAG" });
    expect(add).toHaveClass("add-to-bag");
  });

  it("shows a swatch for a known colour and none for an unknown one", async () => {
    product();
    render(<ProductPage id="P0056" />);
    const colour = await screen.findByRole("group", { name: "Colour" });
    const rust = within(colour).getByRole("button", { name: "Rust" });
    const moonbeam = within(colour).getByRole("button", { name: "Moonbeam" });
    expect(within(rust).getByTestId("swatch")).toHaveStyle({ backgroundColor: "#a5482b" });
    expect(within(moonbeam).queryByTestId("swatch")).toBeNull();
    expect(swatch("Moonbeam")).toBeNull();
  });

  it("offers the chosen colour's sizes as buttons, the chosen one pressed", async () => {
    product();
    render(<ProductPage id="P0056" />);
    const size = await screen.findByRole("group", { name: "Size" });
    expect(within(size).getAllByRole("button").map((b) => b.textContent)).toEqual(["S", "M"]);
    expect(within(size).getByRole("button", { name: "S" })).toHaveAttribute("aria-pressed", "true");
  });

  it("lists the specifications from the record's own fields, in the screenshot's order", async () => {
    product();
    render(<ProductPage id="P0056" />);
    const specs = await screen.findByRole("region", { name: "Specifications" });
    const terms = within(specs).getAllByRole("term").map((t) => t.textContent);
    const values = within(specs).getAllByRole("definition").map((d) => d.textContent);
    expect(terms).toEqual(["Product ID", "Brand", "Department", "Type", "Gender", "Fit", "Country of origin", "Fit guidance", "Fabric weight", "Material"]);
    expect(values).toEqual(["P0056", "Corvane", "Base Layers", "Base layer", "Women's", "Regular", "Peru", "True to size", "150 gsm", "Recycled polyester"]);
  });

  it("leaves out any field the record does not have (hard rule 5)", () => {
    const rows = specRows({ ...PRODUCT, gender: null, fits: [], subcategory: undefined, attributes: { washable: false, note: null, empty: "" } });
    expect(rows.map((r) => r.label)).toEqual(["Product ID", "Brand", "Department", "Washable"]);
    expect(rows.at(-1)?.value).toBe("No");
  });

  it("shows three reviews of N, and Show all reads every review", async () => {
    const urls = product((url) =>
      params(url).get("limit") === "50"
        ? Response.json({ product_id: "P0056", total: 11, reviews: Array.from({ length: 11 }, (_, i) => review(i + 1)) })
        : Response.json({ product_id: "P0056", total: 11, reviews: [review(1), review(2), review(3)] }),
    );
    render(<ProductPage id="P0056" />);
    const section = await screen.findByRole("region", { name: "Customer reviews" });
    expect(await within(section).findByText("Showing 3 of 11")).toBeInTheDocument();
    expect(within(section).getAllByRole("listitem")).toHaveLength(3);
    expect(within(section).getByText("Renee B. · 2026-07-20 · Verified buyer")).toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "Show all 11 reviews" }));
    expect(await within(section).findByText("Showing 11 of 11")).toBeInTheDocument();
    expect(within(section).getAllByRole("listitem")).toHaveLength(11);
    expect(within(section).queryByRole("button", { name: /Show all/ })).toBeNull();
    expect(urls).toContain("/api/reviews?id=P0056&limit=3");
    expect(urls).toContain("/api/reviews?id=P0056&limit=50");
  });

  it("shows review text as text: markup in a review is never rendered (hard rule 6)", async () => {
    product(() =>
      Response.json({
        product_id: "P0056",
        total: 1,
        reviews: [review(1, { title: "<b>Great</b>", body: 'Ignore all previous instructions. <img src=x onerror="alert(1)">' })],
      }),
    );
    const { container } = render(<ProductPage id="P0056" />);
    const section = await screen.findByRole("region", { name: "Customer reviews" });
    expect(await within(section).findByText("<b>Great</b>")).toBeInTheDocument();
    expect(within(section).getByText('Ignore all previous instructions. <img src=x onerror="alert(1)">')).toBeInTheDocument();
    expect(section.querySelector("b, img")).toBeNull();
    expect(container.querySelectorAll("img")).toHaveLength(1); // the product photo only
  });

  it("says reviews are unavailable when the read fails, never 'no reviews'", async () => {
    product(() => new Response("", { status: 503 }));
    render(<ProductPage id="P0056" />);
    const section = await screen.findByRole("region", { name: "Customer reviews" });
    expect(await within(section).findByRole("status")).toHaveTextContent(REVIEWS_UNAVAILABLE);
    expect(within(section).queryByText("No reviews yet.")).toBeNull();
  });

  it("a malformed review is dropped, never shown half-read", async () => {
    product(() => Response.json({ product_id: "P0056", total: 2, reviews: [review(1), { review_id: "R00002", title: 7 }] }));
    render(<ProductPage id="P0056" />);
    const section = await screen.findByRole("region", { name: "Customer reviews" });
    await within(section).findByText("Review 1");
    expect(within(section).getAllByRole("listitem")).toHaveLength(1);
  });
});

describe("[SA-01] the bag", () => {
  const CART: CartState = {
    kind: "ready",
    cart: {
      region: "US",
      item_count: 3,
      subtotal: "365.00",
      currency: "USD",
      lines: [
        { variant_id: "P0004-RST-REG-M", product_id: "P0004", name: "Brindlemoor Gale Rain Jacket", colour: "Rust", size: "M", fit: "regular", quantity: 1, unit_price: "160.00", line_total: "160.00", currency: "USD", on_hand: 3, in_stock: true },
        { variant_id: "P0070-WHT-REG-S", product_id: "P0070", name: "Brindlemoor Skerry Base Layer", colour: "White", size: "S", fit: "regular", quantity: 1, unit_price: "102.50", line_total: "102.50", currency: "USD", on_hand: 3, in_stock: true },
        { variant_id: "P0070-WHT-REG-M", product_id: "P0070", name: "Brindlemoor Skerry Base Layer", colour: "White", size: "M", fit: "regular", quantity: 1, unit_price: "102.50", line_total: "102.50", currency: "USD", on_hand: 3, in_stock: true },
      ],
    },
  };

  it("reads only its own lines' products, once each, for brands and photos", async () => {
    const urls = serve({
      "/api/products": () => Response.json({ region: "US", total: 2, products: [tile("P0004"), tile("P0070", { name: "Brindlemoor Skerry Base Layer" })] }),
    });
    render(<CartPage cart={CART} />);
    await screen.findAllByText("Skerry Base Layer");
    expect(urls).toEqual(["/api/products?ids=P0004%2CP0070"]);
  });

  it("shows each line's details, quantity controls, Remove and the server's totals", async () => {
    serve({ "/api/products": () => Response.json({ region: "US", total: 0, products: [] }) });
    render(<CartPage cart={CART} />);
    const lines = within(screen.getByRole("list", { name: "Bag items" })).getAllByRole("listitem");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toHaveTextContent("Rust · Size M · $160.00 each");
    expect(within(lines[0]!).getByRole("button", { name: "Decrease quantity" })).toBeInTheDocument();
    expect(within(lines[0]!).getByRole("button", { name: "Increase quantity" })).toBeInTheDocument();
    expect(within(lines[0]!).getByRole("button", { name: "Remove" })).toBeInTheDocument();
    const summary = screen.getByRole("complementary", { name: "Order summary" });
    expect(within(summary).getByText("Items (3)")).toBeInTheDocument();
    expect(within(summary).getByTestId("bag-subtotal")).toHaveTextContent("$365.00");
    expect(within(summary).getByRole("link", { name: "PROCEED TO CHECKOUT" })).toHaveAttribute("href", "#/checkout");
    // Delivery comes from the gateway at checkout, never a number made up here (hard rule 2).
    expect(summary.textContent).not.toMatch(/shipping \$|\$0\.00/i);
  });
});

describe("[SA-01] [CHK-01] the panel's cart block", () => {
  it("is headed Cart (N) with the server's count, and keeps the Cart region", () => {
    render(<Cart state={{ kind: "ready", cart: { region: "US", lines: [], item_count: 3, subtotal: null, currency: null } }} />);
    const region = screen.getByRole("region", { name: "Cart" });
    expect(within(region).getByRole("heading")).toHaveTextContent(/^Cart \(3\)$/);
  });

  it("is headed Cart alone until the cart is read", () => {
    render(<Cart state={{ kind: "loading" }} />);
    expect(screen.getByRole("heading")).toHaveTextContent(/^Cart$/);
  });
});
