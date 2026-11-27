import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Root } from "../App";
import { href, parseHash } from "../router";
import { FIXTURE } from "./fixtures";
import { KIND_LABEL } from "./format";
import { CONSOLE_NOTICE, StaffConsole } from "./StaffConsole";
import { STAFF_UNAVAILABLE } from "./StaffGate";
import type { Loader, StaffViews, ViewName } from "./types";

const load: Loader = (view) => Promise.resolve(FIXTURE[view]);

function renderConsole(view: ViewName, views: Partial<StaffViews> = {}) {
  const custom: Loader = (v) => Promise.resolve({ ...FIXTURE, ...views }[v]);
  return render(<StaffConsole view={view} overview={FIXTURE.overview} load={Object.keys(views).length ? custom : load} />);
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("[OPS-1] staff console routes", () => {
  it("parses every staff view and refuses anything else", () => {
    expect(parseHash("#/staff")).toEqual({ page: "staff", view: "system" });
    for (const view of ["system", "overview", "activity", "handoffs", "orders", "stock"] as const) {
      expect(parseHash(`#/staff/${view}`)).toEqual({ page: "staff", view });
      expect(href({ page: "staff", view })).toBe(`#/staff/${view}`);
    }
    expect(parseHash("#/staff/admin")).toEqual({ page: "not-found" });
    expect(parseHash("#/staff/orders/O000001")).toEqual({ page: "not-found" });
  });
});

describe("[OPS-1] the gate: the console exists only when the gateway answers", () => {
  it("shows the shop's not-found page, and loads no console, when /api/staff answers 404 (flag off)", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response("{\"detail\":\"Not Found\"}", { status: 404 })));
    vi.stubGlobal("fetch", fetchMock);
    window.location.hash = "#/staff/overview";
    render(<Root />);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.queryByText(CONSOLE_NOTICE)).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Staff console" })).not.toBeInTheDocument();
    // One read, the gate's: no other staff view, and no shopper session or cart read.
    expect(fetchMock.mock.calls.map((call) => String((call as unknown[])[0]))).toEqual(["/api/staff/overview"]);
  });

  it("says the console is unavailable when the gateway fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("down"))));
    window.location.hash = "#/staff";
    render(<Root />);
    expect(await screen.findByRole("alert")).toHaveTextContent(STAFF_UNAVAILABLE);
  });

  it("loads the console when the gateway answers 200, with only GET reads", async () => {
    const fetchMock = vi.fn((url: string) => {
      const view = url.replace("/api/staff/", "") as ViewName;
      return Promise.resolve(Response.json(FIXTURE[view]));
    });
    vi.stubGlobal("fetch", fetchMock);
    window.location.hash = "#/staff/overview";
    render(<Root />);
    expect(await screen.findByText(CONSOLE_NOTICE)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
    for (const call of fetchMock.mock.calls) {
      const init = (call as unknown[])[1] as RequestInit | undefined;
      expect(init?.method ?? "GET").toBe("GET");
      expect(String((call as unknown[])[0])).toMatch(/^\/api\/staff\//);
    }
  });
});

describe("[OPS-1] the console's chrome", () => {
  it("says 'Demo console · read-only' and never claims a staff sign-in", async () => {
    renderConsole("system");
    expect(screen.getByText("Demo console · read-only")).toBeInTheDocument();
    await screen.findByRole("heading", { name: "Agent system" });
    expect(document.body.textContent).not.toMatch(/signed in as/i);
    expect(screen.queryByRole("button", { name: /sign in|log in/i })).not.toBeInTheDocument();
  });

  it("lists the six views and marks the current one", async () => {
    renderConsole("orders");
    const nav = screen.getByRole("navigation", { name: "Staff console" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "#/staff/system",
      "#/staff/overview",
      "#/staff/activity",
      "#/staff/handoffs",
      "#/staff/orders",
      "#/staff/stock",
    ]);
    expect(within(nav).getByRole("link", { current: "page" })).toHaveTextContent("Orders & escalations");
    await screen.findByRole("heading", { name: "Orders & escalations" });
  });

  it("shows the counts from the overview, not numbers of its own", () => {
    renderConsole("system");
    const nav = screen.getByRole("navigation", { name: "Staff console" });
    expect(within(nav).getByRole("link", { name: /Orders & escalations/ })).toHaveTextContent(String(FIXTURE.overview.orders.escalated));
    expect(within(nav).getByRole("link", { name: /Handoffs/ })).toHaveTextContent(String(FIXTURE.overview.handoffs.open));
    expect(screen.getByText(/Orders 1,000/)).toBeInTheDocument();
  });

  it("says so when a view cannot be read, instead of showing an empty view", async () => {
    render(<StaffConsole view="handoffs" overview={FIXTURE.overview} load={() => Promise.resolve(null)} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be read/);
  });
});

describe("[OPS-1] the agent system view", () => {
  it("draws every node and its legend from the map it is given", async () => {
    renderConsole("system");
    await screen.findByRole("heading", { name: "Agent system" });
    for (const node of FIXTURE.system.map.nodes) {
      expect(document.querySelector(`[data-node="${node.id}"]`)).not.toBeNull();
    }
    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual(FIXTURE.system.map.legend.map((k) => KIND_LABEL[k]));
    expect(screen.getByText("retrieve_context")).toBeInTheDocument();
    expect(screen.getAllByText("AI · Sonnet").length).toBeGreaterThan(0);
  });

  it("marks the traces as sample data and highlights exactly the selected trace's path", async () => {
    renderConsole("system");
    const traces = await screen.findByRole("region", { name: "Trace one action" });
    expect(within(traces).getAllByText("Sample data").length).toBeGreaterThan(0);
    expect(document.querySelector(".is-lit")).toBeNull();

    const checkout = FIXTURE.system.traces.find((t) => t.id === "checkout");
    if (!checkout) throw new Error("fixture lost its checkout trace");
    fireEvent.click(within(traces).getByRole("button", { name: /Web checkout/ }));
    const lit = [...document.querySelectorAll(".is-lit")].map((el) => el.getAttribute("data-node"));
    expect(lit.sort()).toEqual(checkout.steps.map((s) => s.node).sort());
    expect(within(traces).getByText("No AI on this path")).toBeInTheDocument();
    expect(within(traces).getByRole("list")).toBeInTheDocument();

    fireEvent.click(within(traces).getByRole("button", { name: /Web checkout/ }));
    expect(document.querySelector(".is-lit")).toBeNull();
  });

  it("shows no invented timings on a trace", async () => {
    renderConsole("system");
    await screen.findByRole("region", { name: "Trace one action" });
    expect(document.body.textContent).not.toMatch(/\d+\s?ms\b/);
  });
});

describe("[OPS-1] the table views", () => {
  it("overview: four tiles and the latest events", async () => {
    renderConsole("overview");
    expect(await screen.findByText("Escalated orders")).toBeInTheDocument();
    expect(screen.getByText("Live stock holds")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all activity" })).toHaveAttribute("href", "#/staff/activity");
    expect(screen.getAllByRole("row")).toHaveLength(FIXTURE.overview.latest.length + 1);
  });

  it("orders: escalated first, and the filters keep only their rows", async () => {
    renderConsole("orders");
    await screen.findByRole("heading", { name: "Orders & escalations" });
    const firstCells = () => screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0]?.textContent);
    const escalated = FIXTURE.orders.rows.filter((r) => r.escalated).length;
    expect(firstCells().slice(0, escalated).every((id) => FIXTURE.orders.rows.find((r) => r.order_id === id)?.escalated)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Address problem" }));
    expect(firstCells().sort()).toEqual(FIXTURE.orders.rows.filter((r) => r.reason === "address_error").map((r) => r.order_id).sort());

    fireEvent.click(screen.getByRole("button", { name: "Processed" }));
    expect(firstCells().sort()).toEqual(FIXTURE.orders.rows.filter((r) => r.reason === null).map((r) => r.order_id).sort());
  });

  it("orders: shows a page at a time", async () => {
    const rows = Array.from({ length: 120 }, (_, i) => ({ ...FIXTURE.orders.rows[0]!, order_id: `O${String(i + 1).padStart(6, "0")}` }));
    renderConsole("orders", { orders: { counts: FIXTURE.orders.counts, rows } });
    await screen.findByRole("heading", { name: "Orders & escalations" });
    expect(screen.getAllByRole("row")).toHaveLength(51);
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getAllByRole("row")).toHaveLength(101);
  });

  it("handoffs: shows the summary with who wrote it", async () => {
    renderConsole("handoffs");
    const handoff = FIXTURE.handoffs.handoffs[0]!;
    expect(await screen.findByText(handoff.summary)).toBeInTheDocument();
    expect(screen.getByText(handoff.summary_label)).toBeInTheDocument();
    expect(screen.getByText("$244.00")).toBeInTheDocument();
  });

  it("stock: on hand minus holds is available, and the low-stock filter keeps only low rows", async () => {
    renderConsole("stock");
    await screen.findByRole("heading", { name: "Stock & holds" });
    const row = screen.getAllByRole("row").find((r) => r.textContent?.includes("P0001-BLK-REG-L") && r.textContent.includes("Jackets"));
    expect(row?.textContent).toContain("63"); // DC-EAST: 65 on hand, 2 held
    fireEvent.click(screen.getByRole("button", { name: "Low stock (<= 5)" }));
    expect(screen.queryByText("P0001-BLK-REG-L", { selector: "td" })).toBeInTheDocument(); // still in the holds table
    expect(screen.getByText("P0001-FOR-REL-2XL")).toBeInTheDocument();
  });

  it("every empty store reads as empty, never as an error", async () => {
    renderConsole("activity", { activity: { started_at: FIXTURE.activity.started_at, events: [] } });
    expect(await screen.findByText(/No agent events since this server started/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("[OPS-1] text only", () => {
  it("renders hostile text as text, never as markup", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    const handoffs = { started_at: FIXTURE.handoffs.started_at, handoffs: [{ ...FIXTURE.handoffs.handoffs[0]!, summary: hostile }] };
    renderConsole("handoffs", { handoffs });
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("uses no raw-HTML escape hatch anywhere in the console", () => {
    const dir = import.meta.dirname;
    for (const file of readdirSync(dir).filter((f) => /\.tsx?$/.test(f) && !f.endsWith(".test.tsx"))) {
      const source = readFileSync(join(dir, file), "utf8");
      expect(source, file).not.toMatch(/dangerouslySetInnerHTML|innerHTML|outerHTML|insertAdjacentHTML/);
    }
  });
});

describe("[OPS-1] the console changes nothing", () => {
  it("has no write: no POST, PUT, PATCH or DELETE in its reads", () => {
    const dir = import.meta.dirname;
    for (const file of readdirSync(dir).filter((f) => /\.tsx?$/.test(f) && !f.endsWith(".test.tsx"))) {
      expect(readFileSync(join(dir, file), "utf8"), file).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)/);
    }
  });

  it("waits for its read before showing a view", async () => {
    let release: (value: StaffViews["orders"]) => void = () => {};
    const slow = ((view: ViewName) =>
      view === "orders" ? new Promise((resolve) => (release = resolve)) : Promise.resolve(FIXTURE[view])) as unknown as Loader;
    render(<StaffConsole view="orders" overview={FIXTURE.overview} load={slow} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    release(FIXTURE.orders);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Orders & escalations" })).toBeInTheDocument());
  });
});
