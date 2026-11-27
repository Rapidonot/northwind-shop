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
  /** SA-01 PR 2: the highest live price; absent from an older gateway. */
  to_price?: Money | null;
  photo: boolean;
};

/** SA-01 PR 2: one page of tiles and the number of matches in all. */
export type TilePage = { total: number; products: Tile[]; region?: string };

export type Departments = { names: string[]; counts: Record<string, number>; total: number };

export type Review = {
  review_id: string;
  title: string;
  body: string;
  reviewer: string;
  date: string;
  verified: boolean;
};

export type Reviews = { total: number; reviews: Review[] };

/** The grid's page size (SA-01 PR 2: only the products on screen are priced). */
export const PAGE_SIZE = 24;
/** The home hero's featured products, shown with their full names. */
export const FEATURED = ["P0001", "P0004", "P0121"] as const;

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
  /** SA-01 PR 2: the record's own fields for the specifications grid; absent from an older gateway. */
  subcategory?: string | null;
  gender?: string | null;
  fits?: string[];
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

export async function fetchDepartments(): Promise<Loaded<Departments>> {
  const result = await getJson("/api/departments");
  const body = isObject(result?.body) ? result.body : undefined;
  const names = body?.departments;
  if (!Array.isArray(names) || !names.every((n) => typeof n === "string")) return { kind: "unavailable" };
  // Counts are optional: without them the tabs show names only, never a guessed number.
  const counts: Record<string, number> = {};
  if (isObject(body?.counts)) {
    for (const [name, value] of Object.entries(body.counts)) {
      if (typeof value === "number" && Number.isInteger(value) && value >= 0) counts[name] = value;
    }
  }
  const total = typeof body?.total === "number" ? body.total : Object.values(counts).reduce((a, b) => a + b, 0);
  return { kind: "ready", value: { names, counts, total } };
}

export type ProductQuery = { department?: string; q?: string; ids?: readonly string[]; offset?: number; limit?: number };

/** One page of tiles. The portal always asks for a page or for given IDs, so only the products on
 * screen are priced (SA-01 PR 2, hard rule 2). */
export async function fetchProducts(filter: ProductQuery): Promise<Loaded<TilePage>> {
  const params = new URLSearchParams();
  if (filter.department) params.set("department", filter.department);
  if (filter.q) params.set("q", filter.q);
  if (filter.ids) params.set("ids", filter.ids.join(","));
  if (filter.offset) params.set("offset", String(filter.offset));
  if (filter.limit !== undefined) params.set("limit", String(filter.limit));
  const query = params.toString();
  const result = await getJson(query ? `/api/products?${query}` : "/api/products");
  const body = isObject(result?.body) ? result.body : undefined;
  const products = body?.products;
  if (!Array.isArray(products)) return { kind: "unavailable" };
  const total = typeof body?.total === "number" ? body.total : products.length;
  const region = typeof body?.region === "string" ? body.region : undefined;
  return { kind: "ready", value: { total, products: products as Tile[], ...(region ? { region } : {}) } };
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

const REVIEW_KEYS = ["review_id", "title", "body", "reviewer", "date"] as const;

/** A review is kept only with every field of the right type; shopper text stays plain text. */
function toReview(item: unknown): Review[] {
  if (!isObject(item) || typeof item.verified !== "boolean") return [];
  if (!REVIEW_KEYS.every((key) => typeof item[key] === "string")) return [];
  return [
    {
      review_id: item.review_id as string,
      title: item.title as string,
      body: item.body as string,
      reviewer: item.reviewer as string,
      date: item.date as string,
      verified: item.verified,
    },
  ];
}

export async function fetchReviews(id: string, limit?: number): Promise<Loaded<Reviews>> {
  const params = new URLSearchParams({ id });
  if (limit !== undefined) params.set("limit", String(limit));
  const result = await getJson(`/api/reviews?${params.toString()}`);
  if (result?.status === 404) return { kind: "not-found" };
  const body = isObject(result?.body) ? result.body : undefined;
  if (!body || body.product_id !== id || typeof body.total !== "number" || !Array.isArray(body.reviews)) {
    return { kind: "unavailable" };
  }
  return { kind: "ready", value: { total: body.total, reviews: body.reviews.flatMap(toReview) } };
}

/** A tile's price line: one price, or the lowest to the highest, both exactly as sent. */
export function priceText(tile: Pick<Tile, "from_price" | "to_price">): string {
  const low = tile.from_price;
  if (!low) return "Not sold in your region";
  const high = tile.to_price;
  if (high === undefined) return `From ${formatMoney(low)}`;
  if (high === null || (high.amount === low.amount && high.currency === low.currency)) return formatMoney(low);
  return `${formatMoney(low)}–${formatMoney(high)}`;
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
