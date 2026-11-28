// OPS-1: one fixture of every staff view, for the tests. Its shapes are the gateway's contract (`types.ts`). The system view is the gateway's own output (system.json); the tool names (tool_scopes.py),
// models (llm.py), order rows (data/backend/orders.jsonl), intents and labels (the handoff desk) and
// action names (views.py) are copied from the repo; the events, holds and stock numbers are
// illustrative. It is a fixture: the console shows the gateway's data, never this.
import SYSTEM_JSON from "./system.json";
import type { ActivityEvent, AgentMap, OrderRow, StaffViews, SystemView } from "./types";

const STARTED = "2026-11-27T14:00:00Z";

// The gateway's generated map and sample traces, as `GET /api/staff/system` serves them
// (test_ops1_agent_map.py pins this snapshot equal to `views.system_view`).
export const SYSTEM = SYSTEM_JSON as SystemView;
export const MAP: AgentMap = SYSTEM.map;

const order = (
  order_id: string,
  placed_on: string,
  shopper_id: string,
  region: string,
  postal_code: string,
  lines: number,
  units: number,
  reason: string | null,
  status: string,
  escalated: boolean,
): OrderRow => ({ order_id, placed_on, shopper_id, region, postal_code, lines, units, reason, status, escalated, source: "store" });

// Copied from data/backend/orders.jsonl.
export const ORDER_ROWS: OrderRow[] = [
  order("O000001", "2026-10-11", "S0103", "US", "US-7099", 3, 8, null, "picked_up", false),
  order("O000002", "2026-10-09", "S0140", "UK", "UK-9107", 1, 1, "fraud_flag", "on_hold", true),
  order("O000003", "2026-10-02", "S0062", "US", "US-2513", 3, 4, null, "label_created", false),
  order("O000014", "2026-10-10", "S0147", "US", "US-8656", 3, 5, "stock_out", "on_hold", true),
  order("O000024", "2026-10-23", "S0065", "US", "US-6110", 3, 5, "soft_decline", "confirmed", false),
  order("O000037", "2026-10-12", "S0195", "UK", "XX-2013", 3, 7, "address_error", "confirmed", false),
  order("O000049", "2026-10-28", "S0138", "US", "US-1522", 1, 2, "fraud_flag", "on_hold", true),
  order("O000052", "2026-10-17", "S0074", "CA", "CA-6732", 2, 5, "soft_decline", "confirmed", false),
  order("O000078", "2026-10-11", "S0006", "CA", "XX-8192", 3, 5, "address_error", "confirmed", false),
  order("O000127", "2026-10-10", "S0098", "EU", "EU-3426", 1, 1, "soft_decline", "confirmed", false),
];

const tally = (rows: OrderRow[]) => {
  const by_reason: Record<string, number> = {};
  for (const row of rows) if (row.reason) by_reason[row.reason] = (by_reason[row.reason] ?? 0) + 1;
  const escalated = rows.filter((r) => r.escalated).length;
  return { total: rows.length, escalated, processed: rows.filter((r) => !r.reason && !r.escalated).length, by_reason };
};

const event = (time: string, actor: string, action: string, ref: string, outcome: ActivityEvent["outcome"], detail: string): ActivityEvent => ({
  time: `2026-11-27T${time}Z`,
  actor,
  action,
  ref,
  outcome,
  detail,
});

export const EVENTS: ActivityEvent[] = [
  event("15:04:18", "portal-checkout", "order.confirm", "O900001", "ok", "Order confirmed after capture"),
  event("15:04:17", "portal-checkout", "payment.capture", "O900001", "ok", "Captured the authorized amount"),
  event("15:04:16", "portal-checkout", "inventory.commit", "O900001", "ok", "Committed 2 lines at DC-EAST"),
  event("15:04:15", "portal-checkout", "payment.authorize", "O900001", "ok", "Authorized with a sandbox token"),
  event("15:04:14", "portal-checkout", "fraud.assess", "O900001", "ok", "Low risk"),
  event("15:04:13", "portal-checkout", "inventory.reserve", "O900001", "ok", "Held 2 lines for 15 minutes"),
  event("14:58:42", "handoff-desk", "case.create", "H000001", "escalated", "Queued with a Care summary"),
  event("14:51:09", "portal-checkout", "order.escalate", "O900000", "escalated", "Stock lost to another shopper"),
];

export const FIXTURE: StaffViews = {
  overview: {
    started_at: STARTED,
    catalog: { products: 200, variants: 841, orders: 1000 },
    agent_events: { total: EVENTS.length, by_outcome: { ok: 6, escalated: 2 } },
    handoffs: { open: 1, by_status: { queued: 1 } },
    orders: tally(ORDER_ROWS),
    holds: { count: 1, units: 2, nodes: ["DC-EAST"] },
    latest: EVENTS,
  },
  system: SYSTEM,
  activity: { started_at: STARTED, events: EVENTS, total: EVENTS.length },
  handoffs: {
    started_at: STARTED,
    handoffs: [
      {
        reference: "H000001",
        time: "2026-11-27T14:58:42Z",
        intent: "order_issue",
        order_id: "O000800",
        summary: "Shopper reports the parcel arrived damaged and asks for a replacement.",
        summary_label: "written by the Care agent (an AI model) from an untrusted transcript",
        amount: { minor: 24400, currency: "USD" },
        status: "queued",
      },
    ],
  },
  orders: { counts: tally(ORDER_ROWS), rows: ORDER_ROWS },
  stock: {
    started_at: STARTED,
    nodes: ["DC-EAST", "DC-WEST"],
    holds: [
      {
        order_id: "O900002",
        variant_id: "P0001-BLK-REG-L",
        product: "Tidewick Harbor Rain Jacket",
        node: "DC-EAST",
        quantity: 2,
        status: "held",
        expires_at: "2026-11-27T15:20:00Z",
      },
    ],
    variants: [
      {
        product_id: "P0001",
        product: "Tidewick Harbor Rain Jacket",
        department: "Jackets",
        variant_id: "P0001-BLK-REG-L",
        nodes: { "DC-EAST": { on_hand: 65, held: 2 }, "DC-WEST": { on_hand: 3, held: 0 } },
      },
      {
        product_id: "P0001",
        product: "Tidewick Harbor Rain Jacket",
        department: "Jackets",
        variant_id: "P0001-FOR-REL-2XL",
        nodes: { "DC-EAST": { on_hand: 1, held: 0 }, "DC-WEST": { on_hand: 2, held: 0 } },
      },
    ],
  },
};
