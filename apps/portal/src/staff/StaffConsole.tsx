// OPS-1: the demo staff console (docs/design/staff/), read-only, with no staff sign-in. It loads only
// after the gateway answered `/api/staff/overview`, which exists only with DEMO_STAFF_DASHBOARD=1.
import { useEffect, useState } from "react";
import { AgentSystem } from "./AgentSystem";
import { count } from "./format";
import type { Loaded, Loader, Overview, StaffViews, ViewName } from "./types";
import { ActivityViewPage, HandoffsViewPage, OrdersViewPage, OverviewView, StockViewPage } from "./views";

export const CONSOLE_NOTICE = "Demo console · read-only";
export const SIDEBAR_NOTE = "Read-only demo console: there are no staff actions here, and no staff sign-in.";

const NAV: { view: ViewName; label: string }[] = [
  { view: "system", label: "Agent system" },
  { view: "overview", label: "Overview" },
  { view: "activity", label: "Agent activity" },
  { view: "handoffs", label: "Handoffs" },
  { view: "orders", label: "Orders & escalations" },
  { view: "stock", label: "Stock & holds" },
];

export function staffHref(view: ViewName): string {
  return `#/staff/${view}`;
}

function badge(view: ViewName, overview: Overview): number | null {
  if (view === "handoffs") return overview.handoffs.open;
  if (view === "orders") return overview.orders.escalated;
  return null;
}

function Body<V extends ViewName>({ view, data }: { view: V; data: StaffViews[V] }) {
  // The cast is the switch's: each case narrows `view`, and `data` is that view's payload.
  const d = data as StaffViews[ViewName];
  switch (view) {
    case "system":
      return <AgentSystem view={d as StaffViews["system"]} />;
    case "overview":
      return <OverviewView view={d as StaffViews["overview"]} />;
    case "activity":
      return <ActivityViewPage view={d as StaffViews["activity"]} />;
    case "handoffs":
      return <HandoffsViewPage view={d as StaffViews["handoffs"]} />;
    case "orders":
      return <OrdersViewPage view={d as StaffViews["orders"]} />;
    case "stock":
      return <StockViewPage view={d as StaffViews["stock"]} />;
  }
  return null;
}

export function StaffConsole({ view, overview, load }: { view: ViewName; overview: Overview; load: Loader }) {
  const [state, setState] = useState<{ view: ViewName; loaded: Loaded<StaffViews[ViewName]> }>({ view, loaded: { kind: "loading" } });

  useEffect(() => {
    let live = true;
    void load(view).then((data) => {
      if (live) setState({ view, loaded: data === null ? { kind: "failed" } : { kind: "ready", data } });
    });
    return () => {
      live = false;
    };
  }, [view, load]);

  const loaded = state.view === view ? state.loaded : { kind: "loading" as const };

  return (
    <div className="staff">
      <div className="staff-topbar">
        <p className="staff-notice">{CONSOLE_NOTICE}</p>
        <p className="muted">
          Catalog {count(overview.catalog.products)} · Variants {count(overview.catalog.variants)} · Orders {count(overview.catalog.orders)}
        </p>
      </div>
      <header className="staff-masthead">
        <a className="wordmark" href={staffHref("system")}>
          Northwind staff
        </a>
      </header>
      <div className="staff-body">
        <nav className="staff-nav" aria-label="Staff console">
          <ul>
            {NAV.map((item) => {
              const n = badge(item.view, overview);
              return (
                <li key={item.view}>
                  <a href={staffHref(item.view)} aria-current={item.view === view ? "page" : undefined}>
                    <span>{item.label}</span>
                    {n !== null && n > 0 && <span className="nav-count">{count(n)}</span>}
                  </a>
                </li>
              );
            })}
          </ul>
          <p className="staff-nav-note">{SIDEBAR_NOTE}</p>
        </nav>
        <main className="staff-main">
          {loaded.kind === "loading" && <p className="muted">Loading…</p>}
          {loaded.kind === "failed" && (
            <p role="alert" className="empty">
              This view could not be read right now. Nothing was changed; try again shortly.
            </p>
          )}
          {loaded.kind === "ready" && <Body view={view} data={loaded.data} />}
        </main>
      </div>
    </div>
  );
}

export default StaffConsole;
