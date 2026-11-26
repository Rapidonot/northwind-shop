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

  test("a typed request for a person hands off with no answer from the model", async ({ page }) => {
    await openChat(page);
    await page.getByLabel("Your question").fill("Can I talk to a real person?");
    const chat = page.waitForResponse("**/api/chat");
    const handoff = page.waitForResponse("**/api/handoff");
    await page.getByRole("button", { name: "Send" }).click();
    const body = await (await chat).text();
    // The gateway's trigger answered before any search or model call: no token, no citations.
    expect(body).toMatch(/^event: handoff$/m);
    expect(body).toMatch(/^event: done$/m);
    expect(body).not.toMatch(/^event: (token|citations)$/m);
    expect((await handoff).status()).toBe(202);
    await expect(page.getByRole("status", { name: "Handoff" })).toHaveText(QUEUED);
    await expect(page.getByTestId("answer")).toBeEmpty();
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
