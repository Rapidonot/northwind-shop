// SA-01 PR 2: the bag's controls, the demo checkout (CHK-02's portal confirmation control) and the
// order history, on the real gateway, mcp-catalog and mcp-orders. Pay (demo) calls no model: under
// `make e2e-scripted` the canned model counts its requests, and Pay adds none. Each journey ends with
// the AI disclosure on screen (journey.ts).
import { expect, test } from "./journey";

const SCRIPTED = process.env.E2E_MODE === "scripted";
const MODEL = "http://127.0.0.1:18431/_requests"; // the canned model's counter (scripted_model.py)

type Getter = { get(url: string): Promise<{ json(): Promise<unknown> }> };

async function modelRequests(request: Getter): Promise<number> {
  const body = (await (await request.get(MODEL)).json()) as { requests: number };
  return body.requests;
}

test.describe("[SA-01] [CHK-02] add → quantity → checkout → Pay (demo) → orders", () => {
  test("a jacket goes through the bag and the demo checkout into the order history", async ({ page, request }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));

    await page.goto("/#/p/P0004");
    await expect(page.getByRole("article", { name: "Product" })).toBeVisible();
    await page.getByRole("group", { name: "Size" }).getByRole("button").first().click();
    await page.getByRole("button", { name: "ADD TO BAG" }).click();
    await expect(page.getByText("Added to your bag.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Bag, 1 items" })).toBeVisible();

    await page.getByRole("link", { name: /^Bag/ }).click();
    const lines = page.getByRole("list", { name: "Bag items" }).getByRole("listitem");
    await expect(lines).toHaveCount(1);
    await lines.first().getByRole("button", { name: "Increase quantity" }).click();
    // Exact: the control's group and its − / + buttons also have "quantity" in their names.
    await expect(lines.first().getByLabel("Quantity", { exact: true })).toHaveText("2");
    await lines.first().getByRole("button", { name: "Decrease quantity" }).click();
    await expect(lines.first().getByLabel("Quantity", { exact: true })).toHaveText("1");

    await page.getByRole("link", { name: "PROCEED TO CHECKOUT" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Checkout");
    await expect(page.getByRole("textbox", { name: /card/i })).toHaveCount(0);
    await page.getByRole("button", { name: "Sign in (demo)" }).click();
    const pay = page.getByRole("button", { name: "Pay (demo)" });
    await expect(pay).toBeEnabled();
    const total = await page.getByTestId("checkout-total").innerText();

    const before = SCRIPTED ? await modelRequests(request) : 0;
    await pay.click();
    await expect(page.getByRole("heading", { name: "Order placed" })).toBeVisible();
    const placed = await page.getByRole("status").filter({ hasText: "is placed (demo)" }).innerText();
    expect(placed).toContain(total);
    if (SCRIPTED) expect(await modelRequests(request)).toBe(before); // condition (3): no model call

    const orderId = /O\d{6}/.exec(placed)?.[0];
    expect(orderId).toBeTruthy();
    await expect(page.getByRole("link", { name: "Bag, 0 items" })).toBeVisible();
    await page.getByRole("link", { name: "See your orders" }).click();
    const orders = page.getByRole("list", { name: "Your orders" });
    await expect(orders.getByText(orderId ?? "")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("an anonymous shopper's order history asks them to sign in", async ({ page }) => {
    await page.goto("/#/orders");
    await expect(page.getByText("Sign in to see your orders.")).toBeVisible();
    await expect(page.getByRole("list", { name: "Your orders" })).toHaveCount(0);
  });
});
