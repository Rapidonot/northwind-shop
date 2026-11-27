// OPS-1: the demo staff console's read-only contract. The gateway serves each view at
// `/api/staff/<view>`, only with DEMO_STAFF_DASHBOARD=1 (404 otherwise). Every string here has been
// through the gateway's card and secret screen, and the console renders it as text only.

export type Outcome = "ok" | "escalated" | "refused" | "error";

/** How a box on the agent map is drawn. `eval` is greyed: it scores offline, with no live arrows.
 * `end` is a plain box that ends a path (the shopper's answer). */
export type NodeKind = "entry" | "ai" | "mcp" | "code" | "fake" | "eval" | "end";

/** Tools shown as one line, such as "+ cart write tools", with their names kept for the tooltip. */
export interface ToolGroup {
  label: string;
  tools: string[];
}

export interface MapNode {
  id: string;
  kind: NodeKind;
  title: string;
  subtitle: string;
  /** "Sonnet" or "Haiku", from the model constant the agent's own module passes; empty if no model. */
  models: string[];
  /** An MCP server's tool names, from the tool scopes; empty for anything else. */
  tools: string[];
  /** Tools listed as one line: northwind-catalog's cart writes, from CART_TOOL_NAMES. */
  collapsed: ToolGroup[];
}

export interface MapEdge {
  from: string;
  to: string;
  label: string | null;
  /** A call scoped to part of a server (query understanding reaches only `eligible_products`). */
  dashed: boolean;
}

/** Nodes and edges only: where each node sits is the portal's layout table (layout.json). */
export interface AgentMap {
  nodes: MapNode[];
  edges: MapEdge[];
  /** The node kinds present, in legend order. */
  legend: NodeKind[];
}

export interface TraceStep {
  node: string;
  label: string;
}

/** A worked example of one entry point's path. Always sample data, never a recorded run. */
export interface SampleTrace {
  id: string;
  entry: string;
  kind: string;
  title: string;
  refs: string[];
  outcome: Outcome;
  no_ai: boolean;
  steps: TraceStep[];
  sample: true;
}

export interface SystemView {
  map: AgentMap;
  traces: SampleTrace[];
}

export interface ActivityEvent {
  /** ISO 8601. */
  time: string;
  actor: string;
  action: string;
  /** The order, case or session reference the event is about. */
  ref: string;
  outcome: Outcome;
  detail: string;
}

export interface ActivityView {
  started_at: string;
  events: ActivityEvent[];
}

export interface Money {
  minor: number;
  currency: string;
}

export interface Handoff {
  reference: string;
  time: string;
  intent: string;
  order_id: string | null;
  summary: string;
  /** Who wrote the summary: the Care agent (a model) from an untrusted transcript, or code. */
  summary_label: string;
  amount: Money | null;
  status: string;
}

export interface HandoffsView {
  started_at: string;
  handoffs: Handoff[];
}

export interface OrderRow {
  order_id: string;
  placed_on: string;
  shopper_id: string;
  region: string;
  postal_code: string;
  lines: number;
  units: number;
  status: string;
  escalated: boolean;
  /** The order's exception (fraud_flag, address_error, soft_decline, stock_out), or null. */
  reason: string | null;
  /** `store`: the 1,000 synthetic orders; `demo`: placed with Pay (demo) since the server started. */
  source: "store" | "demo";
}

export interface OrderCounts {
  total: number;
  escalated: number;
  processed: number;
  by_reason: Record<string, number>;
}

export interface OrdersView {
  counts: OrderCounts;
  rows: OrderRow[];
}

export interface Hold {
  order_id: string;
  variant_id: string;
  product: string;
  node: string;
  quantity: number;
  status: string;
  expires_at: string;
}

export interface NodeStock {
  on_hand: number;
  held: number;
}

export interface VariantStock {
  product_id: string;
  product: string;
  department: string;
  variant_id: string;
  nodes: Record<string, NodeStock>;
}

export interface StockView {
  started_at: string;
  nodes: string[];
  holds: Hold[];
  variants: VariantStock[];
}

export interface Overview {
  started_at: string;
  catalog: { products: number; variants: number; orders: number };
  agent_events: { total: number; by_outcome: Partial<Record<Outcome, number>> };
  handoffs: { open: number; by_status: Record<string, number> };
  orders: OrderCounts;
  holds: { count: number; units: number; nodes: string[] };
  latest: ActivityEvent[];
}

export interface StaffViews {
  overview: Overview;
  system: SystemView;
  activity: ActivityView;
  handoffs: HandoffsView;
  orders: OrdersView;
  stock: StockView;
}

export type ViewName = keyof StaffViews;

export type Loaded<T> = { kind: "loading" } | { kind: "ready"; data: T } | { kind: "failed" };

export type Loader = <V extends ViewName>(view: V) => Promise<StaffViews[V] | null>;
