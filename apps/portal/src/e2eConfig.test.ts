import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import config from "../playwright.config";

// SA-01 PR 2: two browser-run findings (2026-11-27), pinned on the files themselves because the
// sandbox can't run a browser.
// 1. The canned model counts every request it receives. Pay (demo)'s "no model call" check reads
//    that count before and after Pay, so no other journey may talk to the model meanwhile: the
//    checkout journey runs in its own project, after every other journey has finished.
// 2. A replayed answer can take tens of seconds on a cold first turn (39.8 s in the user's run),
//    far past `expect`'s 5 s: each suggestion waits for its whole /api/chat response, as the smoke
//    test does, before the answer is checked.

const E2E = join(import.meta.dirname, "..", "e2e");

describe("[SA-01] the checkout journey runs alone", () => {
  const projects = config.projects ?? [];

  it("has a project with only checkout.e2e.ts, which depends on the project with every other journey", () => {
    const checkout = projects.find((p) => p.name === "checkout");
    const journeys = projects.find((p) => p.name === "journeys");
    expect(checkout?.testMatch).toEqual(/checkout\.e2e\.ts$/);
    expect(checkout?.dependencies).toEqual(["journeys"]);
    expect(journeys?.testIgnore).toEqual(/checkout\.e2e\.ts$/);
    expect(journeys?.dependencies ?? []).toEqual([]);
    expect(projects.map((p) => p.name).sort()).toEqual(["checkout", "journeys"]);
  });

  it("puts every journey file in exactly one project", () => {
    const files = ["cart", "chat", "checkout", "disclosure", "handoff", "shop"].map((n) => `${n}.e2e.ts`);
    for (const file of files) {
      const inCheckout = (projects.find((p) => p.name === "checkout")?.testMatch as RegExp).test(file);
      const ignored = (projects.find((p) => p.name === "journeys")?.testIgnore as RegExp).test(file);
      expect(inCheckout).toBe(ignored); // in checkout exactly when journeys leaves it out
    }
  });
});

describe("[SA-01] the suggestion journey waits for each whole answer", () => {
  const source = readFileSync(join(E2E, "chat.e2e.ts"), "utf8");
  const journey = source.slice(source.indexOf("suggested questions replay after an answer"));

  it("waits for the /api/chat response of both suggestions and checks it ended without a miss", () => {
    expect(journey.match(/waitForResponse\("\*\*\/api\/chat"\)/g)).toHaveLength(2);
    expect(journey.match(/not\.toMatch\(\/replay_miss\/\)/g)).toHaveLength(2);
    expect(journey.match(/toMatch\(\/\^event: done\$\/m\)/g)).toHaveLength(2);
  });

  it("starts waiting before each click, so no response can be missed", () => {
    for (const question of ["questions.product", "questions.care"]) {
      const click = journey.indexOf(`name: ${question} }).click()`);
      const wait = journey.lastIndexOf('waitForResponse("**/api/chat")', click);
      expect(click).toBeGreaterThan(-1);
      expect(wait).toBeGreaterThan(-1);
      expect(journey.slice(wait, click)).not.toMatch(/\.click\(\)/);
    }
  });
});
