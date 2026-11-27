// OPS-1: one fixture of every staff view, for the tests. Its shapes are the gateway's contract (`types.ts`). The tool names (tool_scopes.py),
// models (llm.py), order rows (data/backend/orders.jsonl), intents and labels (the handoff desk) and
// action names (views.py) are copied from the repo; the events, holds and stock numbers are
// illustrative. It is a fixture: the console shows the gateway's data, never this.
import type { ActivityEvent, AgentMap, MapEdge, MapNode, OrderRow, StaffViews, ToolGroup } from "./types";

const STARTED = "2026-11-27T14:00:00Z";

const node = (id: string, kind: MapNode["kind"], title: string, subtitle = "", models: string[] = [], tools: string[] = [], collapsed: ToolGroup[] = []): MapNode => ({
  id,
  kind,
  title,
  subtitle,
  models,
  tools,
  collapsed,
});

const edge = (from: string, to: string, label: string | null = null, dashed = false): MapEdge => ({ from, to, label, dashed });

// tool_scopes.py: CATALOG_READ_TOOLS, get_cart and eligible_products; the writes collapse to one
// line, named from catalog_tool.CART_TOOL_NAMES less the read `get_cart`.
const CART_WRITES = ["add_to_cart", "remove_from_cart", "set_cart_quantity"];

export const MAP: AgentMap = {
  legend: ["ai", "mcp", "code", "fake", "eval"],
  nodes: [
    node("shopper-chat", "entry", "Shopper chat", "Entry point"),
    node("query-understanding", "ai", "Query understanding", "Rules first; Haiku if unsure", ["Haiku"]),
    node("shopper-assistant", "ai", "Shopper Assistant", "Customer-facing agent", ["Sonnet"]),
    node("output-check", "code", "Output check", "Removes unbacked price, stock and policy claims and planted promotions"),
    node("answer", "end", "Answer", "Shopper reply"),
    node(
      "northwind-catalog",
      "mcp",
      "northwind-catalog",
      "",
      [],
      ["search_products", "get_product", "get_variant", "check_stock", "get_price", "get_cart", "eligible_products"],
      [{ label: "cart write tools", tools: CART_WRITES }],
    ),
    node("northwind-rag", "mcp", "northwind-rag", "Hybrid search: catalog, reviews and policies", [], ["retrieve_context"]),
    node("northwind-orders", "mcp", "northwind-orders", "Signed-in only · order store + carrier (fake) tracking", [], ["get_order", "request_return"]),
    node("northwind-policy", "mcp", "northwind-policy", "Signed-in only · returns & shipping policies", [], ["evaluate_return_eligibility"]),
    node("talk-to-person", "entry", "Talk to a person", "Entry point"),
    node("handoff-trigger", "code", "Handoff trigger", "Runs before any model call"),
    node("handoff-desk", "code", "Handoff desk", "Builds the case"),
    node("care-subagent", "ai", "Care agent", "Handoff and escalation summaries", ["Haiku"]),
    node("case-queue", "fake", "Case queue", "Staff queue"),
    node("pay-demo", "entry", "Pay (demo)", "Entry point"),
    node("checkout-validate", "code", "Validate"),
    node("checkout-hold", "code", "Inventory hold"),
    node("checkout-fraud", "fake", "Fraud"),
    node("checkout-authorize", "fake", "Payment authorize"),
    node("checkout-commit", "code", "Inventory commit"),
    node("checkout-capture", "fake", "Payment capture"),
    node("checkout-confirm", "code", "Confirm"),
    node("staff-request", "entry", "Staff request (command)", "Entry point"),
    node("orchestrator", "ai", "Orchestrator", "Sonnet planner; runs its subagents in parallel", ["Sonnet"]),
    node("orders-subagent", "ai", "Orders agent", "Read-only: fraud and payment", ["Haiku"]),
    node("returns-subagent", "ai", "Returns agent", "", ["Haiku"]),
    node("order-fraud", "fake", "Fraud"),
    node("order-payment", "fake", "Payment"),
    node("order-carrier", "fake", "Carrier"),
    node("escalated-order", "entry", "Escalated order (command)", "Entry point"),
    node("escalation", "code", "Escalation", "Recommended action"),
    node("judge", "eval", "Judge", "Eval only: offline scoring, no live arrows", ["Sonnet"]),
  ],
  edges: [
    edge("shopper-chat", "query-understanding"),
    edge("query-understanding", "shopper-assistant"),
    edge("query-understanding", "northwind-catalog", "eligible_products", true),
    edge("shopper-assistant", "northwind-catalog"),
    edge("shopper-assistant", "northwind-rag"),
    edge("shopper-assistant", "northwind-orders"),
    edge("shopper-assistant", "northwind-policy"),
    edge("shopper-assistant", "output-check"),
    edge("output-check", "answer"),
    edge("talk-to-person", "handoff-trigger"),
    // gateway handoff.py: with no order, the case's amounts are the live cart (hard rule 2).
    edge("handoff-trigger", "northwind-catalog", "get_cart, no order"),
    edge("handoff-trigger", "handoff-desk"),
    edge("handoff-desk", "care-subagent"),
    edge("care-subagent", "case-queue"),
    edge("pay-demo", "checkout-validate"),
    // gateway checkout.py: the bag read live (get_cart), and emptied once placed (remove_from_cart).
    edge("pay-demo", "northwind-catalog", "get_cart · remove_from_cart"),
    edge("checkout-validate", "checkout-hold"),
    edge("checkout-hold", "checkout-fraud"),
    edge("checkout-fraud", "checkout-authorize"),
    edge("checkout-authorize", "checkout-commit"),
    edge("checkout-commit", "checkout-capture"),
    edge("checkout-capture", "checkout-confirm"),
    // orchestrator/run.py: a staff request, split by the planner; both subagents only read.
    edge("staff-request", "orchestrator"),
    edge("orchestrator", "orders-subagent"),
    edge("orchestrator", "returns-subagent"),
    edge("orders-subagent", "order-fraud", "code port"),
    edge("orders-subagent", "order-payment", "code port"),
    edge("returns-subagent", "order-carrier", "code port"),
    // care/__main__.py: an escalated order's case, by the Care agent; the action from escalation.py.
    edge("escalated-order", "escalation"),
    edge("escalation", "care-subagent", "escalation case"),
  ],
};

const order = (order_id: string, placed_on: string, shopper_id: string, region: string, postal_code: string, lines: number, units: number, reason: string | null): OrderRow => ({
  order_id,
  placed_on,
  shopper_id,
  region,
  postal_code,
  lines,
  units,
  reason,
  escalated: reason !== null,
  status: reason === null ? "pending" : "escalated",
  source: "store",
});

// Copied from data/backend/orders.jsonl.
export const ORDER_ROWS: OrderRow[] = [
  order("O000001", "2026-10-11", "S0103", "US", "US-7099", 3, 8, null),
  order("O000002", "2026-10-09", "S0140", "UK", "UK-9107", 1, 1, "fraud_flag"),
  order("O000003", "2026-10-02", "S0062", "US", "US-2513", 3, 4, null),
  order("O000014", "2026-10-10", "S0147", "US", "US-8656", 3, 5, "stock_out"),
  order("O000024", "2026-10-23", "S0065", "US", "US-6110", 3, 5, "soft_decline"),
  order("O000037", "2026-10-12", "S0195", "UK", "XX-2013", 3, 7, "address_error"),
  order("O000049", "2026-10-28", "S0138", "US", "US-1522", 1, 2, "fraud_flag"),
  order("O000052", "2026-10-17", "S0074", "CA", "CA-6732", 2, 5, "soft_decline"),
  order("O000078", "2026-10-11", "S0006", "CA", "XX-8192", 3, 5, "address_error"),
  order("O000127", "2026-10-10", "S0098", "EU", "EU-3426", 1, 1, "soft_decline"),
];

const tally = (rows: OrderRow[]) => {
  const by_reason: Record<string, number> = {};
  for (const row of rows) if (row.reason) by_reason[row.reason] = (by_reason[row.reason] ?? 0) + 1;
  const escalated = rows.filter((r) => r.escalated).length;
  return { total: rows.length, escalated, processed: rows.length - escalated, by_reason };
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
  event("15:04:18", "portal-checkout", "order.confirm", "O001001", "ok", "Order confirmed after capture"),
  event("15:04:17", "portal-checkout", "payment.capture", "O001001", "ok", "Captured the authorized amount"),
  event("15:04:16", "portal-checkout", "inventory.commit", "O001001", "ok", "Committed 2 lines at DC-EAST"),
  event("15:04:15", "portal-checkout", "payment.authorize", "O001001", "ok", "Authorized with a sandbox token"),
  event("15:04:14", "portal-checkout", "fraud.assess", "O001001", "ok", "Low risk"),
  event("15:04:13", "portal-checkout", "inventory.reserve", "O001001", "ok", "Held 2 lines for 15 minutes"),
  event("14:58:42", "handoff-desk", "case.create", "H000001", "escalated", "Queued with a Care summary"),
  event("14:51:09", "portal-checkout", "order.escalate", "O001000", "escalated", "Stock lost to another shopper"),
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
  system: {
    map: MAP,
    traces: [
      {
        id: "chat",
        entry: "shopper-chat",
        kind: "Chat question",
        title: "Is the Tidewick Harbor Rain Jacket waterproof, and is it in stock in Large?",
        refs: ["P0001"],
        outcome: "ok",
        no_ai: false,
        sample: true,
        steps: [
          { node: "shopper-chat", label: "The shopper asks" },
          { node: "query-understanding", label: "Rules read a size filter (L) and an availability filter" },
          { node: "northwind-catalog", label: "eligible_products: which products have L in stock, IDs only" },
          { node: "shopper-assistant", label: "Plans its tool calls" },
          { node: "northwind-rag", label: "retrieve_context: the jacket's specs and care guide" },
          { node: "northwind-catalog", label: "check_stock: live stock for size L" },
          { node: "output-check", label: "Every claim backed by a source or a live tool" },
          { node: "answer", label: "The cited answer" },
        ],
      },
      {
        id: "handoff",
        entry: "shopper-chat",
        kind: "Damaged parcel + handoff",
        title: "My parcel for order O000800 arrived damaged. → Talk to a person",
        refs: ["O000800"],
        outcome: "escalated",
        no_ai: false,
        sample: true,
        steps: [
          { node: "shopper-chat", label: "The signed-in shopper: my parcel for order O000800 arrived damaged" },
          { node: "query-understanding", label: "No product filters: rules only" },
          { node: "shopper-assistant", label: "Looks the order up" },
          { node: "northwind-orders", label: "get_order: O000800, the shopper's own order, delivered" },
          { node: "northwind-policy", label: "evaluate_return_eligibility: can the damaged line be returned" },
          { node: "output-check", label: "Order and policy claims backed by the tools" },
          { node: "answer", label: "The options, and the offer of a person" },
          { node: "talk-to-person", label: "if the shopper presses Talk to a person" },
          { node: "handoff-trigger", label: "Matched before any model call" },
          { node: "handoff-desk", label: "Intent, root cause and amounts from the order store, by code" },
          { node: "care-subagent", label: "One-line summary, labelled model-written" },
          { node: "case-queue", label: "Case queued for staff" },
        ],
      },
      {
        id: "checkout",
        entry: "pay-demo",
        kind: "Web checkout",
        title: "Pay (demo) on a 2-line bag → order shown in history",
        refs: ["O001001"],
        outcome: "ok",
        no_ai: true,
        sample: true,
        steps: [
          { node: "pay-demo", label: "The signed-in shopper presses Pay (demo)" },
          { node: "northwind-catalog", label: "get_cart: the bag read live" },
          { node: "checkout-validate", label: "Digest matches; total under the order limit" },
          { node: "checkout-hold", label: "Stock held" },
          { node: "checkout-fraud", label: "Low risk" },
          { node: "checkout-authorize", label: "Sandbox token authorized" },
          { node: "checkout-commit", label: "Stock committed" },
          { node: "checkout-capture", label: "Captured" },
          { node: "checkout-confirm", label: "Order confirmed" },
          { node: "northwind-catalog", label: "remove_from_cart: the bag emptied" },
        ],
      },
      {
        id: "staff",
        entry: "staff-request",
        kind: "Backend staff request",
        title: "O000002: was it charged, and where is it? → one answer from two subagents",
        refs: ["O000002"],
        outcome: "ok",
        no_ai: false,
        sample: true,
        steps: [
          { node: "staff-request", label: "Staff run the orchestrator command with the request" },
          { node: "orchestrator", label: "The planner splits it: payment and fraud, and tracking" },
          { node: "orders-subagent", label: "payment_fraud, read-only" },
          { node: "order-payment", label: "get_payment_status: no payment intent yet, a valid empty answer" },
          { node: "order-fraud", label: "get_fraud_assessments: none yet, a valid empty answer" },
          { node: "returns-subagent", label: "tracking, read-only" },
          { node: "order-carrier", label: "The carrier's scans for O000002" },
          { node: "orchestrator", label: "One answer from both sections" },
        ],
      },
    ],
  },
  activity: { started_at: STARTED, events: EVENTS },
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
        order_id: "O001002",
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
