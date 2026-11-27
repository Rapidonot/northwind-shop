import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CartState } from "./cartApi";
import { CHECKOUT_UNAVAILABLE, CheckoutPage, ORDERS_UNAVAILABLE, OrdersPage, SIGN_IN_TO_PAY, SIGN_IN_TO_SEE_ORDERS } from "./checkoutPages";
import { ADDED, CartPage, ProductPage } from "./pages";
import type { Product } from "./shopApi";

// SA-01 PR 2: the bag's controls, ADD TO BAG, the demo checkout (CHK-02's portal control) and the
// order history, as the portal drives them. Every amount is the gateway's; there is no card field.

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function serve(routes: Record<string, (call: Call) => Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const call: Call = {
        url,
        method: init?.method ?? "GET",
        headers: (init?.headers as Record<string, string>) ?? {},
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      calls.push(call);
      const route = routes[`${call.method} ${url.split("?")[0]}`];
      return Promise.resolve(route ? route(call) : new Response("", { status: 503 }));
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

const PRODUCT: Product = {
  product_id: "P0004",
  name: "Brindlemoor Gale Rain Jacket",
  brand: "Brindlemoor",
  department: "Jackets",
  description: "A shell.",
  attributes: {},
  colours: ["Black"],
  sizes: ["M"],
  region: "US",
  photo: false,
  variants: [{ variant_id: "P0004-BLK-REL-M", colour: "Black", size: "M", fit: "relaxed", price: { amount: "160.00", currency: "USD" } }],
};

const LINE = {
  variant_id: "P0004-BLK-REL-M",
  product_id: "P0004",
  name: "Brindlemoor Gale Rain Jacket",
  colour: "Black",
  size: "M",
  fit: "relaxed",
  quantity: 2,
  unit_price: "160.00",
  line_total: "320.00",
  currency: "USD",
  on_hand: 9,
  in_stock: true,
};

const CART: CartState = { kind: "ready", cart: { region: "US", item_count: 2, subtotal: "320.00", currency: "USD", lines: [LINE] } };

const SUMMARY = {
  region: "US",
  lines: [
    {
      variant_id: LINE.variant_id,
      product_id: "P0004",
      name: LINE.name,
      colour: "Black",
      size: "M",
      quantity: 2,
      unit_price: { amount: "160.00", currency: "USD" },
      line_total: { amount: "320.00", currency: "USD" },
    },
  ],
  total: { amount: "320.00", currency: "USD" },
  delivery: "Standard delivery. This demo charges nothing for it and gives no date.",
  return_policy: "POL-RETURNS-US",
  issues: [] as string[],
  digest: "d".repeat(32),
  signed_in: true,
};

const letters24 = /^[a-z]{24}$/;

describe("[SA-01] ADD TO BAG", () => {
  it("posts the chosen variant with a fresh letters-only key through the portal guard, then rereads the bag", async () => {
    const calls = serve({
      "GET /api/product": () => Response.json({ product: PRODUCT }),
      "GET /api/reviews": () => Response.json({ product_id: "P0004", total: 0, reviews: [] }),
      "POST /api/cart/items": () => Response.json({ result: "added", variant_id: LINE.variant_id, quantity: 1 }),
    });
    const changed = vi.fn();
    render(<ProductPage id="P0004" onCartChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "ADD TO BAG" }));
    expect(await screen.findByText(ADDED)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/cart/items");
    expect(post?.headers).toMatchObject({ "Content-Type": "application/json", "X-Northwind-Portal": "1" });
    expect(post?.body).toMatchObject({ variant_id: LINE.variant_id, quantity: 1 });
    expect((post?.body as { idempotency_key: string }).idempotency_key).toMatch(letters24);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("shows the gateway's own refusal text, and no change", async () => {
    serve({
      "GET /api/product": () => Response.json({ product: PRODUCT }),
      "GET /api/reviews": () => Response.json({ product_id: "P0004", total: 0, reviews: [] }),
      "POST /api/cart/items": () => Response.json({ isError: true, errorCategory: "business", isRetryable: false, message: "That item is out of stock in your region.", code: "out_of_stock" }, { status: 409 }),
    });
    const changed = vi.fn();
    render(<ProductPage id="P0004" onCartChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "ADD TO BAG" }));
    expect(await screen.findByText("That item is out of stock in your region.")).toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
  });
});

describe("[SA-01] the bag's quantity controls", () => {
  function bag() {
    const calls = serve({
      "GET /api/products": () => Response.json({ region: "US", total: 0, products: [] }),
      "POST /api/cart/quantity": () => Response.json({ result: "updated", variant_id: LINE.variant_id, quantity: 1 }),
      "POST /api/cart/remove": () => Response.json({ result: "removed", variant_id: LINE.variant_id, quantity: 0 }),
    });
    const changed = vi.fn();
    render(<CartPage cart={CART} onCartChanged={changed} />);
    return { calls, changed };
  }

  it("+ and − set the quantity one up or down; Remove removes the line; each rereads the bag", async () => {
    const { calls, changed } = bag();
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Decrease quantity" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(3));
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => [c.url, (c.body as { quantity?: number }).quantity])).toEqual([
      ["/api/cart/quantity", 3],
      ["/api/cart/quantity", 1],
      ["/api/cart/remove", undefined],
    ]);
    const keys = posts.map((c) => (c.body as { idempotency_key: string }).idempotency_key);
    expect(new Set(keys).size).toBe(3);
  });

  it("− on a single item removes it", async () => {
    const calls = serve({
      "GET /api/products": () => Response.json({ region: "US", total: 0, products: [] }),
      "POST /api/cart/remove": () => Response.json({ result: "removed", variant_id: LINE.variant_id, quantity: 0 }),
    });
    render(<CartPage cart={{ kind: "ready", cart: { ...CART.cart, item_count: 1, lines: [{ ...LINE, quantity: 1 }] } }} />);
    fireEvent.click(screen.getByRole("button", { name: "Decrease quantity" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/cart/remove")).toBe(true));
  });
});

describe("[SA-01] [CHK-02] the demo checkout", () => {
  it("shows the gateway's summary: lines, total, delivery and the returns policy, and no card field", async () => {
    serve({ "GET /api/checkout/summary": () => Response.json(SUMMARY) });
    const { container } = render(<CheckoutPage />);
    const summary = await screen.findByRole("complementary", { name: "Order summary" });
    expect(within(summary).getByTestId("checkout-total")).toHaveTextContent("$320.00");
    expect(summary).toHaveTextContent("POL-RETURNS-US");
    expect(summary).toHaveTextContent(SUMMARY.delivery);
    expect(container.querySelectorAll("input, textarea, select")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Pay (demo)" })).toBeEnabled();
  });

  it("asks a signed-out shopper to sign in instead of offering Pay (demo)", async () => {
    const calls = serve({
      "GET /api/checkout/summary": () => Response.json({ ...SUMMARY, signed_in: calls.some((c) => c.url === "/api/session/shopper") }),
      "POST /api/session/shopper": () => Response.json({ signed_in: true }),
    });
    render(<CheckoutPage />);
    expect(await screen.findByText(SIGN_IN_TO_PAY)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pay (demo)" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sign in (demo)" }));
    expect(await screen.findByRole("button", { name: "Pay (demo)" })).toBeInTheDocument();
  });

  it("Pay (demo) sends the summary's digest and a fresh key, then shows the placed order", async () => {
    const calls = serve({
      "GET /api/checkout/summary": () => Response.json(SUMMARY),
      "POST /api/checkout/pay": () => Response.json({ result: "placed", order_id: "O900001", total: { amount: "320.00", currency: "USD" }, bag_emptied: true }),
    });
    const changed = vi.fn();
    render(<CheckoutPage onCartChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    expect(await screen.findByRole("heading", { name: "Order placed" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Order O900001 is placed (demo): $320.00.");
    const post = calls.find((c) => c.url === "/api/checkout/pay");
    expect(post?.body).toMatchObject({ digest: SUMMARY.digest });
    expect((post?.body as { idempotency_key: string }).idempotency_key).toMatch(letters24);
    expect(post?.headers).toMatchObject({ "X-Northwind-Portal": "1" });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("a lost last unit says no longer available and offers BE-04's substitutes by name", async () => {
    serve({
      "GET /api/checkout/summary": () => Response.json(SUMMARY),
      "POST /api/checkout/pay": () =>
        Response.json(
          {
            isError: true,
            errorCategory: "business",
            isRetryable: false,
            message: "An item in your bag is no longer available.",
            code: "no_longer_available",
            short: [{ variant_id: LINE.variant_id, wanted: 2, available: 0 }],
            substitutes: { [LINE.variant_id]: [{ variant_id: "P0004-BLK-REL-L", product_id: "P0004", reason: "other_size" }] },
          },
          { status: 409 },
        ),
      "GET /api/products": () => Response.json({ region: "US", total: 1, products: [{ product_id: "P0004", name: "Brindlemoor Gale Rain Jacket", brand: "Brindlemoor", department: "Jackets", from_price: null, photo: false }] }),
    });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("An item in your bag is no longer available.");
    const subs = within(alert).getByRole("list", { name: "Substitutes" });
    expect(await within(subs).findByRole("link", { name: "Brindlemoor Gale Rain Jacket" })).toHaveAttribute("href", "#/p/P0004");
  });

  it("a changed bag reloads the summary rather than paying a different total", async () => {
    let reads = 0;
    serve({
      "GET /api/checkout/summary": () => {
        reads += 1;
        return Response.json(reads === 1 ? SUMMARY : { ...SUMMARY, total: { amount: "300.00", currency: "USD" }, digest: "e".repeat(32) });
      },
      "POST /api/checkout/pay": () => Response.json({ isError: true, errorCategory: "business", isRetryable: false, message: "Your bag changed. Check the summary again before paying.", code: "summary_changed" }, { status: 409 }),
    });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    await waitFor(() => expect(screen.getByTestId("checkout-total")).toHaveTextContent("$300.00"));
    expect(screen.getByRole("alert")).toHaveTextContent("Your bag changed.");
  });

  it("an issue in the bag disables Pay (demo) and says what it is", async () => {
    serve({ "GET /api/checkout/summary": () => Response.json({ ...SUMMARY, issues: ["out_of_stock"] }) });
    render(<CheckoutPage />);
    expect(await screen.findByRole("list", { name: "Issues" })).toHaveTextContent("doesn't have enough stock");
    expect(screen.getByRole("button", { name: "Pay (demo)" })).toBeDisabled();
  });

  it("two presses send two different keys", async () => {
    const calls = serve({
      "GET /api/checkout/summary": () => Response.json(SUMMARY),
      "POST /api/checkout/pay": () => Response.json({ isError: true, errorCategory: "business", isRetryable: false, message: "The demo payment was declined. Nothing was charged.", code: "payment_declined" }, { status: 409 }),
    });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Pay (demo)" }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/checkout/pay")).toHaveLength(2));
    const [a, b] = calls.filter((c) => c.url === "/api/checkout/pay").map((c) => (c.body as { idempotency_key: string }).idempotency_key);
    expect(a).not.toBe(b);
  });

  it("says unavailable when the summary can't be read, never a zero total", async () => {
    serve({});
    render(<CheckoutPage />);
    expect(await screen.findByRole("status")).toHaveTextContent(CHECKOUT_UNAVAILABLE);
    expect(screen.queryByRole("button", { name: "Pay (demo)" })).toBeNull();
  });
});

describe("[SA-01] order history", () => {
  it("lists the shopper's orders with their status words and names", async () => {
    serve({
      "GET /api/orders": () =>
        Response.json({
          orders: [
            { order_id: "O900001", placed_on: "2026-11-27", status: "confirmed", status_text: "not yet with the carrier", lines: [{ product_id: "P0004", variant_id: LINE.variant_id, quantity: 1 }], total: { amount: "160.00", currency: "USD" }, demo: true },
            { order_id: "O000801", placed_on: "2026-10-02", status: "delivered", status_text: "delivered", lines: [{ product_id: "P0001", variant_id: "P0001-BLK-REG-M", quantity: 2 }] },
          ],
        }),
      "GET /api/products": () => Response.json({ region: "US", total: 1, products: [{ product_id: "P0004", name: "Brindlemoor Gale Rain Jacket", brand: "Brindlemoor", department: "Jackets", from_price: null, photo: false }] }),
    });
    render(<OrdersPage />);
    const list = await screen.findByRole("list", { name: "Your orders" });
    const orders = within(list).getAllByText(/^O\d{6}$/).map((e) => e.textContent);
    expect(orders).toEqual(["O900001", "O000801"]);
    expect(list).toHaveTextContent("2026-11-27 · not yet with the carrier");
    expect(await within(list).findByText("Brindlemoor Gale Rain Jacket × 1")).toBeInTheDocument();
    expect(within(list).getByText("P0001 × 2")).toBeInTheDocument(); // no name read: the ID, never a guess
  });

  it("asks a signed-out shopper to sign in, never shows an empty list", async () => {
    serve({ "GET /api/orders": () => Response.json({ isError: true, errorCategory: "permission", isRetryable: false, message: "Sign in to see your orders." }, { status: 401 }) });
    render(<OrdersPage />);
    expect(await screen.findByText(SIGN_IN_TO_SEE_ORDERS)).toBeInTheDocument();
    expect(screen.queryByText("You have no orders yet.")).toBeNull();
  });

  it("says unavailable when the read fails", async () => {
    serve({});
    render(<OrdersPage />);
    expect(await screen.findByRole("status")).toHaveTextContent(ORDERS_UNAVAILABLE);
  });
});
