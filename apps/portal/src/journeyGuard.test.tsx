import { readdirSync, readFileSync, type Dirent } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// NFR-08: "visible in every journey test". The check lives in e2e/journey.ts's automatic fixture, so
// a journey test is covered only if it takes `test` from there. This fails if one doesn't.
const E2E_DIR = join(import.meta.dirname, "..", "e2e");

// Journey files that end on a page with no chat panel, so the disclosure can't be asserted. Each
// entry needs a reason. Empty: every page today mounts the chat.
const EXEMPT: ReadonlySet<string> = new Set<string>();

// Static imports at the start of a line, so a comment mentioning "./journey" doesn't count.
const IMPORT = /^import\s+([\s\S]*?)\s+from\s+["']([^"']+)["']/gm;
// A journey may import only these. A local helper that re-exports @playwright/test's `test` would
// skip the fixture, so any other source fails until it is reviewed and added here.
const ALLOWED_SOURCES: ReadonlySet<string> = new Set(["./journey", "./questions.json"]);
const DYNAMIC_IMPORT = /\bimport\s*\(|\brequire\s*\(/;

function importsOf(source: string): { clause: string; from: string }[] {
  return Array.from(source.matchAll(IMPORT), (m) => ({ clause: m[1] ?? "", from: m[2] ?? "" }));
}

function journeyFiles(): string[] {
  return readdirSync(E2E_DIR)
    .filter((name) => name.endsWith(".e2e.ts"))
    .sort();
}

describe("[NFR-08] every journey test asserts the disclosure", () => {
  it("finds the journey files, so an empty read can't pass", () => {
    expect(journeyFiles()).toEqual(expect.arrayContaining(["chat.e2e.ts", "disclosure.e2e.ts"]));
  });

  it.each(journeyFiles().filter((name) => !EXEMPT.has(name)))(
    "%s takes its test from ./journey, not @playwright/test",
    (name) => {
      const source = readFileSync(join(E2E_DIR, name), "utf8");
      const imports = importsOf(source);
      const fromJourney = imports.filter((i) => i.from === "./journey");
      expect(fromJourney, `${name} must import { test } from "./journey"`).toHaveLength(1);
      expect(fromJourney[0]?.clause, `${name} must take test from "./journey"`).toMatch(/\btest\b/);
      for (const { from } of imports) {
        expect(ALLOWED_SOURCES.has(from), `${name} imports "${from}", which is not allowed`).toBe(true);
      }
      expect(source, `${name} must not use a dynamic import or require`).not.toMatch(DYNAMIC_IMPORT);
    },
  );

  // Playwright's `testMatch: "*.e2e.ts"` matches in subdirectories too; the guard reads one level.
  it("finds no journey file in a subdirectory, where the guard would not look", () => {
    const entries: Dirent[] = readdirSync(E2E_DIR, { recursive: true, withFileTypes: true });
    const nested = entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".e2e.ts"))
      .filter((entry) => entry.parentPath !== E2E_DIR)
      .map((entry) => join(entry.parentPath, entry.name));
    expect(nested).toEqual([]);
  });

  it("keeps the shared fixture automatic and asserting the disclosure", () => {
    const source = readFileSync(join(E2E_DIR, "journey.ts"), "utf8");
    expect(source).toMatch(/auto:\s*true/);
    expect(source).toMatch(/getByRole\("note",\s*\{\s*name:\s*"AI disclosure"\s*\}\)/);
    // The teardown itself: after the test body (`run()`), visible and with the exact text.
    expect(source).toMatch(
      /await run\(\);\s*await expect\(aiDisclosure\(page\)\)\.toBeVisible\(\);\s*await expect\(aiDisclosure\(page\)\)\.toHaveText\(AI_DISCLOSURE\);/,
    );
    expect(source).toMatch(/^import \{ AI_DISCLOSURE \} from "\.\.\/src\/AiDisclosure";$/m);
  });
});
