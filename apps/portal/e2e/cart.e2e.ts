import { expect, openChat, test } from "./journey";

// Row #16 (CHK-01): the cart journey in the browser, on the scripted model (`make e2e-scripted`).
// The model's tool calls are canned (services/assistant/e2e/scripted_model.py reads cart.json);
// the real mcp-catalog runs them, checking live stock, and the panel shows what GET /api/cart
// returns. One page keeps one session cookie, so the four messages act on one cart.
// The steps are inline, because a journey imports only ./journey (journeyGuard.test.tsx). They are
// cart.json's, which the scripted model reads; test_playwright_servers.py pins the two equal.
type Step = {
  name: string;
  message: string;
  answer: string;
  expect: { variant_id: string; quantity: number }[];
};

const STEPS: Step[] = [
  {
    name: "in-stock add",
    message: "Please add the Tidewick Harbor Rain Jacket in Black, regular fit, size M to my cart.",
    answer: "Done: it's in your cart.",
    expect: [
      {
        variant_id: "P0001-BLK-REG-M",
        quantity: 1
      }
    ]
  },
  {
    name: "out-of-stock add",
    message: "Please add the Brindlemoor Squall Rain Jacket in Charcoal, relaxed fit, size M to my cart.",
    answer: "That one is out of stock, so I didn't add it.",
    expect: [
      {
        variant_id: "P0001-BLK-REG-M",
        quantity: 1
      }
    ]
  },
  {
    name: "change quantity",
    message: "Change the quantity of the Tidewick Harbor Rain Jacket in Black, regular fit, size M in my cart to 2.",
    answer: "Updated: now two in your cart.",
    expect: [
      {
        variant_id: "P0001-BLK-REG-M",
        quantity: 2
      }
    ]
  },
  {
    name: "remove",
    message: "Remove the Tidewick Harbor Rain Jacket in Black, regular fit, size M from my cart.",
    answer: "Removed it from your cart.",
    expect: []
  }
];

const scripted = process.env.E2E_MODE === "scripted";

test.describe("[CHK-01] cart journey in the chat panel", () => {
  test.skip(!scripted, "the recorded cart journey is pytest's (W4)");

  test("add, refused out-of-stock add, change quantity, remove", async ({ page }) => {
    await openChat(page);
    const panel = page.getByRole("region", { name: "Cart" });
    await expect(panel.getByText("Your cart is empty.")).toBeVisible();

    for (const step of STEPS) {
      await page.getByLabel("Your question").fill(step.message);
      const reply = page.waitForResponse("**/api/chat");
      const reread = page.waitForResponse("**/api/cart");
      await page.getByRole("button", { name: "Send" }).click();
      const body = await (await reply).text();
      expect(body, step.name).toMatch(/^event: done$/m);
      expect((await reread).status(), step.name).toBe(200);
      await expect(page.getByTestId("answer"), step.name).toHaveText(step.answer);

      if (step.expect.length === 0) {
        await expect(panel.getByText("Your cart is empty."), step.name).toBeVisible();
      } else {
        await expect(panel.getByRole("listitem"), step.name).toHaveCount(step.expect.length);
        for (const line of step.expect) {
          const item = panel.locator(`[data-variant="${line.variant_id}"]`);
          await expect(item, step.name).toContainText(`× ${line.quantity}`);
        }
      }
    }
  });
});
