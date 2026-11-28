import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Root } from "../App";
import { href, parseHash } from "../router";
import { FIXTURE } from "./fixtures";
import { count, KIND_LABEL, REASONS } from "./format";
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
  it("is one SVG with no HTML boxes, every node and a line for every edge", async () => {
    renderConsole("system");
    await screen.findByRole("heading", { name: "Agent system" });
    const svg = screen.getByRole("img", { name: "Agent system map" });
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg.getAttribute("viewBox")).toBe("0 0 1160 778");
    expect(svg.getAttribute("width")).toBe("100%");
    expect(svg.getAttribute("style")).toMatch(/min-width:\s*960px/);
    expect(svg.closest(".map-scroll")).not.toBeNull();
    for (const node of FIXTURE.system.map.nodes) {
      const g = svg.querySelector(`[data-node="${node.id}"]`);
      expect(g?.tagName.toLowerCase(), node.id).toBe("g");
    }
    expect(document.querySelector("div.map-node")).toBeNull();
    const drawn = [...svg.querySelectorAll("[data-edge]")].map((g) => g.getAttribute("data-edge"));
    expect(drawn.sort()).toEqual(FIXTURE.system.map.edges.map((e) => `${e.from}->${e.to}`).sort());
    expect([...svg.querySelectorAll(".map-separator")].map((l) => l.getAttribute("y1"))).toEqual(["292", "416", "532", "696"]);
    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual(FIXTURE.system.map.legend.map((k) => KIND_LABEL[k]));
  });

  it("draws a dashed line only for the scoped call, and each shared label once", async () => {
    renderConsole("system");
    await screen.findByRole("heading", { name: "Agent system" });
    const dashed = [...document.querySelectorAll("[data-edge] path[stroke-dasharray]")].map((p) => p.parentElement?.getAttribute("data-edge"));
    expect(dashed).toEqual(["query-understanding->northwind-catalog"]);
    const labels = [...document.querySelectorAll(".map-edge-label")].map((t) => t.textContent);
    expect(labels).toContain("eligible_products");
    expect(labels).toContain("escalation case");
    expect(labels.filter((l) => l === "code port")).toHaveLength(1);
  });

  it("lists the catalog's reads and folds its cart writes into one line", async () => {
    renderConsole("system");
    await screen.findByRole("heading", { name: "Agent system" });
    const tools = document.querySelector('[data-node="northwind-catalog"] .map-node-tools');
    const text = (tools?.textContent ?? "").replace(/add_to_cart, remove_from_cart, set_cart_quantity/, "");
    for (const read of ["search_products", "get_product", "get_variant", "check_stock", "get_price", "get_cart", "eligible_products"]) expect(text).toContain(read);
    expect(text).toContain("+ cart write tools");
    expect(text).not.toMatch(/add_to_cart|remove_from_cart|set_cart_quantity/);
    expect(tools?.querySelector(".map-node-more title")?.textContent).toBe("add_to_cart, remove_from_cart, set_cart_quantity");
  });

  it("dims nothing in the full architecture view", async () => {
    renderConsole("system");
    await screen.findByRole("heading", { name: "Agent system" });
    expect(document.querySelector(".is-dim, .is-lit")).toBeNull();
    expect(document.querySelector('[data-node="northwind-orders"]')?.getAttribute("class")).toBe("map-node map-node-mcp");
  });

  it("marks the traces as sample data and highlights exactly the selected trace's path", async () => {
    renderConsole("system");
    const traces = await screen.findByRole("region", { name: "Trace one action" });
    expect(within(traces).getAllByText("Sample data").length).toBeGreaterThan(0);

    const checkout = FIXTURE.system.traces.find((t) => t.id === "checkout");
    if (!checkout) throw new Error("fixture lost its checkout trace");
    fireEvent.click(within(traces).getByRole("button", { name: /Web checkout/ }));
    const path = new Set(checkout.steps.map((s) => s.node));
    const lit = [...document.querySelectorAll(".map-node.is-lit")].map((el) => el.getAttribute("data-node"));
    expect(lit.sort()).toEqual([...path].sort());
    const litEdges = [...document.querySelectorAll(".map-edge.is-lit")].map((g) => g.getAttribute("data-edge"));
    expect(litEdges.sort()).toEqual(FIXTURE.system.map.edges.filter((e) => path.has(e.from) && path.has(e.to)).map((e) => `${e.from}->${e.to}`).sort());
    expect(within(traces).getByText("No AI on this path")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Full architecture" }));
    expect(document.querySelector(".is-lit, .is-dim")).toBeNull();
  });

  it("lights the damaged-parcel trace's chat part first, then the handoff, in that order", async () => {
    renderConsole("system");
    const traces = await screen.findByRole("region", { name: "Trace one action" });
    fireEvent.click(within(traces).getByRole("button", { name: /Damaged parcel/ }));
    const steps = within(traces).getAllByRole("listitem").map((li) => li.querySelector(".trace-step-node")?.textContent);
    expect(steps).toEqual([
      "Shopper chat",
      "Query understanding",
      "Shopper Assistant",
      "northwind-orders",
      "northwind-policy",
      "Output check",
      "Answer",
      "Talk to a person",
      "Handoff trigger",
      "Handoff desk",
      "Care agent",
      "Case queue",
    ]);
    expect(within(traces).getByText("if the shopper presses Talk to a person")).toBeInTheDocument();
    const lit = new Set([...document.querySelectorAll(".map-node.is-lit")].map((el) => el.getAttribute("data-node")));
    for (const id of ["shopper-chat", "shopper-assistant", "northwind-orders", "northwind-policy", "answer", "talk-to-person", "case-queue"]) expect(lit.has(id), id).toBe(true);
  });

  it("makes every trace a walk along drawn lines, from its entry point", () => {
    const edges = new Set(FIXTURE.system.map.edges.map((e) => `${e.from}->${e.to}`));
    const entries = new Set(FIXTURE.system.map.nodes.filter((n) => n.kind === "entry").map((n) => n.id));
    for (const trace of FIXTURE.system.traces) {
      const nodes = trace.steps.map((s) => s.node);
      expect(nodes[0], trace.id).toBe(trace.entry);
      // Each step is reached by a line from a step before it, or starts a new part at an entry point.
      nodes.forEach((node, i) => {
        if (i === 0 || entries.has(node)) return;
        const reached = nodes.slice(0, i).some((earlier) => edges.has(`${earlier}->${node}`));
        expect(reached, `${trace.id}: ${node}`).toBe(true);
      });
    }
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

    fireEvent.click(screen.getByRole("button", { name: /^Fraud review/ }));
    // Every escalated order whose reason is labelled "Fraud review" (seeded fraud_flag, Pay (demo)'s fraud_review).
    expect(firstCells().sort()).toEqual(FIXTURE.orders.rows.filter((r) => r.escalated && r.reason !== null && REASONS[r.reason] === "Fraud review").map((r) => r.order_id).sort());

    fireEvent.click(screen.getByRole("button", { name: /^Other exceptions/ }));
    expect(firstCells().sort()).toEqual(FIXTURE.orders.rows.filter((r) => r.reason !== null && !r.escalated).map((r) => r.order_id).sort());

    fireEvent.click(screen.getByRole("button", { name: /^Processed/ }));
    expect(firstCells().sort()).toEqual(FIXTURE.orders.rows.filter((r) => r.reason === null && !r.escalated).map((r) => r.order_id).sort());
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
    renderConsole("activity", { activity: { started_at: FIXTURE.activity.started_at, events: [], total: 0 } });
    expect(await screen.findByText(/No agent events since this server started/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// The user's look fix (2026-11-28): a tile's value and its note, and a filter chip and the chips
// under it, count the same whole.
const number = (text: string) => Number(text.replace(/,/g, ""));

function chips(group: HTMLElement): Map<string, number> {
  return new Map(
    within(group)
      .getAllByRole("button")
      .map((button) => {
        const found = /^(.*\S)\s+([\d,]+)$/.exec(button.textContent ?? "");
        if (!found) throw new Error(`a filter chip without a count: ${button.textContent}`);
        return [found[1]!, number(found[2]!)] as [string, number];
      }),
  );
}

function tile(title: string): { value: number; note: string } {
  const box = screen.getByText(title).closest(".tile-stat") as HTMLElement;
  return { value: number(box.querySelector(".tile-stat-value")!.textContent!), note: box.querySelector(".muted")!.textContent! };
}

describe("[OPS-1] every count adds up to the whole it sits under", () => {
  it("the escalated-orders tile shows its number and 'of N orders with an exception', and lists no reason", async () => {
    renderConsole("overview");
    await screen.findByText("Escalated orders");
    const { value, note } = tile("Escalated orders");
    expect(value).toBe(FIXTURE.overview.orders.escalated);
    expect(note).toBe(`of ${count(FIXTURE.overview.orders.exceptions)} orders with an exception`);
    for (const label of Object.values(REASONS)) expect(note.toLowerCase()).not.toContain(label.toLowerCase());
    expect(note).not.toContain("·");
  });

  it("each overview breakdown adds up to its tile's number", async () => {
    renderConsole("overview");
    await screen.findByText("Escalated orders");
    for (const title of ["Agent events", "Open handoffs"]) {
      const { value, note } = tile(title);
      const parts = note === "none yet" ? [] : note.split(" · ").map((part) => number(part.split(" ")[0]!));
      expect(parts.reduce((a, b) => a + b, 0), title).toBe(value);
    }
  });

  it("orders: the reason chips add up to Escalated; Escalated, Other exceptions and Processed add up to All", async () => {
    renderConsole("orders");
    await screen.findByRole("heading", { name: "Orders & escalations" });
    const shown = chips(screen.getByRole("group", { name: "Orders" }));
    const whole = ["All", "Escalated", "Other exceptions", "Processed"];
    const reasons = [...shown].filter(([label]) => !whole.includes(label));
    expect(reasons.length).toBeGreaterThan(0);
    expect(reasons.reduce((sum, [, n]) => sum + n, 0)).toBe(shown.get("Escalated"));
    expect(shown.get("Escalated")! + shown.get("Other exceptions")! + shown.get("Processed")!).toBe(shown.get("All"));
    expect(shown.get("All")).toBe(FIXTURE.orders.counts.total);
    // Only escalated orders are broken down: a reason that never escalates has no chip.
    expect(shown.has(REASONS.address_error!)).toBe(false);
    // Each chip's count is the rows it keeps.
    for (const [label, n] of shown) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${label}\\s`) }));
      expect(screen.queryAllByRole("row").slice(1).length, label).toBe(n);
    }
  });

  it("orders: codes that share a label are one chip, and an escalation with no code is a chip of its own", async () => {
    // advisor-reviewer re-review finding 3: a seeded fraud_flag and a Pay (demo) fraud_review, an
    // on-hold Pay (demo) order with no escalation code, an exception that did not escalate, and a
    // processed order.
    const base = FIXTURE.orders.rows[0]!;
    const rows = [
      { ...base, order_id: "O000002", reason: "fraud_flag", status: "on_hold", escalated: true, source: "store" as const },
      { ...base, order_id: "O900001", reason: "fraud_review", status: "on_hold", escalated: true, source: "demo" as const },
      { ...base, order_id: "O900002", reason: null, status: "on_hold", escalated: true, source: "demo" as const },
      { ...base, order_id: "O000024", reason: "soft_decline", status: "confirmed", escalated: false, source: "store" as const },
      { ...base, order_id: "O000001", reason: null, status: "picked_up", escalated: false, source: "store" as const },
    ];
    const counts = {
      total: 5,
      exceptions: 4,
      escalated: 3,
      other_exceptions: 1,
      processed: 1,
      escalated_by_reason: { fraud_flag: 1, fraud_review: 1, unrecorded: 1 },
    };
    renderConsole("orders", { orders: { counts, rows } });
    await screen.findByRole("heading", { name: "Orders & escalations" });
    const shown = chips(screen.getByRole("group", { name: "Orders" }));
    expect([...shown]).toEqual([
      ["All", 5],
      ["Escalated", 3],
      ["Fraud review", 2],
      ["No reason recorded", 1],
      ["Other exceptions", 1],
      ["Processed", 1],
    ]);
    const kept = (label: RegExp) => {
      fireEvent.click(screen.getByRole("button", { name: label }));
      return screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0]!.textContent!.replace("Pay (demo)", "")).sort();
    };
    expect(kept(/^Fraud review\s/)).toEqual(["O000002", "O900001"]);
    expect(kept(/^No reason recorded\s/)).toEqual(["O900002"]);
    expect(kept(/^Processed\s/)).toEqual(["O000001"]);
    expect(kept(/^Other exceptions\s/)).toEqual(["O000024"]);
  });

  it("orders: the stats bar shows only parts of the total, no per-reason stats", async () => {
    renderConsole("orders");
    await screen.findByRole("heading", { name: "Orders & escalations" });
    const bar = document.querySelector(".stats-bar") as HTMLElement;
    for (const label of Object.values(REASONS)) expect(bar.textContent).not.toContain(label);
    const stats = new Map(
      [...bar.querySelectorAll(".stat")].map((stat) => {
        const [label, value] = (stat.textContent ?? "").split(": ");
        return [label!, number(value!)] as [string, number];
      }),
    );
    expect([...stats.keys()]).toEqual(["Total orders", "Escalated", "Other exceptions", "Processed"]);
    expect(stats.get("Escalated")! + stats.get("Other exceptions")! + stats.get("Processed")!).toBe(stats.get("Total orders"));
  });
});

describe("[OPS-1] activity is capped, and says so", () => {
  it("shows the newest events and the total when the gateway sends fewer than it has", async () => {
    renderConsole("activity", { activity: { ...FIXTURE.activity, total: 600 } });
    expect(await screen.findByText(`Showing the newest ${FIXTURE.activity.events.length} of 600 events.`)).toBeInTheDocument();
  });

  it("says nothing extra when every event is shown", async () => {
    renderConsole("activity");
    await screen.findByRole("heading", { name: "Agent activity" });
    expect(screen.queryByText(/Showing the newest/)).not.toBeInTheDocument();
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
