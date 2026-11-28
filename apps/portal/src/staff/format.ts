// OPS-1: display helpers for the staff console. Pure functions, so the tests can use them too.
import type { Money, NodeKind } from "./types";

/** The order store's exception codes, in the words staff read. An unknown code shows as itself. */
export const REASONS: Record<string, string> = {
  // The seeded orders' exceptions.
  fraud_flag: "Fraud review",
  address_error: "Address problem",
  soft_decline: "Soft decline",
  stock_out: "Stock out",
  // Pay (demo)'s escalation codes (the checkout's order store).
  fraud_review: "Fraud review",
  out_of_stock: "Out of stock",
  placement_error: "Placement error",
};

export function reasonLabel(code: string | null): string {
  if (code === null) return "None";
  return REASONS[code] ?? code;
}

export const KIND_LABEL: Record<NodeKind, string> = {
  entry: "Entry point",
  ai: "AI agent (AI · Sonnet / AI · Haiku)",
  mcp: "MCP server (MCP)",
  code: "Code (not a tool)",
  fake: "Fake service",
  eval: "Eval only (greyed)",
  end: "End of a path",
};

export function count(n: number): string {
  return n.toLocaleString("en-US");
}

export function clock(iso: string | null): string {
  if (iso === null) return "–";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "–";
  return at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function shortClock(iso: string | null): string {
  return iso === null ? "–" : clock(iso).slice(0, 5);
}

export function money(value: Money | null): string {
  if (value === null) return "–";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: value.currency }).format(value.minor / 100);
  } catch {
    return `${(value.minor / 100).toFixed(2)} ${value.currency}`;
  }
}

/** "1 (2 units)" for one line of two units. */
export function items(lines: number, units: number): string {
  return `${lines} (${units} ${units === 1 ? "unit" : "units"})`;
}

export function since(iso: string): string {
  return `since this server started at ${shortClock(iso)}`;
}

/** Case-insensitive match of a search box against any of a row's fields. */
export function matches(query: string, fields: (string | null | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  if (q === "") return true;
  return fields.some((field) => (field ?? "").toLowerCase().includes(q));
}
