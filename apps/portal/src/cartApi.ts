// GET /api/cart (row #16, CHK-01): the shopper's cart as the gateway read it from mcp-catalog. The
// panel shows these values exactly as sent: prices and totals are the server's, read live.

import { sessionFetch } from "./session";
import type { Money } from "./shopApi";

export type CartLine = {
  variant_id: string;
  product_id: string;
  name: string;
  colour: string;
  size: string;
  fit: string | null;
  quantity: number;
  unit_price: string | null;
  line_total: string | null;
  currency: string | null;
  on_hand: number;
  in_stock: boolean;
};

export type Cart = {
  region: string;
  lines: CartLine[];
  item_count: number;
  subtotal: string | null;
  currency: string | null;
};

export type CartState = { kind: "loading" } | { kind: "ready"; cart: Cart } | { kind: "unavailable" };

function isCart(value: unknown): value is Cart {
  if (typeof value !== "object" || value === null) return false;
  const cart = value as Record<string, unknown>;
  return Array.isArray(cart.lines) && typeof cart.item_count === "number";
}

/** Any failure is "unavailable", never an empty cart. */
export async function fetchCart(): Promise<CartState> {
  try {
    const response = await sessionFetch("/api/cart", { credentials: "same-origin" });
    if (!response.ok) return { kind: "unavailable" };
    const body = (await response.json()) as { cart?: unknown };
    return isCart(body.cart) ? { kind: "ready", cart: body.cart } : { kind: "unavailable" };
  } catch {
    return { kind: "unavailable" };
  }
}

// ---- SA-01 PR 2: the bag's writes, Pay (demo), the order history and the demo sign-in ----------

/** The portal guard's header (the gateway refuses a write without it, `portal_write.py`). */
const WRITE_HEADERS = { "Content-Type": "application/json", "X-Northwind-Portal": "1" } as const;

/** One key per click: 24 random letters, so it can never look like a card number. */
export function newKey(): string {
  const letters = "abcdefghijklmnopqrstuvwxyz";
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => letters[b % 26]).join("");
}

export type WriteResult<T = Record<string, unknown>> =
  | { ok: true; value: T }
  | { ok: false; status: number; code: string | null; message: string; body: Record<string, unknown> };

export const WRITE_FAILED = "That didn't work. Please try again.";

async function postJson<T>(url: string, body: unknown): Promise<WriteResult<T>> {
  try {
    const response = await sessionFetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: WRITE_HEADERS,
      body: JSON.stringify(body),
    });
    const parsed = (await response.json().catch(() => null)) as Record<string, unknown> | null;
    if (response.ok && parsed) return { ok: true, value: parsed as T };
    const message = typeof parsed?.message === "string" ? parsed.message : WRITE_FAILED;
    const code = typeof parsed?.code === "string" ? parsed.code : null;
    return { ok: false, status: response.status, code, message, body: parsed ?? {} };
  } catch {
    return { ok: false, status: 0, code: null, message: WRITE_FAILED, body: {} };
  }
}

export function addToBag(variantId: string, quantity = 1): Promise<WriteResult> {
  return postJson("/api/cart/items", { variant_id: variantId, quantity, idempotency_key: newKey() });
}

export function setQuantity(variantId: string, quantity: number): Promise<WriteResult> {
  return postJson("/api/cart/quantity", { variant_id: variantId, quantity, idempotency_key: newKey() });
}

export function removeFromBag(variantId: string): Promise<WriteResult> {
  return postJson("/api/cart/remove", { variant_id: variantId, idempotency_key: newKey() });
}

export type SummaryLine = {
  variant_id: string;
  product_id: string;
  name: string;
  colour: string;
  size: string;
  quantity: number;
  unit_price: Money | null;
  line_total: Money | null;
};

export type CheckoutSummary = {
  region: string;
  lines: SummaryLine[];
  total: Money | null;
  delivery: string;
  return_policy: string;
  issues: string[];
  digest: string;
  signed_in: boolean;
};

export type SummaryState = { kind: "loading" } | { kind: "ready"; summary: CheckoutSummary } | { kind: "unavailable" };

export async function fetchSummary(): Promise<SummaryState> {
  try {
    const response = await sessionFetch("/api/checkout/summary", { credentials: "same-origin" });
    if (!response.ok) return { kind: "unavailable" };
    const body = (await response.json()) as Partial<CheckoutSummary>;
    if (!Array.isArray(body.lines) || typeof body.digest !== "string" || !Array.isArray(body.issues)) return { kind: "unavailable" };
    return { kind: "ready", summary: body as CheckoutSummary };
  } catch {
    return { kind: "unavailable" };
  }
}

export type Placed = { result: "placed"; order_id: string; total: Money; bag_emptied: boolean };

/** Pay (demo): the shopper's press of the portal control (CHK-02); one key per press. */
export function payDemo(digest: string, key: string): Promise<WriteResult<Placed>> {
  return postJson<Placed>("/api/checkout/pay", { digest, idempotency_key: key });
}

export type OrderLine = { product_id: string; variant_id: string; quantity: number };
export type OrderEntry = {
  order_id: string;
  placed_on: string | null;
  status: string;
  status_text: string;
  lines: OrderLine[];
  total?: Money;
  demo?: boolean;
};
export type OrdersState =
  | { kind: "loading" }
  | { kind: "ready"; orders: OrderEntry[] }
  | { kind: "signed-out" }
  | { kind: "unavailable" };

export async function fetchOrders(): Promise<OrdersState> {
  try {
    const response = await sessionFetch("/api/orders", { credentials: "same-origin" });
    if (response.status === 401) return { kind: "signed-out" };
    if (!response.ok) return { kind: "unavailable" };
    const body = (await response.json()) as { orders?: unknown };
    return Array.isArray(body.orders) ? { kind: "ready", orders: body.orders as OrderEntry[] } : { kind: "unavailable" };
  } catch {
    return { kind: "unavailable" };
  }
}

/** CHK-06's demo sign-in (only while the gateway's demo flag is on): the one synthetic shopper. */
export async function signInDemo(): Promise<boolean> {
  try {
    const response = await sessionFetch("/api/session/shopper", { method: "POST", credentials: "same-origin" });
    return response.ok;
  } catch {
    return false;
  }
}
