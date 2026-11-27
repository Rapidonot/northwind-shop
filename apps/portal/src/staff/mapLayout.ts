// OPS-1: the agent map's geometry. Pure functions over the layout table (layout.json) and the
// generated edges: where each box sits, and an elbow-routed SVG path for every edge. No DOM
// measuring, so the tests can check every route without a browser.
import layout from "./layout.json";
import type { MapEdge } from "./types";

export interface Slot {
  row: string;
  col: number;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface RowBand {
  id: string;
  title: string | null;
  /** Where the row's title sits, when it has one. */
  titleY: number;
  top: number;
  height: number;
}

export interface Placed {
  boxes: Map<string, Box>;
  rows: RowBand[];
  /** The grid's width; the canvas adds the right gutter the loop-back edges run in. */
  width: number;
  canvasWidth: number;
  height: number;
}

export type Route = "straight" | "over" | "down" | "step" | "loop";

export interface Routed {
  edge: MapEdge;
  route: Route;
  points: [number, number][];
  label: { x: number; y: number; anchor: "start" | "middle" | "end" } | null;
}

export const SLOTS = layout.slots as Record<string, Slot>;
export const COLUMNS = layout.columns;
const { width: W, gap_x: GX, gap_y: GY, title_band: BAND, gutter: GUTTER, top: TOP } = layout.box;

export function rowsOf(): RowBand[] {
  const rows: RowBand[] = [];
  let y = TOP;
  for (const row of layout.rows) {
    const titleY = y;
    if (row.title !== null) y += BAND;
    rows.push({ id: row.id, title: row.title, titleY, top: y, height: row.height });
    y += row.height + GY;
  }
  return rows;
}

/** Places every node that has a slot; nodes without one are left out (the tests forbid that). */
export function place(nodeIds: readonly string[]): Placed {
  const rows = rowsOf();
  const byRow = new Map(rows.map((r) => [r.id, r]));
  const boxes = new Map<string, Box>();
  for (const id of nodeIds) {
    const slot = SLOTS[id];
    const row = slot ? byRow.get(slot.row) : undefined;
    if (!slot || !row) continue;
    boxes.set(id, { x: slot.col * (W + GX), y: row.top, w: W, h: row.height });
  }
  const last = rows[rows.length - 1];
  const width = COLUMNS * W + (COLUMNS - 1) * GX;
  return { boxes, rows, width, canvasWidth: width + GUTTER, height: last ? last.top + last.height + TOP : TOP };
}

function classify(from: Slot, to: Slot, rowIndex: Map<string, number>): Route {
  const dr = (rowIndex.get(to.row) ?? 0) - (rowIndex.get(from.row) ?? 0);
  const dc = to.col - from.col;
  if (dr === 0) return dc === 1 ? "straight" : "over";
  if (dr > 0) return "down";
  return dc > 0 ? "step" : "loop";
}

const midX = (b: Box) => b.x + b.w / 2;
const midY = (b: Box) => b.y + b.h / 2;

/** One elbow path per edge whose two ends are placed:
 * - straight: the next box in the row, side to side;
 * - over: further along the row, up over the boxes between and down into the target's top;
 * - down: any lower row, down to a trunk in the gap below the source's row, across, and down into
 *   the target's top (a fan-out shares the trunk);
 * - step: up and to the right, through the gap left of the target's column;
 * - loop: up and back, out along the right gutter and down into the target's top.
 * Lines entering one box's top are spread across it, in the order of their sources. */
export function route(edges: readonly MapEdge[], placed: Placed): Routed[] {
  const rowIndex = new Map(placed.rows.map((r, i) => [r.id, i]));
  const rowById = new Map(placed.rows.map((r) => [r.id, r]));
  const live = edges.filter((e) => placed.boxes.has(e.from) && placed.boxes.has(e.to) && SLOTS[e.from] && SLOTS[e.to]);
  const kind = new Map(live.map((e) => [e, classify(SLOTS[e.from]!, SLOTS[e.to]!, rowIndex)]));
  const box = (id: string) => placed.boxes.get(id)!;

  // Entry points across each target's top, ordered by where their sources are.
  const entries = new Map<MapEdge, number>();
  const intoTop = new Map<string, MapEdge[]>();
  for (const e of live) {
    if (kind.get(e) === "straight" || kind.get(e) === "step") continue;
    intoTop.set(e.to, [...(intoTop.get(e.to) ?? []), e]);
  }
  for (const [target, incoming] of intoTop) {
    const b = box(target);
    const sorted = [...incoming].sort((a, c) => midX(box(a.from)) - midX(box(c.from)) || a.from.localeCompare(c.from));
    sorted.forEach((e, i) => entries.set(e, b.x + (b.w * (i + 1)) / (sorted.length + 1)));
  }

  // One trunk per source in the gap below its row, spread when several sources share a gap.
  const trunks = new Map<string, number>();
  const bySourceRow = new Map<string, string[]>();
  for (const e of live) {
    if (kind.get(e) !== "down") continue;
    const row = SLOTS[e.from]!.row;
    const sources = bySourceRow.get(row) ?? [];
    if (!sources.includes(e.from)) sources.push(e.from);
    bySourceRow.set(row, sources);
  }
  for (const [rowId, sources] of bySourceRow) {
    const row = rowById.get(rowId)!;
    const bottom = row.top + row.height;
    const ordered = [...sources].sort((a, c) => midX(box(a)) - midX(box(c)));
    ordered.forEach((src, i) => trunks.set(src, bottom + (GY * (i + 1)) / (ordered.length + 1)));
  }

  return live.map((edge): Routed => {
    const s = box(edge.from);
    const t = box(edge.to);
    const r = kind.get(edge)!;
    const entry = entries.get(edge) ?? midX(t);
    switch (r) {
      case "straight":
        return { edge, route: r, points: [[s.x + s.w, midY(s)], [t.x, midY(t)]], label: null };
      case "over": {
        const y = rowById.get(SLOTS[edge.from]!.row)!.top - 10;
        const points: [number, number][] = [[midX(s), s.y], [midX(s), y], [entry, y], [entry, t.y]];
        return { edge, route: r, points, label: { x: (midX(s) + entry) / 2, y: y - 4, anchor: "middle" } };
      }
      case "down": {
        const y = trunks.get(edge.from)!;
        const points: [number, number][] = [[midX(s), s.y + s.h], [midX(s), y], [entry, y], [entry, t.y]];
        return { edge, route: r, points, label: { x: entry - 6, y: (y + t.y) / 2 + 4, anchor: "end" } };
      }
      case "step": {
        const x = t.x - GX / 2;
        const points: [number, number][] = [[s.x + s.w, midY(s)], [x, midY(s)], [x, midY(t)], [t.x, midY(t)]];
        return { edge, route: r, points, label: { x: x + 4, y: (midY(s) + midY(t)) / 2, anchor: "start" } };
      }
      case "loop": {
        const x = placed.width + GUTTER / 2;
        const y = t.y - 12;
        const points: [number, number][] = [[s.x + s.w, midY(s)], [x, midY(s)], [x, y], [entry, y], [entry, t.y]];
        return { edge, route: r, points, label: { x: (x + entry) / 2, y: y - 4, anchor: "middle" } };
      }
    }
  });
}

export function pathOf(points: readonly [number, number][]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
}
