import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// SA-01 "Bold utility": the look's hard rules, read from the stylesheet itself (jsdom applies no CSS,
// so a computed-style check would pass on anything).
const CSS = readFileSync(join(import.meta.dirname, "theme.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

type Rule = { selector: string; body: string };

function rules(css: string): Rule[] {
  const out: Rule[] = [];
  // Flatten one level of @media: its inner rules count like any other.
  const flat = css.replace(/@media[^{]*\{([\s\S]*?\})\s*\}/g, "$1");
  for (const match of flat.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    out.push({ selector: (match[1] ?? "").trim(), body: match[2] ?? "" });
  }
  return out;
}

const RULES = rules(CSS);
const withDecl = (pattern: RegExp) => RULES.filter((r) => pattern.test(r.body)).map((r) => r.selector);

describe("[SA-01] the Bold utility look", () => {
  it("parses the stylesheet into rules", () => {
    expect(RULES.length).toBeGreaterThan(30);
  });

  it("defines the palette", () => {
    for (const colour of ["#ffffff", "#111111", "#efefef", "#e5e5e5", "#5f5f5f"]) {
      expect(CSS.toLowerCase()).toContain(colour);
    }
  });

  it("uses the accent only for ADD TO BAG and the bag count", () => {
    const users = withDecl(/#c73e1d/i);
    expect(users.length).toBeGreaterThan(0);
    for (const selector of users) expect(selector).toMatch(/^\.(bag-count|add-to-bag)\b/);
  });

  it("has no shadow and no gradient anywhere", () => {
    expect(CSS).not.toMatch(/box-shadow|text-shadow|drop-shadow/);
    expect(CSS).not.toMatch(/gradient\(/);
  });

  it("keeps every corner between 0 and 4px", () => {
    const radii = [...CSS.matchAll(/border-radius:\s*([^;]+);/g)].map((m) => (m[1] ?? "").trim());
    expect(radii.length).toBeGreaterThan(0);
    for (const value of radii) {
      for (const part of value.split(/\s+/)) {
        expect(part).toMatch(/^(0|[0-4]px)$/);
      }
    }
  });

  it("uppercases only the wordmark and short buttons", () => {
    const users = withDecl(/text-transform:\s*uppercase/);
    expect(users.sort()).toEqual([".btn-short", ".wordmark"]);
  });

  it("sets Archivo 800 for the wordmark, Archivo 500-700 for headings and Inter for the body", () => {
    const wordmark = RULES.find((r) => r.selector === ".wordmark")?.body ?? "";
    expect(wordmark).toMatch(/font-family:\s*"Archivo"/);
    expect(wordmark).toMatch(/font-weight:\s*800/);
    const headings = RULES.filter((r) => /^(h1|h2|h3)(,|$)/.test(r.selector.split("\n")[0] ?? ""));
    for (const rule of headings) {
      const weight = /font-weight:\s*(\d+)/.exec(rule.body)?.[1];
      if (weight) expect(Number(weight)).toBeGreaterThanOrEqual(500);
      if (weight) expect(Number(weight)).toBeLessThanOrEqual(700);
    }
    expect(RULES.find((r) => r.selector === "body")?.body).toMatch(/font-family:\s*"Inter"/);
  });

  it("self-hosts both fonts", () => {
    expect(CSS).toContain('url("/fonts/archivo-latin-wght-normal.woff2")');
    expect(CSS).toContain('url("/fonts/inter-latin-wght-normal.woff2")');
    expect(CSS).not.toMatch(/https?:\/\//);
  });

  it("never crops a photo: a 1:1 box, contained, on the tile grey", () => {
    const box = RULES.find((r) => r.selector === ".image-box")?.body ?? "";
    expect(box).toMatch(/aspect-ratio:\s*1 \/ 1/);
    expect(box).toMatch(/background:\s*var\(--tile\)/);
    expect(RULES.find((r) => r.selector === ".image-box img")?.body).toMatch(/object-fit:\s*contain/);
    expect(CSS).not.toMatch(/object-fit:\s*cover/);
  });

  it("lays the grid out in 2 columns on phone and 4 on desktop", () => {
    const grids = RULES.filter((r) => r.selector === ".grid").map((r) => r.body);
    expect(grids.some((b) => /repeat\(2,/.test(b))).toBe(true);
    expect(grids.some((b) => /repeat\(4,/.test(b))).toBe(true);
  });

  // SA-01 PR 2 (home-tabs.png): the PR 1 underline became a tab filled black, as the user asked.
  it("fills the selected department tab with ink, its text on the ground colour", () => {
    const selected = RULES.find((r) => r.selector === '.departments a[aria-current="page"]')?.body ?? "";
    expect(selected).toMatch(/background:\s*var\(--ink\)/);
    expect(selected).toMatch(/color:\s*var\(--ground\)/);
  });

  it("separates product-page sections with 8px grey", () => {
    expect(RULES.find((r) => r.selector === ".product-section")?.body).toMatch(/border-top:\s*var\(--gap\) solid var\(--tile\)/);
    expect(CSS).toMatch(/--gap:\s*8px/);
  });
});
