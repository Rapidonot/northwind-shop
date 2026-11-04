import { useEffect, useState } from "react";
import { Chat } from "./Chat";

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

export function App() {
  const [health, setHealth] = useState<Health>({ state: "loading" });

  useEffect(() => {
    void fetchHealth().then(setHealth);
  }, []);

  return (
    <main>
      <h1>Northwind Shop</h1>
      <p role="status">{label(health)}</p>
      <Chat />
    </main>
  );
}
