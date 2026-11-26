import { type FormEvent, useEffect, useState } from "react";
import type { CartState } from "./cartApi";
import { CARD_REFUSED, mightBeCardData } from "./cardGuard";
import { ChatPanel } from "./ChatPanel";
import {
  CartPage,
  DepartmentPage,
  HomePage,
  NotFoundPage,
  PlaceholderPage,
  ProductPage,
  SearchPage,
} from "./pages";
import { href, type Route, useRoute } from "./router";
import { fetchDepartments, type Loaded } from "./shopApi";

export const BANNER = "Demo store. AI assistant. No real orders, payments or deliveries.";

type Health =
  | { state: "loading" }
  | { state: "ok"; pgvector: string | null }
  | { state: "db-unavailable" }
  | { state: "gateway-unavailable" };

async function fetchHealth(): Promise<Health> {
  try {
    const res = await fetch("/api/healthz");
    if (!res.ok) return { state: "gateway-unavailable" };
    const body = (await res.json()) as { db: "ok" | "unavailable"; pgvector: string | null };
    return body.db === "ok" ? { state: "ok", pgvector: body.pgvector } : { state: "db-unavailable" };
  } catch {
    return { state: "gateway-unavailable" };
  }
}

function label(health: Health): string {
  switch (health.state) {
    case "loading":
      return "checking…";
    case "ok":
      return `gateway: ok · pgvector ${health.pgvector ?? "missing"}`;
    case "db-unavailable":
      return "gateway: ok · db unavailable";
    case "gateway-unavailable":
      return "gateway unavailable";
  }
}

function Page({ route, cart }: { route: Route; cart: CartState }) {
  switch (route.page) {
    case "home":
      return <HomePage />;
    case "department":
      return <DepartmentPage name={route.name} />;
    case "search":
      return <SearchPage q={route.q} />;
    case "product":
      return <ProductPage id={route.id} />;
    case "cart":
      return <CartPage cart={cart} />;
    case "checkout":
      return <PlaceholderPage heading="Checkout" />;
    case "orders":
      return <PlaceholderPage heading="Orders" />;
    case "not-found":
      return <NotFoundPage />;
  }
}

/** The top nav is the only department picker (rule 1 of the trial build): every department, the
 * selected one underlined. */
function DepartmentNav({ departments, selected }: { departments: Loaded<string[]>; selected: string | null }) {
  if (departments.kind !== "ready") return <nav aria-label="Departments" className="departments" />;
  return (
    <nav aria-label="Departments" className="departments">
      <ul>
        {departments.value.map((name) => (
          <li key={name}>
            <a href={href({ page: "department", name })} aria-current={name === selected ? "page" : undefined}>
              {name}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function SearchBox({ initial }: { initial: string }) {
  const [q, setQ] = useState(initial);
  const [refused, setRefused] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    // Card data never goes into a URL (security review F1, hard rule 1).
    if (mightBeCardData(q)) {
      setRefused(true);
      setQ("");
      return;
    }
    setRefused(false);
    window.location.hash = href({ page: "search", q: q.trim() });
  };
  return (
    <form role="search" className="search" onSubmit={submit}>
      <input aria-label="Search products" type="search" value={q} maxLength={64} onChange={(e) => setQ(e.target.value)} placeholder="Search" />
      {refused && <p role="alert">{CARD_REFUSED}</p>}
    </form>
  );
}

export function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health>({ state: "loading" });
  const [departments, setDepartments] = useState<Loaded<string[]>>({ kind: "loading" });
  const [cart, setCart] = useState<CartState>({ kind: "loading" });
  const [chatOpen, setChatOpen] = useState(false);

  // The chat panel's cart read is the first request on a session path; the page and the nav wait
  // for it, so a first visit opens one session, not three (security review F3).
  const sessionReady = cart.kind !== "loading";

  useEffect(() => {
    void fetchHealth().then(setHealth);
  }, []);

  useEffect(() => {
    if (sessionReady) void fetchDepartments().then(setDepartments);
  }, [sessionReady]);

  const count = cart.kind === "ready" ? cart.cart.item_count : null;
  const selected = route.page === "department" ? route.name : null;

  return (
    <div className={chatOpen ? "shell chat-open" : "shell"}>
      <p className="banner">{BANNER}</p>
      <header className="masthead">
        <a className="wordmark" href="#/">
          NORTHWIND
        </a>
        <SearchBox key={route.page === "search" ? route.q : ""} initial={route.page === "search" ? route.q : ""} />
        <a className="masthead-link" href={href({ page: "orders" })}>
          Orders
        </a>
        <a className="bag-link" href={href({ page: "cart" })} aria-label={count === null ? "Bag" : `Bag, ${count} items`}>
          Bag <span className="bag-count">{count ?? "–"}</span>
        </a>
      </header>
      <DepartmentNav departments={departments} selected={selected} />
      <div className="layout">
        <main className="content">
          {sessionReady ? <Page route={route} cart={cart} /> : <p className="muted">Loading…</p>}
        </main>
        <aside className="chat-dock" aria-label="Ask Northwind">
          <div className="chat-dock-head">
            <h2>Ask Northwind</h2>
            <button type="button" className="btn btn-short chat-close" onClick={() => setChatOpen(false)}>
              CLOSE
            </button>
          </div>
          <ChatPanel onCart={setCart} />
        </aside>
      </div>
      <button type="button" className="btn btn-short chat-button" aria-expanded={chatOpen} onClick={() => setChatOpen(true)}>
        ASK NORTHWIND
      </button>
      <footer className="footer">
        <p role="status">{label(health)}</p>
      </footer>
    </div>
  );
}
