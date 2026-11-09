import { expect, test } from "./journey";
import questions from "./questions.json" with { type: "json" };

test.describe("[SA-02] chat smoke", () => {
  test("a question streams text and shows a citation", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/chatting with an AI assistant/)).toBeVisible();
    await page.getByLabel("Your question").fill(questions.product);
    const reply = page.waitForResponse("**/api/chat");
    await page.getByRole("button", { name: "Send" }).click();

    const body = await (await reply).text();
    expect(body.match(/^event: token$/gm)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(body).toMatch(/^event: done$/m);

    await expect(page.getByTestId("answer")).not.toBeEmpty();
    const sources = page.getByRole("list", { name: "Sources" }).getByRole("listitem");
    await expect(sources.first()).toBeVisible();
  });
});
