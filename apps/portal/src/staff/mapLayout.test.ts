import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { chipLabels } from "./AgentSystem";
import { FIXTURE } from "./fixtures";
import layout from "./layout.json";
import { type Box, chipWidth, edgeKey, laneOf, MCP_BAR, nodeText, ROUTES, SEPARATORS, SLOTS, TYPE, VIEW_BOX, wrap } from "./mapLayout";

const MAP = FIXTURE.system.map;
const IDS = MAP.nodes.map((n) => n.id);
const KEYS = MAP.edges.map(edgeKey);

const onEdgeOf = (b: Box, [x, y]: [number, number]) =>
  ((x === b.x || x === b.x + b.w) && y >= b.y && y <= b.y + b.h) || ((y === b.y || y === b.y + b.h) && x >= b.x && x <= b.x + b.w);

const crosses = (b: Box, [ax, ay]: [number, number], [bx, by]: [number, number]) => {
  if (ax === bx) return ax > b.x && ax < b.x + b.w && Math.max(ay, by) > b.y && Math.min(ay, by) < b.y + b.h;
  return ay > b.y && ay < b.y + b.h && Math.max(ax, bx) > b.x && Math.min(ax, bx) < b.x + b.w;
};

describe("[OPS-1] the agent map's layout table", () => {
  it("holds four numbers per box and two per route point, as the code reads them", () => {
    for (const [id, slot] of Object.entries(layout.slots)) {
      expect(slot.length === 4 && slot.every((n) => typeof n === "number"), id).toBe(true);
    }
    for (const [key, r] of Object.entries(layout.routes)) {
      expect(r.points.length >= 2 && r.points.every((p) => p.length === 2 && p.every((n) => typeof n === "number")), key).toBe(true);
      const label = r.label as unknown[] | null;
      if (label) expect(label.length === 3 && ["start", "middle", "end"].includes(String(label[2])), key).toBe(true);
    }
  });

  it("gives every generated node a box, and every box names a generated node", () => {
    expect(Object.keys(SLOTS).sort()).toEqual([...IDS].sort());
  });

  it("gives every generated edge a route, and every route names a generated edge", () => {
    expect(Object.keys(ROUTES).sort()).toEqual([...KEYS].sort());
  });

  it("keeps every box inside the canvas, apart from the others, and inside one lane", () => {
    const [width, height] = VIEW_BOX;
    const boxes = Object.entries(SLOTS);
    for (const [id, b] of boxes) {
      expect(b.x >= 0 && b.y >= 0 && b.x + b.w <= width && b.y + b.h <= height, id).toBe(true);
      for (const y of SEPARATORS) expect(y > b.y && y < b.y + b.h, `${id} straddles ${y}`).toBe(false);
      for (const [other, o] of boxes) {
        if (other === id) continue;
        const apart = b.x + b.w <= o.x || o.x + o.w <= b.x || b.y + b.h <= o.y || o.y + o.h <= b.y;
        expect(apart, `${id} overlaps ${other}`).toBe(true);
      }
    }
  });

  it("keeps the lane claims true of the nodes in them", () => {
    const lane = (n: number) => MAP.nodes.filter((node) => laneOf(SLOTS[node.id]!) === n);
    expect(lane(2).some((n) => n.kind === "ai")).toBe(false); // "Checkout · no AI on this path"
    expect(lane(4).every((n) => n.kind === "eval")).toBe(true); // eval only, below the last line
  });
});

describe("[OPS-1] the agent map's connector lines", () => {
  it("starts each route on its source box and ends it on its target box, turning only at right angles", () => {
    for (const edge of MAP.edges) {
      const key = edgeKey(edge);
      const points = ROUTES[key]!.points;
      expect(onEdgeOf(SLOTS[edge.from]!, points[0]!), `${key} start`).toBe(true);
      expect(onEdgeOf(SLOTS[edge.to]!, points[points.length - 1]!), `${key} end`).toBe(true);
      for (let i = 1; i < points.length; i++) {
        const [ax, ay] = points[i - 1]!;
        const [bx, by] = points[i]!;
        expect(ax === bx || ay === by, `${key} segment ${i}`).toBe(true);
      }
    }
  });

  it("never draws a line through a box it does not start or end at", () => {
    for (const edge of MAP.edges) {
      const points = ROUTES[edgeKey(edge)]!.points;
      for (const [id, b] of Object.entries(SLOTS)) {
        if (id === edge.from || id === edge.to) continue;
        for (let i = 1; i < points.length; i++) {
          expect(crosses(b, points[i - 1]!, points[i]!), `${edgeKey(edge)} crosses ${id}`).toBe(false);
        }
      }
    }
  });

  it("fans the assistant out to the four MCP servers from one bus line", () => {
    const servers = ["northwind-catalog", "northwind-rag", "northwind-orders", "northwind-policy"];
    const fan = servers.map((s) => ROUTES[`shopper-assistant->${s}`]!.points);
    const busY = fan[0]![1]![1];
    for (const points of fan) {
      expect(points[0]).toEqual(fan[0]![0]); // one exit
      expect(points[1]![1]).toBe(busY); // one bus
      expect(points[2]![1]).toBe(busY);
    }
  });

  it("routes the escalation back up the right side to the Care agent", () => {
    const loop = ROUTES["escalation->care-subagent"]!.points;
    const right = Math.max(...Object.values(SLOTS).map((b) => b.x + b.w));
    expect(loop.some(([x]) => x > right)).toBe(true);
    expect(loop[loop.length - 1]![1]).toBe(SLOTS["care-subagent"]!.y);
  });

  it("keeps every label inside the canvas", () => {
    const [width, height] = VIEW_BOX;
    for (const [key, r] of Object.entries(ROUTES)) {
      if (r.label) expect(r.label.x >= 0 && r.label.x <= width && r.label.y >= 0 && r.label.y <= height, key).toBe(true);
    }
  });
});

describe("[OPS-1] text fits its box", () => {
  it("ends every node's text above its chips, and fits the chips across the box", () => {
    for (const node of MAP.nodes) {
      const box = SLOTS[node.id]!;
      const t = nodeText(node, box);
      const labels = chipLabels(node);
      const row = labels.reduce((sum, l) => sum + chipWidth(l) + 6, -6);
      expect(row, `${node.id} chips`).toBeLessThanOrEqual(box.w - 2 * TYPE.pad);
      if (t.chipsRight) {
        // Beside the title: the title must stop short of the chips, and the text inside the box.
        const titleWidth = Math.max(...t.title.map((l) => l.length)) * t.titleSize * 0.56;
        expect(t.x + titleWidth, `${node.id}: title meets its chips`).toBeLessThanOrEqual(box.x + box.w - TYPE.pad - row - 8);
        expect(t.bottom, `${node.id}: text runs out of the box`).toBeLessThanOrEqual(box.y + box.h - 4);
      } else {
        const limit = labels.length > 0 ? t.chipY - 2 : box.y + box.h - 4;
        expect(t.bottom, `${node.id}: text runs to ${t.bottom}, room to ${limit}`).toBeLessThanOrEqual(limit);
      }
    }
  });


  it("wraps on word boundaries and never loses a word", () => {
    const text = "Removes unbacked price, stock and policy claims and planted promotions";
    const lines = wrap(text, 120, 11);
    expect(lines.length).toBeGreaterThan(1);
    expect(lines.join(" ")).toBe(text);
  });
});

describe("[OPS-1] the agent map's look", () => {
  const css = readFileSync(join(import.meta.dirname, "..", "theme.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: (m[1] ?? "").trim(), body: m[2] ?? "" }));

  it("greys only eval-only nodes, and dims only for a selected trace", () => {
    const greying = rules.filter((r) => /\.map-node/.test(r.selector) && /var\(--secondary\)|var\(--tile\)|opacity/.test(r.body));
    expect(greying.length).toBeGreaterThan(0);
    for (const r of greying) expect(r.selector).toMatch(/map-node-eval|is-dim/);
    for (const r of rules.filter((x) => /\.map-(edge|node)/.test(x.selector) && /opacity/.test(x.body))) expect(r.selector).toMatch(/is-dim/);
  });

  it("draws edges at 1.25 and lit edges at 2, with an MCP server's 3px bar", () => {
    const body = (selector: string) => rules.find((r) => r.selector === selector)?.body ?? "";
    expect(body(".staff .map-edge path")).toMatch(/stroke-width:\s*1\.25/);
    expect(body(".staff .map-edge.is-lit path")).toMatch(/stroke-width:\s*2;/);
    expect(MCP_BAR).toBe(3);
  });
});
