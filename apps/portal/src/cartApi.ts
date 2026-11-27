// GET /api/cart (row #16, CHK-01): the shopper's cart as the gateway read it from mcp-catalog. The
// panel shows these values exactly as sent: prices and totals are the server's, read live.

import { sessionFetch } from "./session";

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
