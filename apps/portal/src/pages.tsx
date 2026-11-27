// SA-01 PR 1: the shop pages. Every value comes from the gateway: product facts from the catalog
// record, prices from the live price table (hard rule 2). Nothing here is written by a model.
import { useEffect, useMemo, useState } from "react";
import type { CartState } from "./cartApi";
import { ProductGrid, ProductImage } from "./ProductTile";
import { href } from "./router";
import { displayName, fetchProduct, fetchProducts, formatMoney, type Loaded, type Product, type Tile } from "./shopApi";

export const SHOP_UNAVAILABLE = "The shop isn't available right now.";
export const NO_RESULTS = "No products match.";
export const NOT_FOUND = "We couldn't find that page.";

function useLoaded<T>(load: () => Promise<Loaded<T>>, key: string): Loaded<T> {
  const [state, setState] = useState<{ key: string; value: Loaded<T> }>({ key, value: { kind: "loading" } });
  useEffect(() => {
    let live = true;
    void load().then((value) => {
      if (live) setState({ key, value });
    });
    return () => {
      live = false;
    };
    // `load` is rebuilt each render; `key` says when the request changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state.key === key ? state.value : { kind: "loading" };
}

function Status({ state }: { state: Loaded<unknown> }) {
  if (state.kind === "loading") return <p className="muted">Loading…</p>;
  if (state.kind === "unavailable") return <p role="status">{SHOP_UNAVAILABLE}</p>;
  if (state.kind === "not-found") return <p role="status">{NOT_FOUND}</p>;
  return null;
}

const itemCount = (n: number) => `${n} ${n === 1 ? "item" : "items"}`;

export function HomePage() {
  const products = useLoaded(() => fetchProducts({}), "home");
  if (products.kind !== "ready") return <Status state={products} />;
  const [hero, ...rest] = products.value;
  if (!hero) return <p>{NO_RESULTS}</p>;
  return (
    <>
      <section className="hero" aria-label="Featured">
        <a href={href({ page: "product", id: hero.product_id })}>
          <ProductImage id={hero.product_id} name={hero.name} photo={hero.photo} size="hero" />
        </a>
        <div className="hero-text">
          <span className="tile-brand">{hero.brand}</span>
          <h1>{displayName(hero.brand, hero.name)}</h1>
          {hero.from_price && <p className="price">From {formatMoney(hero.from_price)}</p>}
          <a className="btn btn-short" href={href({ page: "product", id: hero.product_id })}>
            VIEW
          </a>
        </div>
      </section>
      <h2 className="section-heading">Products</h2>
      <ProductGrid tiles={rest.slice(0, 8)} label="Products" />
    </>
  );
}

function TileList({ state, heading }: { state: Loaded<Tile[]>; heading: string }) {
  return (
    <>
      <div className="page-head">
        <h1>{heading}</h1>
        {state.kind === "ready" && <p className="muted">{itemCount(state.value.length)}</p>}
      </div>
      {state.kind === "ready" ? (
        state.value.length === 0 ? (
          <p>{NO_RESULTS}</p>
        ) : (
          <ProductGrid tiles={state.value} label="Products" />
        )
      ) : (
        <Status state={state} />
      )}
    </>
  );
}

/** One heading and "N items", nothing else above the grid: the top nav is the only department
 * picker (rule 1 of the trial build). */
export function DepartmentPage({ name }: { name: string }) {
  const state = useLoaded(() => fetchProducts({ department: name }), `d:${name}`);
  return <TileList state={state} heading={name} />;
}

export function SearchPage({ q }: { q: string }) {
  const state = useLoaded<Tile[]>(
    () => (q ? fetchProducts({ q }) : Promise.resolve({ kind: "ready", value: [] })),
    `s:${q}`,
  );
  return <TileList state={state} heading={q ? `Results for “${q}”` : "Search"} />;
}

function specRows(attributes: Record<string, unknown>): [string, string][] {
  return Object.entries(attributes)
    .filter(([, value]) => ["string", "number", "boolean"].includes(typeof value) || Array.isArray(value))
    .map(([key, value]) => [key.replaceAll("_", " "), Array.isArray(value) ? value.join(", ") : String(value)]);
}

function ProductView({ product }: { product: Product }) {
  const [variantId, setVariantId] = useState<string | null>(product.variants[0]?.variant_id ?? null);
  const variant = product.variants.find((v) => v.variant_id === variantId) ?? null;
  const colours = useMemo(() => [...new Set(product.variants.map((v) => v.colour))], [product]);
  const sizes = useMemo(
    () => [...new Set(product.variants.filter((v) => v.colour === variant?.colour).map((v) => v.size))],
    [product, variant],
  );
  const pick = (colour: string, size?: string) => {
    const match =
      product.variants.find((v) => v.colour === colour && (size === undefined || v.size === size)) ??
      product.variants.find((v) => v.colour === colour);
    if (match) setVariantId(match.variant_id);
  };
  const specs = specRows(product.attributes);
  return (
    <article className="product" aria-label="Product">
      <ProductImage id={product.product_id} name={product.name} photo={product.photo} size="hero" />
      <div className="product-main">
        <span className="tile-brand">{product.brand}</span>
        <h1>{displayName(product.brand, product.name)}</h1>
        <p className="price" data-testid="price">
          {variant?.price ? formatMoney(variant.price) : "Not sold in your region"}
        </p>
      </div>
      <section className="product-section" aria-label="Options">
        <h2>Colour: {variant?.colour}</h2>
        <div className="options">
          {colours.map((colour) => (
            <button key={colour} type="button" className="option" aria-pressed={colour === variant?.colour} onClick={() => pick(colour)}>
              {colour}
            </button>
          ))}
        </div>
        {sizes.length > 0 && (
          <>
            <h2>Size: {variant?.size}</h2>
            <div className="options">
              {sizes.map((size) => (
                <button key={size} type="button" className="option" aria-pressed={size === variant?.size} onClick={() => pick(variant?.colour ?? "", size)}>
                  {size}
                </button>
              ))}
            </div>
          </>
        )}
      </section>
      <section className="product-section" aria-label="Description">
        <h2>Description</h2>
        <p>{product.description}</p>
      </section>
      {specs.length > 0 && (
        <section className="product-section" aria-label="Specifications">
          <h2>Specifications</h2>
          <dl className="specs">
            {specs.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </article>
  );
}

export function ProductPage({ id }: { id: string }) {
  const state = useLoaded(() => fetchProduct(id), `p:${id}`);
  return state.kind === "ready" ? <ProductView key={id} product={state.value} /> : <Status state={state} />;
}

/** PR 1: the bag, read-only. Quantity controls and checkout come in PR 2. A cart line has no brand
 * (`get_cart` is the assistant's tool, unchanged), so the brand line comes from the portal's own
 * product list; until it is read, or if it can't be, the full catalog name is shown (rule 4). */
export function CartPage({ cart }: { cart: CartState }) {
  const products = useLoaded(() => fetchProducts({}), "bag-brands");
  const brands = new Map(products.kind === "ready" ? products.value.map((p) => [p.product_id, p.brand]) : []);
  return (
    <section aria-label="Bag" className="bag">
      <div className="page-head">
        <h1>Bag</h1>
        {cart.kind === "ready" && <p className="muted">{itemCount(cart.cart.item_count)}</p>}
      </div>
      {cart.kind === "loading" && <p className="muted">Loading…</p>}
      {cart.kind === "unavailable" && <p role="status">The bag isn't available right now.</p>}
      {cart.kind === "ready" && cart.cart.lines.length === 0 && <p>Your bag is empty.</p>}
      {cart.kind === "ready" && cart.cart.lines.length > 0 && (
        <>
          <ul className="bag-lines" aria-label="Bag items">
            {cart.cart.lines.map((line) => (
              <li key={line.variant_id} data-variant={line.variant_id}>
                <a href={href({ page: "product", id: line.product_id })}>
                  {brands.has(line.product_id) ? (
                    <>
                      <span className="tile-brand">{brands.get(line.product_id)}</span>
                      <span>{displayName(brands.get(line.product_id) ?? "", line.name)}</span>
                    </>
                  ) : (
                    line.name
                  )}
                </a>
                <span className="muted">{[line.colour, line.size, line.fit].filter(Boolean).join(" · ")}</span>
                <span>Qty {line.quantity}</span>
                <span>
                  {line.line_total && line.currency
                    ? formatMoney({ amount: line.line_total, currency: line.currency })
                    : "Not sold in your region"}
                </span>
              </li>
            ))}
          </ul>
          <p className="bag-subtotal" data-testid="bag-subtotal">
            Subtotal{" "}
            {cart.cart.subtotal && cart.cart.currency
              ? formatMoney({ amount: cart.cart.subtotal, currency: cart.cart.currency })
              : "unavailable"}
          </p>
        </>
      )}
    </section>
  );
}

export function PlaceholderPage({ heading }: { heading: string }) {
  return (
    <section className="page-head">
      <h1>{heading}</h1>
      <p className="muted">Not available yet.</p>
    </section>
  );
}

export function NotFoundPage() {
  return (
    <section className="page-head">
      <h1>{NOT_FOUND}</h1>
      <a href="#/">Back to the shop</a>
    </section>
  );
}
