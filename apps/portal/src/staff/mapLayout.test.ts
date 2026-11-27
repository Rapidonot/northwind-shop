import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixtures";
import layout from "./layout.json";
import { type Box, COLUMNS, place, route, SLOTS } from "./mapLayout";

const MAP = FIXTURE.system.map;
const IDS = MAP.nodes.map((n) => n.id);
const placed = place(IDS);
const routes = route(MAP.edges, placed);

const onEdgeOf = (b: Box, [x, y]: [number, number]) =>
  ((x === b.x || x === b.x + b.w) && y >= b.y && y <= b.y + b.h) || ((y === b.y || y === b.y + b.h) && x >= b.x && x <= b.x + b.w);

describe("[OPS-1] the agent map's layout table", () => {
  it("gives every generated node a slot, and every slot names a generated node", () => {
    expect(Object.keys(SLOTS).sort()).toEqual([...IDS].sort());
  });

  it("puts every slot in a known row and column, one node per cell", () => {
    const rows = new Set(layout.rows.map((r) => r.id));
    const cells = new Set<string>();
    for (const [id, slot] of Object.entries(SLOTS)) {
      expect(rows.has(slot.row), id).toBe(true);
      expect(slot.col >= 0 && slot.col < COLUMNS, id).toBe(true);
      const cell = `${slot.row}:${slot.col}`;
      expect(cells.has(cell), `${id} shares ${cell}`).toBe(false);
      cells.add(cell);
    }
  });

  it("makes every box the same width, lines columns up across lanes, and shares a height within a row", () => {
    const boxes = [...placed.boxes.entries()];
    expect(new Set(boxes.map(([, b]) => b.w)).size).toBe(1);
    for (const [id] of boxes) {
      const sameCol = boxes.filter(([other]) => SLOTS[other]?.col === SLOTS[id]?.col);
      expect(new Set(sameCol.map(([, o]) => o.x)).size, id).toBe(1);
      const sameRow = boxes.filter(([other]) => SLOTS[other]?.row === SLOTS[id]?.row);
      expect(new Set(sameRow.map(([, o]) => `${o.y}/${o.h}`)).size, id).toBe(1);
    }
  });

  it("keeps the lane claims true of the nodes in them", () => {
    const inRow = (row: string) => MAP.nodes.filter((n) => SLOTS[n.id]?.row === row);
    expect(inRow("checkout").some((n) => n.kind === "ai")).toBe(false); // "no AI anywhere on this path"
    expect(inRow("handoff").some((n) => n.kind === "mcp")).toBe(false); // "no MCP"
    expect(inRow("orders").concat(inRow("orders-ports")).some((n) => n.kind === "mcp")).toBe(false); // "ports in code, not MCP"
    expect(inRow("eval").every((n) => n.kind === "eval")).toBe(true);
  });
});

describe("[OPS-1] the agent map's connector lines", () => {
  it("routes a line for every generated edge", () => {
    expect(routes.map((r) => `${r.edge.from}->${r.edge.to}`).sort()).toEqual(MAP.edges.map((e) => `${e.from}->${e.to}`).sort());
  });

  it("starts each line on its source box and ends it on its target box, turning only at right angles", () => {
    for (const r of routes) {
      const name = `${r.edge.from}->${r.edge.to}`;
      const first = r.points[0]!;
      const last = r.points[r.points.length - 1]!;
      expect(onEdgeOf(placed.boxes.get(r.edge.from)!, first), `${name} start`).toBe(true);
      expect(onEdgeOf(placed.boxes.get(r.edge.to)!, last), `${name} end`).toBe(true);
      for (let i = 1; i < r.points.length; i++) {
        const [ax, ay] = r.points[i - 1]!;
        const [bx, by] = r.points[i]!;
        expect(ax === bx || ay === by, `${name} segment ${i}`).toBe(true);
      }
    }
  });

  it("routes the design's shapes: lanes straight, the assistant fanned out, the escalation looped back", () => {
    const kind = (from: string, to: string) => routes.find((r) => r.edge.from === from && r.edge.to === to)?.route;
    expect(kind("query-understanding", "shopper-assistant")).toBe("straight");
    for (const server of ["northwind-catalog", "northwind-rag", "northwind-orders", "northwind-policy"]) {
      expect(kind("shopper-assistant", server)).toBe("down");
    }
    expect(kind("query-understanding", "northwind-catalog")).toBe("down");
    expect(kind("handoff-desk", "care-subagent")).toBe("straight");
    expect(kind("checkout-capture", "checkout-confirm")).toBe("straight");
    expect(kind("orders-subagent", "order-fraud")).toBe("straight");
    expect(kind("orders-subagent", "order-payment")).toBe("over");
    expect(kind("orders-subagent", "order-inventory")).toBe("down");
    expect(kind("orders-subagent", "order-carrier")).toBe("down");
    expect(kind("escalation", "care-subagent")).toBe("loop");
  });

  it("never draws a line through a box it does not start or end at", () => {
    const crosses = (b: Box, [ax, ay]: [number, number], [bx, by]: [number, number]) => {
      if (ax === bx) return ax > b.x && ax < b.x + b.w && Math.max(ay, by) > b.y && Math.min(ay, by) < b.y + b.h;
      return ay > b.y && ay < b.y + b.h && Math.max(ax, bx) > b.x && Math.min(ax, bx) < b.x + b.w;
    };
    for (const r of routes) {
      for (const [id, b] of placed.boxes) {
        if (id === r.edge.from || id === r.edge.to) continue;
        for (let i = 1; i < r.points.length; i++) {
          expect(crosses(b, r.points[i - 1]!, r.points[i]!), `${r.edge.from}->${r.edge.to} crosses ${id}`).toBe(false);
        }
      }
    }
  });

  it("spreads lines entering one box across its top, in their sources' order", () => {
    const into = routes.filter((r) => r.edge.to === "northwind-catalog");
    const xs = into.map((r) => r.points[r.points.length - 1]![0]);
    expect(new Set(xs).size).toBe(into.length);
    const qu = into.find((r) => r.edge.from === "query-understanding")!;
    const assistant = into.find((r) => r.edge.from === "shopper-assistant")!;
    expect(qu.points[qu.points.length - 1]![0]).toBeLessThan(assistant.points[assistant.points.length - 1]![0]);
  });
});

describe("[OPS-1] the agent map's look", () => {
  const css = readFileSync(join(import.meta.dirname, "..", "theme.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: (m[1] ?? "").trim(), body: m[2] ?? "" }));

  it("greys only eval-only nodes, and dims only for a selected trace", () => {
    const greying = rules.filter((r) => /\.map-node/.test(r.selector) && /var\(--secondary\)|var\(--tile\)|opacity/.test(r.body));
    expect(greying.length).toBeGreaterThan(0);
    for (const r of greying) expect(r.selector).toMatch(/map-node-eval|is-dim/);
    const fading = rules.filter((r) => /\.map-(edge|node)/.test(r.selector) && /opacity/.test(r.body));
    for (const r of fading) expect(r.selector).toMatch(/is-dim/);
  });
});
