import { aiDisclosure, expect, openChat, test } from "./journey";

// None of these sends a message the model sees: the 422 is refused by the gateway's request
// validation, before the chat handler runs.
const OVER_LENGTH = "a".repeat(2001); // MAX_MESSAGE_CHARS is 2000 (services/gateway chat.py)

test.describe("[NFR-08] AI disclosure in every journey", () => {
  test("is shown when the chat opens", async ({ page }) => {
    await openChat(page);
  });

  test("is shown again in a new session after a reload", async ({ page }) => {
    await openChat(page);
    await page.reload();
    await expect(aiDisclosure(page)).toBeVisible();
    await expect(aiDisclosure(page)).toBeInViewport();
  });

  test("stays shown after the gateway refuses a message", async ({ page }) => {
    await openChat(page);
    await page.getByLabel("Your question").fill(OVER_LENGTH);
    const reply = page.waitForResponse("**/api/chat");
    await page.getByRole("button", { name: "Send" }).click();
    expect((await reply).status()).toBe(422);
    await expect(page.getByRole("alert")).toHaveText("That message could not be accepted.");
    await expect(aiDisclosure(page)).toBeVisible();
  });
});
