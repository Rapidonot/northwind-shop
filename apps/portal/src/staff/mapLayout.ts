// OPS-1: the agent map's geometry, read from the layout table (layout.json), which was measured
// from the reference mockup. Each generated node has a box and each generated edge a routed elbow
// path; text is wrapped to its box here, since SVG text does not wrap. Pure functions, so the tests
// check every box and route without a browser.
import layout from "./layout.json";
import type { MapEdge } from "./types";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type Anchor = "start" | "middle" | "end";

export interface RouteSpec {
  points: [number, number][];
  label: { x: number; y: number; anchor: Anchor } | null;
}

export const VIEW_BOX = layout.view_box as [number, number];
export const MIN_WIDTH = layout.min_width;
export const SEPARATORS: readonly number[] = layout.separators;
export const LANE_TITLES: readonly { text: string; x: number; y: number }[] = layout.lane_titles;

export const SLOTS: Readonly<Record<string, Box>> = Object.fromEntries(
  Object.entries(layout.slots as unknown as Record<string, [number, number, number, number]>).map(([id, [x, y, w, h]]) => [id, { x, y, w, h }]),
);

export const ROUTES: Readonly<Record<string, RouteSpec>> = Object.fromEntries(
  Object.entries(layout.routes as unknown as Record<string, { points: [number, number][]; label: [number, number, Anchor] | null }>).map(([key, r]) => [
    key,
    { points: r.points, label: r.label ? { x: r.label[0], y: r.label[1], anchor: r.label[2] } : null },
  ]),
);

export const edgeKey = (edge: Pick<MapEdge, "from" | "to">) => `${edge.from}->${edge.to}`;

export function pathOf(points: readonly [number, number][]): string {
  return points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
}

/** Type sizes from the reference: title 13px (12px in the checkout lane), description 11px, tools
 * 10px monospace, chips 10.5px in 18px-high chips. */
export const TYPE = { title: 13, titleSmall: 12, description: 11, tools: 10, chip: 10.5, chipHeight: 18, pad: 10 } as const;

/** Average glyph widths, as a share of the font size, used to wrap text to a box. */
const SANS = 0.56;
const MONO = 0.6;

export function wrap(text: string, width: number, size: number, mono = false): string[] {
  const max = Math.max(4, Math.floor(width / (size * (mono ? MONO : SANS))));
  const lines: string[] = [];
  let line = "";
  const add = (piece: string, joiner: string) => {
    const next = line === "" ? piece : `${line}${joiner}${piece}`;
    if (next.length <= max) {
      line = next;
      return;
    }
    if (line !== "") lines.push(line);
    line = piece;
  };
  for (const word of text.split(" ")) {
    if (word.length <= max) {
      add(word, " ");
      continue;
    }
    // A name too long for a line breaks after an underscore; a piece still too long breaks at the
    // width, so no line ever runs past its box.
    const pieces = word.split(/(?<=_)/).flatMap((piece) => (piece.length <= max ? [piece] : (piece.match(new RegExp(`.{1,${max}}`, "g")) ?? [piece])));
    pieces.forEach((piece, i) => add(piece, i === 0 ? " " : ""));
  }
  if (line !== "") lines.push(line);
  return lines;
}

export function chipWidth(text: string): number {
  return Math.ceil(text.length * TYPE.chip * SANS + 14);
}

/** Which lane a box is in: the band between two separators that holds its top. */
export function laneOf(box: Box): number {
  return SEPARATORS.filter((y) => y <= box.y).length;
}

export const CHECKOUT_LANE = 2;
/** Space above the title's cap height and below the chips, as tight as the reference's boxes. */
const TOP_PAD = 8;
const BOTTOM_PAD = 6;
const WIDE = 300;
const SHORT = 60;
export const MCP_BAR = 3;

export interface NodeText {
  x: number;
  titleSize: number;
  title: string[];
  titleY: number;
  sub: string[];
  subY: number;
  /** Tool names, wrapped, then one line per collapsed group. */
  tools: string[];
  toolsY: number;
  /** The baseline of the last line of text. */
  bottom: number;
  chipY: number;
  /** A wide, short box (the eval-only judge) puts its chips at the top right, beside the title. */
  chipsRight: boolean;
}

/** Where a node's text goes inside its box: the title (12px in the checkout lane), then the
 * description, then the tools, with the chips along the bottom. */
export function nodeText(
  node: Pick<MapEdge, never> & { kind: string; title: string; subtitle: string; tools: string[]; collapsed: { label: string }[] },
  box: Box,
): NodeText {
  const inset = TYPE.pad + (node.kind === "mcp" ? MCP_BAR : 0);
  const width = box.w - inset - TYPE.pad;
  const titleSize = laneOf(box) === CHECKOUT_LANE ? TYPE.titleSmall : TYPE.title;
  const title = wrap(node.title, width, titleSize, node.kind === "mcp");
  const sub = node.subtitle ? wrap(node.subtitle, width, TYPE.description) : [];
  const tools = [...(node.tools.length > 0 ? wrap(node.tools.join(", "), width, TYPE.tools, true) : []), ...node.collapsed.map((g) => `+ ${g.label}`)];
  const titleY = box.y + TOP_PAD + titleSize;
  let bottom = titleY + (title.length - 1) * (titleSize + 3);
  const subY = bottom + TYPE.description + 3;
  if (sub.length > 0) bottom = subY + (sub.length - 1) * (TYPE.description + 3);
  const toolsY = bottom + TYPE.tools + 5;
  if (tools.length > 0) bottom = toolsY + (tools.length - 1) * (TYPE.tools + 3);
  const chipsRight = box.w >= WIDE && box.h <= SHORT;
  const chipY = chipsRight ? box.y + TOP_PAD - 2 : box.y + box.h - BOTTOM_PAD - TYPE.chipHeight;
  return { x: box.x + inset, titleSize, title, titleY, sub, subY, tools, toolsY, bottom, chipY, chipsRight };
}
