import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, BANNER } from "./App";
import { CartPage, DepartmentPage, NO_RESULTS, NOT_FOUND, ProductPage, SHOP_UNAVAILABLE } from "./pages";
import { ProductTile } from "./ProductTile";
import { href, parseHash } from "./router";
import { displayName, formatMoney, type Product, type Tile } from "./shopApi";

const DEPARTMENTS = ["Base Layers", "Bath", "Bedding", "Dining", "Fleece", "Jackets", "Knitwear", "Living", "Pants", "Shirts"];

const tile = (id: string, over: Partial<Tile> = {}): Tile => ({
  product_id: id,
  name: `Tidewick Harbor Rain Jacket ${id}`,
  brand: "Tidewick",
  department: "Jackets",
  from_price: { amount: "89.00", currency: "USD" },
  photo: true,
  ...over,
});

const PRODUCT: Product = {
  product_id: "P0001",
  name: "Tidewick Harbor Rain Jacket",
  brand: "Tidewick",
  department: "Jackets",
  description: "A waterproof shell.",
  attributes: { waterproof_rating_mm: 10000, seam_sealed: true },
  colours: ["Black", "Olive"],
  sizes: ["S", "M"],
  region: "US",
  photo: true,
  variants: [
    { variant_id: "P0001-BLK-REG-S", colour: "Black", size: "S", fit: "regular", price: { amount: "89.00", currency: "USD" } },
    { variant_id: "P0001-BLK-REG-M", colour: "Black", size: "M", fit: "regular", price: { amount: "91.50", currency: "USD" } },
    { variant_id: "P0001-OLV-REG-M", colour: "Olive", size: "M", fit: "regular", price: null },
  ],
};

type Handler = (url: string) => Response | Promise<Response>;

function serve(handler: Handler) {
  const mock = vi.fn((url: string) => Promise.resolve(handler(url)));
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("[SA-01] routes", () => {
  it.each([
    ["", { page: "home" }],
    ["#/", { page: "home" }],
    ["#/d/Base%20Layers", { page: "department", name: "Base Layers" }],
    ["#/search?q=rain%20jacket", { page: "search", q: "rain jacket" }],
    ["#/p/P0001", { page: "product", id: "P0001" }],
    ["#/cart", { page: "cart" }],
    ["#/checkout", { page: "checkout" }],
    ["#/orders", { page: "orders" }],
  ])("%s", (hash, route) => {
    expect(parseHash(hash)).toEqual(route);
  });

  it.each(["#/p/P1", "#/p/../x", "#/d/%E0%A4%A", "#/cart/extra", "#/admin", "#/p/P0001/x"])("%s is not found", (hash) => {
    expect(parseHash(hash)).toEqual({ page: "not-found" });
  });

  it("round-trips its own links", () => {
    for (const route of [
      { page: "department", name: "Base Layers" },
      { page: "search", q: "wool & silk" },
      { page: "product", id: "P0042" },
    ] as const) {
      expect(parseHash(href(route))).toEqual(route);
    }
  });
});

describe("[SA-01] names and prices", () => {
  it("drops the leading brand from the name, once", () => {
    expect(displayName("Tidewick", "Tidewick Harbor Rain Jacket")).toBe("Harbor Rain Jacket");
    expect(displayName("Tidewick", "Tidewickish Coat")).toBe("Tidewickish Coat");
    expect(displayName("Tidewick", "Tidewick")).toBe("Tidewick");
    expect(displayName("", "Plain Name")).toBe("Plain Name");
  });

  it("shows the server's amount as sent, in its currency", () => {
    expect(formatMoney({ amount: "89.00", currency: "USD" })).toBe("$89.00");
    expect(formatMoney({ amount: "189.04", currency: "CAD" })).toBe("CA$189.04");
    expect(formatMoney({ amount: "70.10", currency: "GBP" })).toBe("£70.10");
    expect(formatMoney({ amount: "130.66", currency: "EUR" })).toBe("€130.66");
    expect(formatMoney({ amount: "5.00", currency: "JPY" })).toBe("5.00 JPY");
  });
});

describe("[SA-01] product tile", () => {
  it("shows the brand line once and the name without it; the alt text is the full name", () => {
    render(<ul><ProductTile tile={tile("P0001")} /></ul>);
    const item = screen.getByRole("listitem");
    expect(within(item).getAllByText("Tidewick")).toHaveLength(1);
    expect(within(item).getByText("Harbor Rain Jacket P0001")).toBeInTheDocument();
    const img = within(item).getByRole("img");
    expect(img).toHaveAttribute("alt", "Tidewick Harbor Rain Jacket P0001");
    expect(img).toHaveAttribute("src", "/products/800/P0001.webp");
    expect(img.closest(".image-box")).not.toBeNull();
  });

  it("is a plain tile with no image when there is no photo", () => {
    render(<ul><ProductTile tile={tile("P0002", { photo: false })} /></ul>);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByTestId("image-box")).toBeEmptyDOMElement();
  });

  it("carries no badge or eyebrow label", () => {
    render(<ul><ProductTile tile={tile("P0001")} /></ul>);
    expect(screen.getByRole("listitem").textContent).not.toMatch(/live price|in stock|out of stock|new|sale|★|stars?|bestseller|limited/i);
  });
});

describe("[SA-01] department page", () => {
  it("has one heading and N items, and no picker of its own", async () => {
    serve(() => Response.json({ region: "US", total: 3, products: [tile("P0001"), tile("P0002"), tile("P0003")] }));
    render(<DepartmentPage name="Jackets" />);
    expect(await screen.findByText("3 items")).toBeInTheDocument();
    expect(screen.getAllByRole("heading")).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Jackets" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(within(screen.getByRole("list", { name: "Products" })).getAllByRole("listitem")).toHaveLength(3);
  });

  it("asks the gateway for its department only", async () => {
    const mock = serve(() => Response.json({ region: "US", total: 0, products: [] }));
    render(<DepartmentPage name="Base Layers" />);
    expect(await screen.findByText(NO_RESULTS)).toBeInTheDocument();
    expect(mock).toHaveBeenCalledWith("/api/products?department=Base+Layers", { credentials: "same-origin" });
  });

  it("says unavailable, never an empty list, when the read fails", async () => {
    serve(() => new Response("", { status: 503 }));
    render(<DepartmentPage name="Jackets" />);
    expect(await screen.findByRole("status")).toHaveTextContent(SHOP_UNAVAILABLE);
    expect(screen.queryByText(NO_RESULTS)).toBeNull();
  });
});

describe("[SA-01] product page", () => {
  it("shows the selected variant's live price, the facts and the hero image, uncropped", async () => {
    serve(() => Response.json({ product: PRODUCT }));
    render(<ProductPage id="P0001" />);
    expect(await screen.findByTestId("price")).toHaveTextContent("$89.00");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Harbor Rain Jacket");
    expect(screen.getByRole("img")).toHaveAttribute("src", "/products/1600/P0001.webp");
    expect(screen.getByRole("img")).toHaveAttribute("alt", "Tidewick Harbor Rain Jacket");
    expect(screen.getByText("A waterproof shell.")).toBeInTheDocument();
    expect(screen.getByText("waterproof rating mm")).toBeInTheDocument();
    screen.getByRole("button", { name: "M" }).click();
    await waitFor(() => expect(screen.getByTestId("price")).toHaveTextContent("$91.50"));
    screen.getByRole("button", { name: "Olive" }).click();
    await waitFor(() => expect(screen.getByTestId("price")).toHaveTextContent("Not sold in your region"));
  });

  it("has no stock badge, star average or live-price badge", async () => {
    serve(() => Response.json({ product: PRODUCT }));
    const { container } = render(<ProductPage id="P0001" />);
    await screen.findByTestId("price");
    expect(container.textContent).not.toMatch(/live price|in stock|out of stock|only \d+ left|★|stars?|rating:/i);
  });

  it("an unknown product is not found; a failed read is unavailable", async () => {
    serve(() => new Response("{}", { status: 404 }));
    const { unmount } = render(<ProductPage id="P9999" />);
    expect(await screen.findByRole("status")).toHaveTextContent(NOT_FOUND);
    unmount();
    serve(() => new Response("", { status: 503 }));
    render(<ProductPage id="P0001" />);
    expect(await screen.findByRole("status")).toHaveTextContent(SHOP_UNAVAILABLE);
  });
});

describe("[SA-01] bag page (read-only in PR 1)", () => {
  it("lists the lines and the subtotal as the server sent them", () => {
    render(
      <CartPage
        cart={{
          kind: "ready",
          cart: {
            region: "US",
            item_count: 2,
            subtotal: "178.00",
            currency: "USD",
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
                currency: "USD",
                on_hand: 4,
                in_stock: true,
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("2 items")).toBeInTheDocument();
    expect(screen.getByText("Qty 2")).toBeInTheDocument();
    expect(screen.getByTestId("bag-subtotal")).toHaveTextContent("$178.00");
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it("says unavailable, never empty, when the cart read failed", () => {
    render(<CartPage cart={{ kind: "unavailable" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("The bag isn't available right now.");
    expect(screen.queryByText("Your bag is empty.")).toBeNull();
  });
});

describe("[SA-01] the shell", () => {
  function shop() {
    return serve((url) => {
      if (url === "/api/departments") return Response.json({ departments: DEPARTMENTS });
      if (url === "/api/healthz") return Response.json({ status: "ok", db: "ok", pgvector: "0.8.1" });
      if (url === "/api/cart") {
        return Response.json({ cart: { region: "US", lines: [], item_count: 3, subtotal: null, currency: null } });
      }
      if (url.startsWith("/api/products")) return Response.json({ region: "US", total: 2, products: [tile("P0001"), tile("P0002")] });
      return new Response("", { status: 503 });
    });
  }

  it("shows the banner, the wordmark and all ten departments in the top nav", async () => {
    shop();
    render(<App />);
    expect(screen.getByText(BANNER)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "NORTHWIND" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Departments" });
    await waitFor(() => expect(within(nav).getAllByRole("link")).toHaveLength(10));
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(DEPARTMENTS);
  });

  it("marks only the selected department, and the department page has no chip row", async () => {
    shop();
    window.location.hash = "#/d/Jackets";
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Departments" });
    await waitFor(() => expect(within(nav).getByRole("link", { name: "Jackets" })).toHaveAttribute("aria-current", "page"));
    expect(within(nav).getAllByRole("link").filter((a) => a.hasAttribute("aria-current"))).toHaveLength(1);
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    expect(await screen.findByText("2 items")).toBeInTheDocument();
  });

  it("shows the bag count from the cart read, and only it carries the accent class", async () => {
    shop();
    const { container } = render(<App />);
    expect(await screen.findByRole("link", { name: "Bag, 3 items" })).toBeInTheDocument();
    const accented = container.querySelectorAll(".bag-count, .add-to-bag");
    expect(Array.from(accented).map((e) => e.className)).toEqual(["bag-count"]);
  });

  it("offers the chat as an ASK NORTHWIND button and a panel with the AI disclosure", async () => {
    shop();
    render(<App />);
    const button = screen.getByRole("button", { name: "ASK NORTHWIND" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.click();
    await waitFor(() => expect(button).toHaveAttribute("aria-expanded", "true"));
    const dock = screen.getByRole("complementary", { name: "Ask Northwind" });
    expect(within(dock).getByRole("note", { name: "AI disclosure" })).toBeInTheDocument();
  });

  it("uppercases only the wordmark and short buttons in its copy", () => {
    shop();
    render(<App />);
    const upper = screen.getAllByText(/^[A-Z][A-Z ]{2,}$/).map((e) => e.textContent);
    expect(upper.sort()).toEqual(["ASK NORTHWIND", "CLOSE", "NORTHWIND"]);
  });
});
