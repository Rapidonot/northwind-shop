import { aiDisclosure, expect, openChat, test } from "./journey";

// Row #23 (SA-06, NFR-08): the shopper asks for a person and is told a person will take over. The
// AI disclosure stays (journey.ts asserts it at the end of every journey). No request here reaches
// the assistant's model; the Care agent's summary runs after the reply, in the gateway's desk.
const QUEUED = /You asked for a person\. Someone from Northwind's care team will take over this chat\./;

test.describe("[SA-06] [NFR-08] handoff to a person", () => {
  test("the button hands off at once and says a person will take over", async ({ page }) => {
    await openChat(page);
    const reply = page.waitForResponse("**/api/handoff");
    await page.getByRole("button", { name: "Talk to a person" }).click();
    const response = await reply;
    expect(response.status()).toBe(202);
    const status = page.getByRole("status", { name: "Handoff" });
    await expect(status).toHaveText(QUEUED);
    await expect(status).toContainText(/Your reference is H\d{6}\./);
    await expect(page.getByRole("button", { name: "Talk to a person" })).toBeDisabled();
    await expect(aiDisclosure(page)).toBeVisible();
  });

  test("asking twice in one session keeps the first reference", async ({ page }) => {
    await openChat(page);
    const first = page.waitForResponse("**/api/handoff");
    await page.getByRole("button", { name: "Talk to a person" }).click();
    const one = (await (await first).json()) as { reference: string };
    await page.reload();
    const second = page.waitForResponse("**/api/handoff");
    await page.getByRole("button", { name: "Talk to a person" }).click();
    const two = (await (await second).json()) as { reference: string };
    expect(two.reference).toBe(one.reference);
  });
});
