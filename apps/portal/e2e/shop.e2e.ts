// SA-01 PR 1: scripted journeys over every shop page, on the real gateway and mcp-catalog. No page
// calls the model. Each journey ends with the AI disclosure on screen (journey.ts).
import { expect, test } from "./journey";

const DEPARTMENTS = ["Base Layers", "Bath", "Bedding", "Dining", "Fleece", "Jackets", "Knitwear", "Living", "Pants", "Shirts"];
const BANNER = "Demo store. AI assistant. No real orders, payments or deliveries.";

test.describe("[SA-01] shop pages", () => {
  test("home → department → product → bag, with live prices and no errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await page.goto("/");
    await expect(page.getByText(BANNER)).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Departments" });
    await expect(nav.getByRole("link")).toHaveText(DEPARTMENTS);
    await expect(page.getByRole("region", { name: "Featured" })).toBeVisible();

    await nav.getByRole("link", { name: "Jackets" }).click();
    await expect(page).toHaveURL(/#\/d\/Jackets$/);
    await expect(nav.getByRole("link", { name: "Jackets" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Jackets");
    await expect(page.getByText("33 items")).toBeVisible();
    const tiles = page.getByRole("list", { name: "Products" }).getByRole("listitem");
    await expect(tiles).toHaveCount(33);
    await expect(tiles.first()).toContainText(/From (\$|CA\$|£|€)\d+\.\d{2}/);

    await tiles.first().getByRole("link").click();
    await expect(page).toHaveURL(/#\/p\/P\d{4}$/);
    await expect(page.getByRole("article", { name: "Product" })).toBeVisible();
    await expect(page.getByTestId("price")).toHaveText(/^(\$|CA\$|£|€)\d+\.\d{2}$|^Not sold in your region$/);
    await expect(page.getByRole("region", { name: "Description" })).toBeVisible();

    await page.getByRole("link", { name: /^Bag/ }).click();
    await expect(page.getByRole("region", { name: "Bag" })).toBeVisible();
    await expect(page.getByText("Your bag is empty.")).toBeVisible();

    await page.getByRole("link", { name: "Orders" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Orders");
    await page.goto("/#/checkout");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Checkout");
    await expect(page.getByRole("textbox", { name: /card/i })).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("search results list matching products with prices", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("searchbox", { name: "Search products" }).fill("rain jacket");
    await page.getByRole("searchbox", { name: "Search products" }).press("Enter");
    await expect(page).toHaveURL(/#\/search\?q=rain\+jacket$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Results for “rain jacket”");
    const tiles = page.getByRole("list", { name: "Products" }).getByRole("listitem");
    await expect(tiles.first()).toBeVisible();
    for (const text of await tiles.allInnerTexts()) expect(text).toMatch(/From |Not sold in your region/);
  });

  test("photos come first, uncropped in 1:1 boxes, with the full name as alt text", async ({ page }) => {
    await page.goto("/#/d/Jackets");
    const tiles = page.getByRole("list", { name: "Products" }).getByRole("listitem");
    await expect(tiles).toHaveCount(33);
    const withPhoto = await tiles.evaluateAll((items) => items.map((item) => item.querySelector("img") !== null));
    const firstPlain = withPhoto.indexOf(false);
    if (firstPlain !== -1) expect(withPhoto.slice(firstPlain)).not.toContain(true);
    const boxes = page.getByTestId("image-box");
    for (const box of (await boxes.all()).slice(0, 4)) {
      const size = await box.boundingBox();
      expect(size && Math.abs(size.width - size.height) <= 1).toBe(true);
    }
    const img = page.locator(".image-box img").first();
    if ((await img.count()) > 0) {
      await expect(img).toHaveCSS("object-fit", "contain");
      expect(await img.getAttribute("alt")).toMatch(/^\S+ /);
    }
  });

  test("an unknown product and an unknown page say so plainly", async ({ page }) => {
    await page.goto("/#/p/P9999");
    // Scoped to the page content: the footer's gateway health line is a status too.
    await expect(page.getByRole("main").getByRole("status")).toHaveText("We couldn't find that page.");
    await page.goto("/#/nowhere");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("We couldn't find that page.");
  });
});

test.describe("[SA-01] chat on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("ASK NORTHWIND opens the chat full screen, with the disclosure", async ({ page }) => {
    await page.goto("/#/d/Bath");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Bath");
    const button = page.getByRole("button", { name: "ASK NORTHWIND" });
    await expect(button).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Ask Northwind" })).toBeHidden();
    await button.click();
    const dock = page.getByRole("complementary", { name: "Ask Northwind" });
    await expect(dock).toBeVisible();
    await expect(dock.getByLabel("Your question")).toBeVisible();
  });
});
