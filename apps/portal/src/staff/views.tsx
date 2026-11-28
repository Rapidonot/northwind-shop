// OPS-1: the staff console's table views (docs/design/staff/). Every number is counted by the
// gateway from the real stores; only the traces on the "Agent system" view are sample data.
import { useState } from "react";
import { clock, count, items, matches, money, reasonLabel, shortClock, since, UNRECORDED } from "./format";
import { Empty, More, OutcomePill, PAGE, Picker, SearchField, Segmented, Stat, StatusPill, Toolbar, ViewHead } from "./parts";
import type { ActivityEvent, ActivityView, HandoffsView, Outcome, OrdersView, Overview, StockView } from "./types";

const OUTCOMES = ["ok", "escalated", "refused", "error"] as const;

function EventTable({ events }: { events: ActivityEvent[] }) {
  return (
    <table className="staff-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Agent</th>
          <th>Action</th>
          <th>Order / session</th>
          <th>Outcome</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {events.map((event, index) => (
          <tr key={`${event.time}-${index}`}>
            <td>{clock(event.time)}</td>
            <td>{event.actor}</td>
            <td>{event.action}</td>
            <td className="mono">{event.ref}</td>
            <td>
              <OutcomePill outcome={event.outcome} />
            </td>
            <td className="muted">{event.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function breakdown(parts: [string, number][]): string {
  const shown = parts.filter(([, n]) => n > 0).map(([label, n]) => `${count(n)} ${label}`);
  return shown.length > 0 ? shown.join(" · ") : "none yet";
}

function Tile({ title, value, note, alert = false }: { title: string; value: number; note: string; alert?: boolean }) {
  return (
    <div className="tile-stat">
      <p className="tile-stat-title">{title}</p>
      <p className={alert && value > 0 ? "tile-stat-value is-alert" : "tile-stat-value"}>{count(value)}</p>
      <p className="muted">{note}</p>
    </div>
  );
}

export function OverviewView({ view }: { view: Overview }) {
  const outcome = (name: Outcome) => view.agent_events.by_outcome[name] ?? 0;
  return (
    <>
      <ViewHead title="Overview" note={`Live read-only snapshot of the shop's agents, handoffs, orders and stock holds. Agent events, handoffs and holds are ${since(view.started_at)}.`} />
      <div className="tiles">
        <Tile title="Agent events" value={view.agent_events.total} note={breakdown(OUTCOMES.map((o) => [o, outcome(o)]))} />
        <Tile title="Open handoffs" value={view.handoffs.open} note={breakdown(Object.entries(view.handoffs.by_status))} alert />
        <Tile title="Escalated orders" value={view.orders.escalated} note={`of ${count(view.orders.exceptions)} ${view.orders.exceptions === 1 ? "order" : "orders"} with an exception`} alert />
        <Tile title="Live stock holds" value={view.holds.count} note={`${count(view.holds.units)} units held${view.holds.nodes.length > 0 ? ` across ${view.holds.nodes.join(" & ")}` : ""}`} />
      </div>
      <div className="section-head">
        <h2>Latest {view.latest.length} agent events</h2>
        <a className="btn btn-outline" href="#/staff/activity">
          View all activity
        </a>
      </div>
      {view.latest.length > 0 ? <EventTable events={view.latest} /> : <Empty>No agent events {since(view.started_at)}.</Empty>}
    </>
  );
}

export function ActivityViewPage({ view }: { view: ActivityView }) {
  const [agent, setAgent] = useState("");
  const [outcome, setOutcome] = useState<Outcome | "all">("all");
  const [query, setQuery] = useState("");
  const agents = [...new Set(view.events.map((e) => e.actor))].sort();
  const shown = view.events.filter(
    (e) => (agent === "" || e.actor === agent) && (outcome === "all" || e.outcome === outcome) && matches(query, [e.ref, e.action, e.actor, e.detail]),
  );
  return (
    <>
      <ViewHead title="Agent activity" note={`Every audited agent action ${since(view.started_at)}: actor, action, reference and outcome.`} />
      <Toolbar>
        <Picker label="All agents" options={agents} value={agent} onChange={setAgent} />
        <Segmented
          label="Outcome"
          options={[{ value: "all", label: "All outcomes" }, ...OUTCOMES.map((o) => ({ value: o, label: o }))]}
          value={outcome}
          onChange={setOutcome}
        />
        <SearchField label="Search order, session, action" value={query} onChange={setQuery} />
      </Toolbar>
      {shown.length > 0 ? <EventTable events={shown} /> : <Empty>{view.events.length === 0 ? `No agent events ${since(view.started_at)}.` : "No events match these filters."}</Empty>}
      {view.total > view.events.length && (
        <p className="muted">
          Showing the newest {count(view.events.length)} of {count(view.total)} events.
        </p>
      )}
    </>
  );
}

export function HandoffsViewPage({ view }: { view: HandoffsView }) {
  const [status, setStatus] = useState("all");
  const [intent, setIntent] = useState("");
  const [query, setQuery] = useState("");
  const statuses = [...new Set(view.handoffs.map((h) => h.status))].sort();
  const intents = [...new Set(view.handoffs.map((h) => h.intent))].sort();
  const shown = view.handoffs.filter(
    (h) => (status === "all" || h.status === status) && (intent === "" || h.intent === intent) && matches(query, [h.reference, h.order_id, h.summary, h.intent]),
  );
  return (
    <>
      <ViewHead title="Handoffs" note={`Requests for a person, as cases staff can act on without the transcript, ${since(view.started_at)}.`} />
      <div className="stats-bar">
        <Stat label="Open handoffs" value={count(view.handoffs.length)} strong />
        {statuses.map((s) => (
          <Stat key={s} label={s} value={count(view.handoffs.filter((h) => h.status === s).length)} />
        ))}
      </div>
      <Toolbar>
        <Segmented label="Status" options={[{ value: "all", label: "All statuses" }, ...statuses.map((s) => ({ value: s, label: s }))]} value={status} onChange={setStatus} />
        <Picker label="All intents" options={intents} value={intent} onChange={setIntent} />
        <SearchField label="Search reference, order ID, summary" value={query} onChange={setQuery} />
      </Toolbar>
      {shown.length > 0 ? (
        <table className="staff-table">
          <thead>
            <tr>
              <th>Reference</th>
              <th>Time</th>
              <th>Intent</th>
              <th>Summary</th>
              <th>Amount</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((h) => (
              <tr key={h.reference}>
                <td className="mono">{h.reference}</td>
                <td>{shortClock(h.time)}</td>
                <td>{h.intent}</td>
                <td>
                  {h.order_id && <span className="mono">{h.order_id} — </span>}
                  {h.summary}
                  <span className="summary-label">{h.summary_label}</span>
                </td>
                <td>{money(h.amount)}</td>
                <td>
                  <StatusPill status={h.status} escalated={false} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>{view.handoffs.length === 0 ? `No handoffs ${since(view.started_at)}.` : "No handoffs match these filters."}</Empty>
      )}
      {view.handoffs.length > 0 && (
        <p className="muted">
          Showing {count(shown.length)} of {count(view.handoffs.length)} handoff cases
        </p>
      )}
    </>
  );
}

// A reason chip is a part of "Escalated": `reason:<label>` keeps the escalated orders whose reason
// has that label (seeded fraud_flag and Pay (demo)'s fraud_review are both "Fraud review").
type OrderFilter = "all" | "escalated" | "other" | "processed" | `reason:${string}`;

const escalatedLabel = (row: OrdersView["rows"][number]) => reasonLabel(row.reason ?? UNRECORDED);

export function OrdersViewPage({ view }: { view: OrdersView }) {
  const [filter, setFilter] = useState<OrderFilter>("all");
  const [region, setRegion] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const { counts } = view;
  const regions = [...new Set(view.rows.map((r) => r.region))].sort();
  const byLabel = new Map<string, number>();
  for (const [code, n] of Object.entries(counts.escalated_by_reason)) byLabel.set(reasonLabel(code), (byLabel.get(reasonLabel(code)) ?? 0) + n);
  const keep = (filterValue: OrderFilter, row: OrdersView["rows"][number]) => {
    if (filterValue === "all") return true;
    if (filterValue === "escalated") return row.escalated;
    if (filterValue === "other") return row.reason !== null && !row.escalated;
    if (filterValue === "processed") return row.reason === null && !row.escalated;
    return row.escalated && `reason:${escalatedLabel(row)}` === filterValue;
  };
  const shown = view.rows
    .filter((r) => keep(filter, r) && (region === "" || r.region === region) && matches(query, [r.order_id, r.shopper_id, r.postal_code, r.status]))
    .sort((a, b) => Number(b.escalated) - Number(a.escalated) || b.placed_on.localeCompare(a.placed_on) || a.order_id.localeCompare(b.order_id));
  const options: { value: OrderFilter; label: string; count: number }[] = [
    { value: "all", label: "All", count: counts.total },
    { value: "escalated", label: "Escalated", count: counts.escalated },
    ...[...byLabel].map(([label, n]) => ({ value: `reason:${label}` as const, label, count: n })),
    { value: "other", label: "Other exceptions", count: counts.other_exceptions },
    { value: "processed", label: "Processed", count: counts.processed },
  ];
  return (
    <>
      <ViewHead title="Orders & escalations" note="The order store's synthetic orders, with their real statuses and reasons, and orders placed with Pay (demo). Escalated orders first." />
      <div className="stats-bar">
        <Stat label="Total orders" value={count(counts.total)} />
        <Stat label="Escalated" value={count(counts.escalated)} strong />
        <Stat label="Other exceptions" value={count(counts.other_exceptions)} />
        <Stat label="Processed" value={count(counts.processed)} />
      </div>
      <Toolbar>
        <Segmented
          label="Orders"
          options={options}
          value={filter}
          onChange={(v) => {
            setFilter(v);
            setLimit(PAGE);
          }}
        />
        <Picker label="All regions" options={regions} value={region} onChange={setRegion} />
        <SearchField label="Search order, shopper, postal code" value={query} onChange={setQuery} />
      </Toolbar>
      {shown.length > 0 ? (
        <table className="staff-table">
          <thead>
            <tr>
              <th>Order ID</th>
              <th>Placed</th>
              <th>Shopper</th>
              <th>Region</th>
              <th>Postal</th>
              <th>Items</th>
              <th>Status</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, limit).map((r) => (
              <tr key={r.order_id}>
                <td className="mono">
                  {r.order_id}
                  {r.source === "demo" && <span className="tag">Pay (demo)</span>}
                </td>
                <td>{r.placed_on}</td>
                <td className="mono">{r.shopper_id}</td>
                <td>{r.region}</td>
                <td className="mono">{r.postal_code}</td>
                <td>{items(r.lines, r.units)}</td>
                <td>
                  <StatusPill status={r.status} escalated={r.escalated} />
                </td>
                <td className={r.reason ? "reason" : "muted"}>{reasonLabel(r.reason)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No orders match these filters.</Empty>
      )}
      <More shown={Math.min(limit, shown.length)} total={shown.length} onMore={() => setLimit((n) => n + PAGE)} noun="orders" />
    </>
  );
}

type StockFilter = "all" | "held" | "low";

export const LOW_STOCK = 5;

export function StockViewPage({ view }: { view: StockView }) {
  const [filter, setFilter] = useState<StockFilter>("all");
  const [department, setDepartment] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const units = view.holds.reduce((sum, h) => sum + h.quantity, 0);
  const departments = [...new Set(view.variants.map((v) => v.department))].sort();
  const avail = (v: StockView["variants"][number], node: string) => {
    const s = v.nodes[node];
    return s ? s.on_hand - s.held : 0;
  };
  const total = (v: StockView["variants"][number]) => view.nodes.reduce((sum, node) => sum + avail(v, node), 0);
  const shown = view.variants.filter((v) => {
    if (filter === "held" && !view.nodes.some((node) => (v.nodes[node]?.held ?? 0) > 0)) return false;
    if (filter === "low" && total(v) > LOW_STOCK) return false;
    return (department === "" || v.department === department) && matches(query, [v.product, v.product_id, v.variant_id]);
  });
  return (
    <>
      <ViewHead title="Stock & holds" note={`On hand per node (${view.nodes.join(", ")}) minus live holds = available. Holds are ${since(view.started_at)}.`} />
      <div className="section-head">
        <h2>Active holds</h2>
        <span className="muted">
          {count(view.holds.length)} holds · {count(units)} units held
        </span>
      </div>
      {view.holds.length > 0 ? (
        <table className="staff-table">
          <thead>
            <tr>
              <th>Order</th>
              <th>Variant</th>
              <th>Product</th>
              <th>Node</th>
              <th>Qty</th>
              <th>Status</th>
              <th>Expires</th>
            </tr>
          </thead>
          <tbody>
            {view.holds.map((h) => (
              <tr key={`${h.order_id}-${h.variant_id}-${h.node}`}>
                <td className="mono">{h.order_id}</td>
                <td className="mono">{h.variant_id}</td>
                <td>{h.product}</td>
                <td className="mono">{h.node}</td>
                <td>{h.quantity}</td>
                <td>{h.status}</td>
                <td className="mono">{clock(h.expires_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No stock holds {since(view.started_at)}.</Empty>
      )}
      <div className="section-head">
        <h2>Product & variant stock ({view.nodes.join(", ")})</h2>
        <span className="muted">{count(view.variants.length)} variants</span>
      </div>
      <Toolbar>
        <Segmented
          label="Variants"
          options={[
            { value: "all", label: "All variants" },
            { value: "held", label: "With active holds" },
            { value: "low", label: `Low stock (<= ${LOW_STOCK})` },
          ]}
          value={filter}
          onChange={(v) => {
            setFilter(v);
            setLimit(PAGE);
          }}
        />
        <Picker label="All departments" options={departments} value={department} onChange={setDepartment} />
        <SearchField label="Search product, product ID, variant" value={query} onChange={setQuery} />
      </Toolbar>
      {shown.length > 0 ? (
        <table className="staff-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>Variant</th>
              {view.nodes.map((node) => [
                <th key={`${node}-on`}>{node} on hand</th>,
                <th key={`${node}-held`}>{node} holds</th>,
                <th key={`${node}-avail`}>{node} avail</th>,
              ])}
              <th>Total avail</th>
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, limit).map((v) => (
              <tr key={v.variant_id}>
                <td>
                  {v.product}
                  <span className="cell-sub">
                    {v.product_id} · {v.department}
                  </span>
                </td>
                <td className="mono">{v.variant_id}</td>
                {view.nodes.map((node) => [
                  <td key={`${node}-on`}>{v.nodes[node]?.on_hand ?? 0}</td>,
                  <td key={`${node}-held`}>{v.nodes[node]?.held ?? 0}</td>,
                  <td key={`${node}-avail`}>
                    <b>{avail(v, node)}</b>
                  </td>,
                ])}
                <td>
                  <b>{total(v)}</b>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>No variants match these filters.</Empty>
      )}
      <More shown={Math.min(limit, shown.length)} total={shown.length} onMore={() => setLimit((n) => n + PAGE)} noun="variants" />
    </>
  );
}
