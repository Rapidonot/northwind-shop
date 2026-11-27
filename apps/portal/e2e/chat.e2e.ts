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

// SA-01 PR 2 (the user's change to Amendment 3): a suggested question replays only as the first
// message of a fresh conversation. After an answer, New conversation empties the chat and the next
// suggestion still replays, with no miss.
test.describe("[SA-01] suggested questions replay after an answer", () => {
  test("a suggestion, New conversation, then another suggestion: both answered, 0 misses", async ({ page }) => {
    await page.goto("/");
    const panel = page.getByRole("complementary", { name: "Ask Northwind" });
    const suggestions = panel.getByRole("group", { name: "Suggested questions" });
    // Wait for the whole answer, as the smoke test does: a cold first turn in replay can take tens
    // of seconds, far past expect's 5 s (39.8 s in the user's run of 2026-11-27).
    const first = page.waitForResponse("**/api/chat");
    await suggestions.getByRole("button", { name: questions.product }).click();
    const firstBody = await (await first).text();
    expect(firstBody).not.toMatch(/replay_miss/);
    expect(firstBody).toMatch(/^event: done$/m);
    await expect(panel.getByTestId("answer")).not.toBeEmpty();
    await expect(suggestions).toHaveCount(0);
    await expect(panel.getByRole("alert")).toHaveCount(0); // a replay miss would show here

    await panel.getByRole("button", { name: "New conversation" }).click();
    const reply = page.waitForResponse("**/api/chat");
    await suggestions.getByRole("button", { name: questions.care }).click();
    const body = await (await reply).text();
    expect(body).not.toMatch(/replay_miss/);
    expect(body).toMatch(/^event: done$/m);
    await expect(panel.getByLabel("Your message")).toHaveText(questions.care);
    await expect(panel.getByTestId("answer")).not.toBeEmpty();
    await expect(panel.getByRole("alert")).toHaveCount(0);
  });
});
