// SA-01: the shop pages' reads. Prices are the gateway's, read live from the price table on every
// request (hard rule 2); the page shows them exactly as sent and never computes one. Any failure is
// "unavailable", never an empty list; an unknown product is "not-found".
import { mightBeCardData } from "./cardGuard";
import { sessionFetch } from "./session";

export type Money = { amount: string; currency: string };

export type Tile = {
  product_id: string;
  name: string;
  brand: string;
  department: string;
  from_price: Money | null;
  photo: boolean;
};

export type ProductVariant = {
  variant_id: string;
  colour: string;
  size: string;
  fit: string | null;
  price: Money | null;
};

export type Product = {
  product_id: string;
  name: string;
  brand: string;
  department: string;
  description: string;
  attributes: Record<string, unknown>;
  colours: string[];
  sizes: string[];
  region: string;
  variants: ProductVariant[];
  photo: boolean;
};

export type Loaded<T> = { kind: "loading" } | { kind: "ready"; value: T } | { kind: "unavailable" } | { kind: "not-found" };

function safeDecode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

async function getJson(url: string): Promise<{ status: number; body: unknown } | null> {
  // Never put possible card data in a URL (security review F1); the route guards this too.
  if (mightBeCardData(url) || mightBeCardData(safeDecode(url.replaceAll("+", " ")))) return null;
  try {
    const response = await sessionFetch(url, { credentials: "same-origin" });
    return { status: response.status, body: response.ok ? await response.json() : null };
  } catch {
    return null;
  }
}

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

export async function fetchDepartments(): Promise<Loaded<string[]>> {
  const result = await getJson("/api/departments");
  const names = isObject(result?.body) ? result.body.departments : undefined;
  if (!Array.isArray(names) || !names.every((n) => typeof n === "string")) return { kind: "unavailable" };
  return { kind: "ready", value: names };
}

export async function fetchProducts(filter: { department?: string; q?: string }): Promise<Loaded<Tile[]>> {
  const params = new URLSearchParams();
  if (filter.department) params.set("department", filter.department);
  if (filter.q) params.set("q", filter.q);
  const query = params.toString();
  const result = await getJson(query ? `/api/products?${query}` : "/api/products");
  const products = isObject(result?.body) ? result.body.products : undefined;
  if (!Array.isArray(products)) return { kind: "unavailable" };
  return { kind: "ready", value: products as Tile[] };
}

export async function fetchProduct(id: string): Promise<Loaded<Product>> {
  const result = await getJson(`/api/product?${new URLSearchParams({ id }).toString()}`);
  if (result?.status === 404) return { kind: "not-found" };
  const product = isObject(result?.body) ? result.body.product : undefined;
  if (!isObject(product) || product.product_id !== id || !Array.isArray(product.variants)) {
    return { kind: "unavailable" };
  }
  return { kind: "ready", value: product as Product };
}

/** A price as the gateway sent it, in its own currency. The amount string is never re-rounded. */
export function formatMoney(money: Money): string {
  const symbols: Record<string, string> = { USD: "$", CAD: "CA$", GBP: "£", EUR: "€" };
  const symbol = symbols[money.currency];
  return symbol ? `${symbol}${money.amount}` : `${money.amount} ${money.currency}`;
}

/** Catalog names start with the brand (rule 4 of the trial build): show the name without it. The
 * alt text keeps the full name. */
export function displayName(brand: string, name: string): string {
  const prefix = `${brand} `;
  return brand && name.startsWith(prefix) && name.length > prefix.length ? name.slice(prefix.length) : name;
}

export const tileImage = (id: string) => `/products/800/${id}.webp`;
export const heroImage = (id: string) => `/products/1600/${id}.webp`;
