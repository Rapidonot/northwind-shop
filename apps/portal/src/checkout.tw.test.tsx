import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addToBag, fetchOrders, fetchSummary, newKey, payDemo, removeFromBag, setQuantity, WRITE_FAILED } from "./cartApi";
import type { CartState } from "./cartApi";
import { CheckoutPage, ORDERS_UNAVAILABLE, OrdersPage, SIGN_IN_TO_SEE_ORDERS } from "./checkoutPages";
import { CartPage, ProductPage } from "./pages";
import { resetSessionOrder } from "./session";
import type { Product } from "./shopApi";

// SA-01 PR 2, test-writer pass: boundaries and failure paths of the bag's controls, ADD TO BAG, the
// demo checkout (CHK-02: only a press of Pay (demo) confirms; never chat text) and the order history.
// Hard rules: no card field (1), amounts are the gateway's (2), one key per press (9), a failed
// read is never an empty result (10).

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown; credentials?: string };
type Reply = Response | Promise<Response>;

function serve(routes: Record<string, (call: Call) => Reply>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const call: Call = {
        url,
        method: init?.method ?? "GET",
        headers: (init?.headers as Record<string, string>) ?? {},
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
        credentials: init?.credentials,
      };
      calls.push(call);
      const route = routes[`${call.method} ${url.split("?")[0]}`];
      return Promise.resolve(route ? route(call) : new Response("", { status: 503 }));
    }),
  );
  return calls;
}

beforeEach(() => resetSessionOrder());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.location.hash = "";
});

const posts = (calls: Call[], url?: string) => calls.filter((c) => c.method === "POST" && (url === undefined || c.url === url));
const refusal = (code: string, message: string, status = 409) =>
  Response.json({ isError: true, errorCategory: "business", isRetryable: false, message, code }, { status });

const VID = "P0004-BLK-REL-M";
const money = (amount: string) => ({ amount, currency: "USD" });

const LINE = {
  variant_id: VID,
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
const cartOf = (quantity: number): CartState => ({
  kind: "ready",
  cart: { region: "US", item_count: quantity, subtotal: "x", currency: "USD", lines: [{ ...LINE, quantity }] },
});

const SUMMARY = {
  region: "US",
  lines: [{ variant_id: VID, product_id: "P0004", name: LINE.name, colour: "Black", size: "M", quantity: 2, unit_price: money("160.00"), line_total: money("320.00") }],
  total: money("320.00"),
  delivery: "Standard delivery. This demo charges nothing for it and gives no date.",
  return_policy: "POL-RETURNS-US",
  issues: [] as string[],
  digest: "d".repeat(32),
  signed_in: true,
};
const PLACED = { result: "placed", order_id: "O900001", total: money("320.00"), bag_emptied: true };

const PRODUCT: Product = {
  product_id: "P0004",
  name: LINE.name,
  brand: "Brindlemoor",
  department: "Jackets",
  description: "A shell.",
  attributes: {},
  colours: ["Black"],
  sizes: ["M", "L"],
  region: "US",
  photo: false,
  variants: [
    { variant_id: "P0004-BLK-REL-M", colour: "Black", size: "M", fit: "relaxed", price: money("160.00") },
    { variant_id: "P0004-BLK-REL-L", colour: "Black", size: "L", fit: "relaxed", price: money("160.00") },
  ],
};
const productRoutes = {
  "GET /api/products": () => Response.json({ region: "US", total: 0, products: [] }),
  "GET /api/reviews": () => Response.json({ product_id: "P0004", total: 0, reviews: [] }),
};

describe("[SA-01] cartApi: keys and write shapes", () => {
  it("newKey is 24 lowercase letters, never repeated, never card-like", () => {
    const keys = Array.from({ length: 500 }, () => newKey());
    expect(keys.every((k) => /^[a-z]{24}$/.test(k))).toBe(true);
    expect(new Set(keys).size).toBe(500);
  });

  it("every write is a same-origin JSON POST with the portal header and exactly the documented fields", async () => {
    const calls = serve({
      "POST /api/cart/items": () => Response.json({ result: "added", variant_id: VID, quantity: 1 }),
      "POST /api/cart/quantity": () => Response.json({ result: "updated", variant_id: VID, quantity: 4 }),
      "POST /api/cart/remove": () => Response.json({ result: "removed", variant_id: VID, quantity: 0 }),
      "POST /api/checkout/pay": () => Response.json(PLACED),
    });
    await addToBag(VID);
    await setQuantity(VID, 4);
    await removeFromBag(VID);
    await payDemo("a".repeat(32), "k".repeat(24));
    expect(calls.map((c) => c.method)).toEqual(["POST", "POST", "POST", "POST"]);
    for (const call of calls) {
      expect(call.credentials).toBe("same-origin");
      expect(call.headers).toEqual({ "Content-Type": "application/json", "X-Northwind-Portal": "1" });
      expect((call.body as { idempotency_key: string }).idempotency_key).toMatch(/^[A-Za-z0-9_-]{16,64}$/);
    }
    expect(Object.keys(calls[0]?.body as object).sort()).toEqual(["idempotency_key", "quantity", "variant_id"]);
    expect((calls[0]?.body as { quantity: number }).quantity).toBe(1); // ADD TO BAG defaults to one
    expect(Object.keys(calls[1]?.body as object).sort()).toEqual(["idempotency_key", "quantity", "variant_id"]);
    expect(Object.keys(calls[2]?.body as object).sort()).toEqual(["idempotency_key", "variant_id"]);
    expect(calls[3]?.body).toEqual({ digest: "a".repeat(32), idempotency_key: "k".repeat(24) });
  });

  it("a network failure, a non-JSON error and a non-JSON 200 are all a failed write with the fixed text", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    expect(await addToBag(VID)).toMatchObject({ ok: false, status: 0, message: WRITE_FAILED });
    resetSessionOrder();
    serve({ "POST /api/cart/items": () => new Response("<html>bad gateway</html>", { status: 502 }) });
    expect(await addToBag(VID)).toMatchObject({ ok: false, status: 502, message: WRITE_FAILED });
    resetSessionOrder();
    serve({ "POST /api/cart/items": () => new Response("OK", { status: 200 }) });
    expect(await addToBag(VID)).toMatchObject({ ok: false, message: WRITE_FAILED });
  });

  it("a refusal carries the gateway's message and code through, and never the request body", async () => {
    serve({ "POST /api/checkout/pay": () => refusal("on_hold", "Your order is on hold: a person will check it. Nothing was charged.") });
    const done = await payDemo("a".repeat(32), "k".repeat(24));
    expect(done).toMatchObject({ ok: false, status: 409, code: "on_hold", message: "Your order is on hold: a person will check it. Nothing was charged." });
    expect(JSON.stringify(done)).not.toContain("k".repeat(24));
  });
});

describe("[SA-01] cartApi: a failed read is never a valid empty result", () => {
  it("fetchSummary says unavailable for 503, a thrown fetch, and a body missing its digest, lines or issues", async () => {
    for (const reply of [
      () => new Response("", { status: 503 }),
      () => Response.json({ ...SUMMARY, digest: undefined }),
      () => Response.json({ ...SUMMARY, lines: "none" }),
      () => Response.json({ ...SUMMARY, issues: null }),
      () => new Response("not json", { status: 200 }),
    ]) {
      resetSessionOrder();
      serve({ "GET /api/checkout/summary": reply });
      expect(await fetchSummary()).toEqual({ kind: "unavailable" });
    }
    resetSessionOrder();
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    expect(await fetchSummary()).toEqual({ kind: "unavailable" });
  });

  it("fetchOrders tells signed-out (401), failed (503, malformed, thrown) and a genuinely empty list apart", async () => {
    const cases: Array<[() => Response, string]> = [
      [() => new Response("{}", { status: 401 }), "signed-out"],
      [() => Response.json({ signed_in: false, message: "Sign in to see your orders." }), "signed-out"],
      [() => new Response("", { status: 503 }), "unavailable"],
      [() => Response.json({ orders: null }), "unavailable"],
      [() => Response.json({ total: 0 }), "unavailable"],
      [() => new Response("nope", { status: 200 }), "unavailable"],
      [() => Response.json({ orders: [], total: 0 }), "ready"],
    ];
    for (const [reply, kind] of cases) {
      resetSessionOrder();
      serve({ "GET /api/orders": reply });
      expect((await fetchOrders()).kind).toBe(kind);
    }
    resetSessionOrder();
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    expect(await fetchOrders()).toEqual({ kind: "unavailable" });
  });
});

describe("[SA-01] ADD TO BAG: edges", () => {
  it("adds the size the shopper picked, not just the first variant", async () => {
    const calls = serve({
      ...productRoutes,
      "GET /api/product": () => Response.json({ product: PRODUCT }),
      "POST /api/cart/items": () => Response.json({ result: "added", variant_id: "P0004-BLK-REL-L", quantity: 1 }),
    });
    render(<ProductPage id="P0004" />);
    fireEvent.click(await screen.findByRole("button", { name: "L" }));
    fireEvent.click(screen.getByRole("button", { name: "ADD TO BAG" }));
    await screen.findByText("Added to your bag.");
    expect(posts(calls)[0]?.body).toMatchObject({ variant_id: "P0004-BLK-REL-L", quantity: 1 });
  });

  it("a variant with no price cannot be added, and nothing is posted", async () => {
    const calls = serve({
      ...productRoutes,
      "GET /api/product": () => Response.json({ product: { ...PRODUCT, variants: [{ ...PRODUCT.variants[0]!, price: null }] } }),
    });
    render(<ProductPage id="P0004" />);
    const button = await screen.findByRole("button", { name: "ADD TO BAG" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(posts(calls)).toHaveLength(0);
  });

  it("a double click while the first add is in flight sends one write", async () => {
    let release: (r: Response) => void = () => {};
    const calls = serve({
      ...productRoutes,
      "GET /api/product": () => Response.json({ product: PRODUCT }),
      "POST /api/cart/items": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    render(<ProductPage id="P0004" />);
    const button = await screen.findByRole("button", { name: "ADD TO BAG" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    release(Response.json({ result: "added", variant_id: VID, quantity: 1 }));
    await screen.findByText("Added to your bag.");
    expect(posts(calls)).toHaveLength(1);
  });

  it("a network failure shows the fixed text and does not announce the bag changed", async () => {
    serve({
      ...productRoutes,
      "GET /api/product": () => Response.json({ product: PRODUCT }),
    });
    const changed = vi.fn();
    render(<ProductPage id="P0004" onCartChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "ADD TO BAG" }));
    expect(await screen.findByText(WRITE_FAILED)).toBeInTheDocument();
    expect(screen.queryByText("Added to your bag.")).toBeNull();
    expect(changed).not.toHaveBeenCalled();
  });
});

describe("[SA-01] the bag's quantity controls: edges", () => {
  it("+ is disabled at the quantity limit of 10 and no write for 11 can be sent", async () => {
    const calls = serve(productRoutes);
    render(<CartPage cart={cartOf(10)} />);
    const plus = screen.getByRole("button", { name: "Increase quantity" });
    expect(plus).toBeDisabled();
    fireEvent.click(plus);
    expect(posts(calls)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Decrease quantity" })).toBeEnabled();
  });

  it("+ at 9 sends exactly 10", async () => {
    const calls = serve({ ...productRoutes, "POST /api/cart/quantity": () => Response.json({ result: "updated", variant_id: VID, quantity: 10 }) });
    render(<CartPage cart={cartOf(9)} />);
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity" }));
    await waitFor(() => expect(posts(calls, "/api/cart/quantity")).toHaveLength(1));
    expect(posts(calls)[0]?.body).toMatchObject({ variant_id: VID, quantity: 10 });
  });

  it("a live-stock refusal shows the gateway's text, still rereads the bag, and sends nothing more", async () => {
    const calls = serve({
      ...productRoutes,
      "POST /api/cart/quantity": () => refusal("insufficient_stock", "There isn't enough stock for that."),
    });
    const changed = vi.fn();
    render(<CartPage cart={cartOf(2)} onCartChanged={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity" }));
    expect(await screen.findByText("There isn't enough stock for that.")).toBeInTheDocument();
    expect(changed).toHaveBeenCalledTimes(1);
    expect(posts(calls)).toHaveLength(1);
  });

  it("all three controls are disabled while a write is in flight, so one click is one write", async () => {
    let release: (r: Response) => void = () => {};
    const calls = serve({
      ...productRoutes,
      "POST /api/cart/quantity": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    render(<CartPage cart={cartOf(3)} />);
    fireEvent.click(screen.getByRole("button", { name: "Increase quantity" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Increase quantity" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Decrease quantity" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(posts(calls)).toHaveLength(1);
    release(Response.json({ result: "updated", variant_id: VID, quantity: 4 }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled());
  });

  it("a failed Remove says so with the fixed text", async () => {
    serve(productRoutes);
    render(<CartPage cart={cartOf(1)} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByText(WRITE_FAILED)).toBeInTheDocument();
  });
});

describe("[SA-01] [CHK-02] the demo checkout: only a press confirms", () => {
  it("makes no write at all until Pay (demo) is pressed", async () => {
    const calls = serve({ "GET /api/checkout/summary": () => Response.json(SUMMARY) });
    render(<CheckoutPage />);
    await screen.findByRole("button", { name: "Pay (demo)" });
    expect(posts(calls)).toHaveLength(0);
  });

  it("a double press sends one pay request", async () => {
    let release: (r: Response) => void = () => {};
    const calls = serve({
      "GET /api/checkout/summary": () => Response.json(SUMMARY),
      "POST /api/checkout/pay": () => new Promise<Response>((resolve) => (release = resolve)),
    });
    render(<CheckoutPage />);
    const button = await screen.findByRole("button", { name: "Pay (demo)" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    release(Response.json(PLACED));
    await screen.findByRole("heading", { name: "Order placed" });
    expect(posts(calls, "/api/checkout/pay")).toHaveLength(1);
  });

  it("after the order is placed there is no Pay (demo) button left to press again", async () => {
    serve({ "GET /api/checkout/summary": () => Response.json(SUMMARY), "POST /api/checkout/pay": () => Response.json(PLACED) });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    await screen.findByRole("heading", { name: "Order placed" });
    expect(screen.queryByRole("button", { name: "Pay (demo)" })).toBeNull();
    expect(screen.getByRole("link", { name: "See your orders" })).toHaveAttribute("href", "#/orders");
  });

  it.each([
    ["payment_declined", "The demo payment was declined. Nothing was charged."],
    ["on_hold", "Your order is on hold: a person will check it. Nothing was charged."],
    ["over_order_limit", "This order is above the demo's order limit."],
    ["empty", "Your bag is empty."],
    ["not_ready", "Something in your bag can't be ordered. Check the summary."],
    ["idempotency_conflict", "That payment request was already used for a different order summary."],
  ])("a %s refusal shows the gateway's text and never says the order was placed", async (code, message) => {
    const changed = vi.fn();
    serve({ "GET /api/checkout/summary": () => Response.json(SUMMARY), "POST /api/checkout/pay": () => refusal(code, message) });
    render(<CheckoutPage onCartChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("heading", { name: "Order placed" })).toBeNull();
    expect(screen.queryByText(/is placed/)).toBeNull();
    expect(changed).not.toHaveBeenCalled(); // the bag is untouched
  });

  it("a network failure on Pay shows the fixed text, never a placed order", async () => {
    serve({ "GET /api/checkout/summary": () => Response.json(SUMMARY) });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(WRITE_FAILED);
    expect(screen.queryByRole("heading", { name: "Order placed" })).toBeNull();
  });

  it("a session that lost its sign-in (401 on Pay) shows the gateway's text and places nothing", async () => {
    serve({
      "GET /api/checkout/summary": () => Response.json(SUMMARY),
      "POST /api/checkout/pay": () => Response.json({ isError: true, errorCategory: "permission", isRetryable: false, message: "Sign in to pay (demo)." }, { status: 401 }),
    });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign in to pay (demo).");
    expect(screen.queryByRole("heading", { name: "Order placed" })).toBeNull();
  });

  it("after summary_changed the next press carries the reread summary's digest, not the stale one", async () => {
    let reads = 0;
    const calls = serve({
      "GET /api/checkout/summary": () => {
        reads += 1;
        return Response.json(reads === 1 ? SUMMARY : { ...SUMMARY, total: money("300.00"), digest: "e".repeat(32) });
      },
      "POST /api/checkout/pay": (call) =>
        (call.body as { digest: string }).digest === "d".repeat(32) ? refusal("summary_changed", "Your bag changed. Check the summary again before paying.") : Response.json(PLACED),
    });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Pay (demo)" }));
    await waitFor(() => expect(screen.getByTestId("checkout-total")).toHaveTextContent("$300.00"));
    fireEvent.click(screen.getByRole("button", { name: "Pay (demo)" }));
    await screen.findByRole("heading", { name: "Order placed" });
    expect(posts(calls, "/api/checkout/pay").map((c) => (c.body as { digest: string }).digest)).toEqual(["d".repeat(32), "e".repeat(32)]);
  });

  it.each([
    ["empty", "Your bag is empty."],
    ["not_sold_here", "An item in your bag isn't sold in your region."],
    ["over_order_limit", "This order is above the demo's order limit."],
    ["some_future_issue", "Something in your bag can't be ordered."],
  ])("the %s issue disables Pay (demo) and is said in words", async (issue, text) => {
    const calls = serve({ "GET /api/checkout/summary": () => Response.json({ ...SUMMARY, issues: [issue] }) });
    render(<CheckoutPage />);
    expect(await screen.findByRole("list", { name: "Issues" })).toHaveTextContent(text);
    const button = screen.getByRole("button", { name: "Pay (demo)" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(posts(calls)).toHaveLength(0);
  });

  it("shows the order's total exactly as the gateway sent it", async () => {
    serve({
      "GET /api/checkout/summary": () => Response.json({ ...SUMMARY, total: money("1234.50") }),
      "POST /api/checkout/pay": () => Response.json({ ...PLACED, total: money("1234.50") }),
    });
    render(<CheckoutPage />);
    expect(await screen.findByTestId("checkout-total")).toHaveTextContent(/\$1,?234\.50/);
    fireEvent.click(screen.getByRole("button", { name: "Pay (demo)" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/Order O900001 is placed \(demo\): \$1,?234\.50\./);
  });

  it("has no input of any kind and stores nothing in the browser, before and after paying", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    serve({ "GET /api/checkout/summary": () => Response.json(SUMMARY), "POST /api/checkout/pay": () => Response.json(PLACED) });
    const { container } = render(<CheckoutPage />);
    await screen.findByRole("button", { name: "Pay (demo)" });
    expect(container.querySelectorAll("input, textarea, select, iframe")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Pay (demo)" }));
    await screen.findByRole("heading", { name: "Order placed" });
    expect(container.querySelectorAll("input, textarea, select, iframe")).toHaveLength(0);
    expect(setItem).not.toHaveBeenCalled();
    expect(container.innerHTML).not.toMatch(/\b\d{13,19}\b/);
  });

  it("a signed-out shopper is never sent a pay request, even after pressing around the page", async () => {
    const calls = serve({ "GET /api/checkout/summary": () => Response.json({ ...SUMMARY, signed_in: false }) });
    render(<CheckoutPage />);
    await screen.findByRole("button", { name: "Sign in (demo)" });
    expect(screen.queryByRole("button", { name: "Pay (demo)" })).toBeNull();
    expect(posts(calls, "/api/checkout/pay")).toHaveLength(0);
  });

  it("a failed sign-in says it isn't available and does not show Pay (demo)", async () => {
    serve({ "GET /api/checkout/summary": () => Response.json({ ...SUMMARY, signed_in: false }), "POST /api/session/shopper": () => new Response("", { status: 404 }) });
    render(<CheckoutPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in (demo)" }));
    expect(await screen.findByText("Sign-in isn't available in this demo right now.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pay (demo)" })).toBeNull();
  });
});

describe("[SA-01] order history: edges", () => {
  it("a genuinely empty history says so, and is not confused with a failure or a sign-in prompt", async () => {
    serve({ "GET /api/orders": () => Response.json({ orders: [], total: 0 }) });
    render(<OrdersPage />);
    expect(await screen.findByText("You have no orders yet.")).toBeInTheDocument();
    expect(screen.queryByText(ORDERS_UNAVAILABLE)).toBeNull();
    expect(screen.queryByText(SIGN_IN_TO_SEE_ORDERS)).toBeNull();
  });

  it("a malformed answer is unavailable, never 'no orders yet'", async () => {
    serve({ "GET /api/orders": () => Response.json({ orders: "none" }) });
    render(<OrdersPage />);
    expect(await screen.findByRole("status")).toHaveTextContent(ORDERS_UNAVAILABLE);
    expect(screen.queryByText("You have no orders yet.")).toBeNull();
  });

  it("shows the count, shows a total only where the gateway sent one, and keeps the gateway's order", async () => {
    serve({
      "GET /api/orders": () =>
        Response.json({
          orders: [
            { order_id: "O900002", placed_on: "2026-11-27", status: "confirmed", status_text: "not yet with the carrier", lines: [{ product_id: "P0004", variant_id: VID, quantity: 1 }], total: money("160.00"), demo: true },
            { order_id: "O000801", placed_on: "2026-10-02", status: "delivered", status_text: "delivered", lines: [{ product_id: "P0001", variant_id: "P0001-BLK-REG-M", quantity: 2 }] },
            { order_id: "O000700", placed_on: null, status: "on_hold", status_text: "on hold", lines: [] },
          ],
        }),
    });
    render(<OrdersPage />);
    const list = await screen.findByRole("list", { name: "Your orders" });
    expect(screen.getByText("3 orders")).toBeInTheDocument();
    expect(within(list).getAllByText(/^O\d{6}$/).map((e) => e.textContent)).toEqual(["O900002", "O000801", "O000700"]);
    expect(within(list).getAllByText(/^\$/)).toHaveLength(1);
    expect(within(list).getByText("$160.00")).toBeInTheDocument();
    expect(list).toHaveTextContent("on hold"); // a missing date leaves no 'null' or 'undefined'
    expect(list.textContent).not.toMatch(/null|undefined/);
  });

  it("signing in from the history rereads it and then lists the orders", async () => {
    let signedIn = false;
    const calls = serve({
      "GET /api/orders": () => (signedIn ? Response.json({ orders: [{ order_id: "O000801", placed_on: "2026-10-02", status: "delivered", status_text: "delivered", lines: [] }] }) : new Response("{}", { status: 401 })),
      "POST /api/session/shopper": () => {
        signedIn = true;
        return Response.json({ signed_in: true });
      },
    });
    render(<OrdersPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in (demo)" }));
    expect(await screen.findByRole("list", { name: "Your orders" })).toHaveTextContent("O000801");
    expect(calls.filter((c) => c.url === "/api/orders")).toHaveLength(2);
  });
});
