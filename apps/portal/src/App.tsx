import { type FormEvent, useCallback, useEffect, useState } from "react";
import type { CartState } from "./cartApi";
import { CARD_REFUSED, mightBeCardData } from "./cardGuard";
import { ChatPanel } from "./ChatPanel";
import { CheckoutPage, OrdersPage } from "./checkoutPages";
import { DepartmentTabs } from "./DepartmentTabs";
import {
  CartPage,
  DepartmentPage,
  HomePage,
  NotFoundPage,
  ProductPage,
  SearchPage,
} from "./pages";
import { href, type Route, useRoute } from "./router";
import { type Departments, fetchDepartments, type Loaded } from "./shopApi";
import { SUGGESTIONS } from "./suggestions";

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

function Page({ route, cart, onCartChanged }: { route: Route; cart: CartState; onCartChanged: () => void }) {
  switch (route.page) {
    case "home":
      return <HomePage />;
    case "department":
      return <DepartmentPage name={route.name} />;
    case "search":
      return <SearchPage q={route.q} />;
    case "product":
      return <ProductPage id={route.id} onCartChanged={onCartChanged} />;
    case "cart":
      return <CartPage cart={cart} onCartChanged={onCartChanged} />;
    case "checkout":
      return <CheckoutPage onCartChanged={onCartChanged} />;
    case "orders":
      return <OrdersPage />;
    case "not-found":
      return <NotFoundPage />;
  }
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
      <input aria-label="Search products" type="search" value={q} maxLength={64} onChange={(e) => setQ(e.target.value)} placeholder="Search jackets, fleece, bedding…" />
      <button type="submit" className="search-button">
        Search
      </button>
      {refused && <p role="alert">{CARD_REFUSED}</p>}
    </form>
  );
}

export function App() {
  const route = useRoute();
  const [health, setHealth] = useState<Health>({ state: "loading" });
  const [departments, setDepartments] = useState<Loaded<Departments>>({ kind: "loading" });
  const [cart, setCart] = useState<CartState>({ kind: "loading" });
  // SA-01 PR 2: the assistant is a slide-in side panel that narrows the page (assistant-panel.png;
  // look round 2). On desktop it starts open, so the AI disclosure is on screen from the first load
  // (NFR-08, and every journey's closing check); Close slides it away and ASK NORTHWIND brings it
  // back. On phone it starts closed and opens full screen.
  const [chatOpen, setChatOpen] = useState(() => window.matchMedia?.("(min-width: 900px)").matches ?? false);

  // The chat panel's cart read is the first request on a session path; the page and the nav wait
  // for it, so a first visit opens one session, not three (security review F3).
  const sessionReady = cart.kind !== "loading";

  useEffect(() => {
    void fetchHealth().then(setHealth);
  }, []);

  useEffect(() => {
    if (sessionReady) void fetchDepartments().then(setDepartments);
  }, [sessionReady]);

  // SA-01 PR 2: after a shop page changes the bag, the chat panel reads it again; its read also
  // sets the bag count (`onCart`), so the panel and the count never disagree.
  const [cartRound, setCartRound] = useState(0);
  const refreshCart = useCallback(() => setCartRound((n) => n + 1), []);
  const count = cart.kind === "ready" ? cart.cart.item_count : null;

  return (
    <div className={chatOpen ? "shell chat-open" : "shell"}>
      <div className="page">
        <header className="masthead">
          <a className="wordmark" href="#/">
            NORTHWIND
          </a>
          <SearchBox key={route.page === "search" ? route.q : ""} initial={route.page === "search" ? route.q : ""} />
          <div className="masthead-links">
            <a className="masthead-link" href={href({ page: "orders" })}>
              Orders
            </a>
            <a className="bag-link" href={href({ page: "cart" })} aria-label={count === null ? "Bag" : `Bag, ${count} items`}>
              Bag <span className="bag-count">{count ?? "–"}</span>
            </a>
          </div>
        </header>
        <DepartmentTabs departments={departments} route={route} />
        <main className="content">
          {sessionReady ? <Page route={route} cart={cart} onCartChanged={refreshCart} /> : <p className="muted">Loading…</p>}
        </main>
        <footer className="footer">
          <div>
            <p>NORTHWIND — demo storefront</p>
            <p role="status" className="health">
              {label(health)}
            </p>
          </div>
          <p className="banner">{BANNER}</p>
        </footer>
      </div>
      <aside className="chat-dock" aria-label="Ask Northwind" inert={!chatOpen}>
        <div className="chat-dock-head">
          <h2>Northwind AI assistant</h2>
          <button type="button" className="btn btn-outline chat-close" onClick={() => setChatOpen(false)}>
            Close
          </button>
        </div>
        <ChatPanel onCart={setCart} suggestions={SUGGESTIONS} round={cartRound} />
      </aside>
      <button type="button" className="btn btn-short chat-button" aria-expanded={chatOpen} onClick={() => setChatOpen(true)}>
        ASK NORTHWIND
      </button>
    </div>
  );
}
