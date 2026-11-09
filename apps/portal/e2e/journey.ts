// NFR-08: every journey test imports `test` from here, never from @playwright/test, so every journey
// ends by asserting the AI disclosure is still on screen. `src/journeyGuard.test.tsx` enforces the
// import. A journey therefore has to end on a page that mounts the chat; a page that doesn't imports
// @playwright/test directly and goes on the guard's exemption list.
import { test as base, expect, type Page } from "@playwright/test";
import { AI_DISCLOSURE } from "../src/AiDisclosure";

export { expect };

export function aiDisclosure(page: Page) {
  return page.getByRole("note", { name: "AI disclosure" });
}

export const test = base.extend<{ aiDisclosureAtEnd: void }>({
  aiDisclosureAtEnd: [
    // Named `run`, not `use`: the react-hooks lint reads any `use(...)` call as a hook.
    async ({ page }, run) => {
      await run();
      await expect(aiDisclosure(page)).toBeVisible();
      await expect(aiDisclosure(page)).toHaveText(AI_DISCLOSURE);
    },
    { auto: true },
  ],
});

/** Opens the chat and checks the disclosure is visible without scrolling. */
export async function openChat(page: Page): Promise<void> {
  await page.goto("/");
  await expect(aiDisclosure(page)).toBeVisible();
  await expect(aiDisclosure(page)).toHaveText(AI_DISCLOSURE);
  await expect(aiDisclosure(page)).toBeInViewport();
}
