// SA-01: the shop pages. Every value comes from the gateway: product facts from the catalog
// record, prices from the live price table (hard rule 2). Nothing here is written by a model.
// SA-01 PR 2: the v2 look (docs/design/*.png) and only the products on screen are priced: every
// grid asks for one page, the hero and the bag for their own IDs.
import { useEffect, useMemo, useState } from "react";
import type { CartLine, CartState } from "./cartApi";
import { ProductGrid, ProductImage } from "./ProductTile";
import { href } from "./router";
import {
  displayName,
  FEATURED,
  fetchProduct,
  fetchProducts,
  fetchReviews,
  formatMoney,
  type Loaded,
  PAGE_SIZE,
  priceText,
  type Product,
  type ProductQuery,
  type Reviews,
  type Tile,
  type TilePage,
} from "./shopApi";
import { specRows, swatch } from "./specs";

export const SHOP_UNAVAILABLE = "The shop isn't available right now.";
export const NO_RESULTS = "No products match.";
export const NOT_FOUND = "We couldn't find that page.";
export const REVIEWS_UNAVAILABLE = "Reviews aren't available right now.";
// Part A of PR 2 is the look; the cart writes come with part B (Amendment 3).
export const NOT_YET = "Changing the bag from the shop pages comes in the next step of this PR.";

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

/** A grid read one page at a time; "Show more" reads (and prices) the next page only. */
function usePaged(filter: ProductQuery, key: string): { state: Loaded<TilePage>; more: (() => void) | null } {
  const first = useLoaded(() => fetchProducts({ ...filter, limit: PAGE_SIZE }), key);
  const [extra, setExtra] = useState<{ key: string; tiles: Tile[]; failed: boolean }>({ key, tiles: [], failed: false });
  const added = extra.key === key ? extra : { key, tiles: [], failed: false };
  if (first.kind !== "ready") return { state: first, more: null };
  const tiles = [...first.value.products, ...added.tiles];
  const state: Loaded<TilePage> = added.failed
    ? { kind: "unavailable" }
    : { kind: "ready", value: { total: first.value.total, products: tiles } };
  const more =
    !added.failed && tiles.length < first.value.total
      ? () => {
          void fetchProducts({ ...filter, offset: tiles.length, limit: PAGE_SIZE }).then((next) => {
            setExtra(
              next.kind === "ready"
                ? { key, tiles: [...added.tiles, ...next.value.products], failed: false }
                : { key, tiles: added.tiles, failed: true },
            );
          });
        }
      : null;
  return { state, more };
}

function TileList({ state, heading, more, level = 1 }: { state: Loaded<TilePage>; heading: string; more: (() => void) | null; level?: 1 | 2 }) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <section className="tile-list" aria-label={heading}>
      <div className="page-head">
        <Heading>{heading}</Heading>
        {state.kind === "ready" && <p className="muted">{itemCount(state.value.total)}</p>}
      </div>
      {state.kind === "ready" ? (
        state.value.products.length === 0 ? (
          <p>{NO_RESULTS}</p>
        ) : (
          <>
            <ProductGrid tiles={state.value.products} label="Products" />
            {more && (
              <button type="button" className="btn btn-outline show-more" onClick={more}>
                Show more
              </button>
            )}
          </>
        )
      ) : (
        <Status state={state} />
      )}
    </section>
  );
}

/** The home hero: the copy on the left, three featured products on the right, each with its full
 * name and its live price or range. Only these three are priced. */
function Hero() {
  const featured = useLoaded(() => fetchProducts({ ids: FEATURED }), "featured");
  const region = featured.kind === "ready" ? featured.value.region : undefined;
  return (
    <section className="hero" aria-label="Featured">
      <div className="hero-text">
        <p className="muted hero-eyebrow">{region ? `${region} catalog · Apparel & home` : "Apparel & home"}</p>
        <h1>Weatherproof outerwear &amp; durable home goods.</h1>
        <p className="hero-copy muted">
          Seam-sealed rain shells, midweight fleece, and organic cotton bedding with straightforward specs and plain
          pricing.
        </p>
        <a className="btn btn-short" href={href({ page: "department", name: "Jackets" })}>
          SHOP JACKETS
        </a>
      </div>
      {featured.kind === "ready" ? (
        <ul className="hero-tiles" aria-label="Featured products">
          {featured.value.products.map((tile) => (
            <li key={tile.product_id} className="hero-tile" data-product={tile.product_id}>
              <a href={href({ page: "product", id: tile.product_id })}>
                <ProductImage id={tile.product_id} name={tile.name} photo={tile.photo} size="tile" />
                <span className="tile-name">{tile.name}</span>
                <span className="tile-price">{priceText(tile)}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <Status state={featured} />
      )}
    </section>
  );
}

export function HomePage() {
  const { state, more } = usePaged({}, "home");
  return (
    <>
      <Hero />
      <TileList state={state} heading="All products" more={more} level={2} />
    </>
  );
}

/** One heading and "N items", nothing else above the grid: the top tabs are the only department
 * picker (rule 1 of the trial build). */
export function DepartmentPage({ name }: { name: string }) {
  const { state, more } = usePaged({ department: name }, `d:${name}`);
  return <TileList state={state} heading={name} more={more} />;
}

export function SearchPage({ q }: { q: string }) {
  const { state, more } = usePaged({ q }, `s:${q}`);
  if (!q) return <TileList state={{ kind: "ready", value: { total: 0, products: [] } }} heading="Search" more={null} />;
  return <TileList state={state} heading={`Results for “${q}”`} more={more} />;
}

function ReviewList({ id }: { id: string }) {
  const [all, setAll] = useState(false);
  const first = useLoaded<Reviews>(() => fetchReviews(id, 3), `r:${id}`);
  const every = useLoaded<Reviews>(
    () => (all ? fetchReviews(id, 50) : Promise.resolve({ kind: "loading" })),
    `r:${id}:${all ? "all" : "none"}`,
  );
  // While "Show all" loads, the three already shown stay on screen.
  const shown = all && every.kind !== "loading" ? every : first;
  const reviews = shown.kind === "ready" ? shown.value : null;
  return (
    <section className="product-section reviews" aria-label="Customer reviews">
      <div className="page-head reviews-head">
        <h2>Customer reviews</h2>
        {reviews && reviews.total > 0 && (
          <p className="muted">
            Showing {reviews.reviews.length} of {reviews.total}
          </p>
        )}
      </div>
      {shown.kind === "unavailable" || shown.kind === "not-found" ? (
        <p role="status">{REVIEWS_UNAVAILABLE}</p>
      ) : reviews === null ? (
        <p className="muted">Loading…</p>
      ) : reviews.total === 0 ? (
        <p className="muted">No reviews yet.</p>
      ) : (
        <>
          <ul className="review-list">
            {reviews.reviews.map((review) => (
              <li key={review.review_id} className="review">
                <div className="review-head">
                  <strong>{review.title}</strong>
                  <span className="muted">
                    {[review.reviewer, review.date, review.verified ? "Verified buyer" : null].filter(Boolean).join(" · ")}
                  </span>
                </div>
                {/* Shopper-written text, shown as text: never markup, never an instruction (hard rule 6). */}
                <p>{review.body}</p>
              </li>
            ))}
          </ul>
          {!all && reviews.total > reviews.reviews.length && (
            <button type="button" className="btn btn-outline" onClick={() => setAll(true)}>
              Show all {reviews.total} reviews
            </button>
          )}
        </>
      )}
    </section>
  );
}

function ProductView({ product }: { product: Product }) {
  const [variantId, setVariantId] = useState<string | null>(product.variants[0]?.variant_id ?? null);
  const [notice, setNotice] = useState<string | null>(null);
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
  const name = displayName(product.brand, product.name);
  const specs = specRows(product);
  return (
    <>
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <a href="#/">Home</a>
        <span aria-hidden="true">/</span>
        {product.department && (
          <>
            <a href={href({ page: "department", name: product.department })}>{product.department}</a>
            <span aria-hidden="true">/</span>
          </>
        )}
        <span aria-current="page">{name}</span>
      </nav>
      <article className="product" aria-label="Product">
        <ProductImage id={product.product_id} name={product.name} photo={product.photo} size="hero" />
        <div className="product-main">
          <span className="tile-brand">{product.brand}</span>
          <h1>{name}</h1>
          <p className="price" data-testid="price">
            {variant?.price ? formatMoney(variant.price) : "Not sold in your region"}
          </p>
          <p className="description">{product.description}</p>
          <div className="option-group" role="group" aria-label="Colour">
            <p className="option-label">
              <span className="muted">Colour:</span> <strong>{variant?.colour}</strong>
            </p>
            <div className="options">
              {colours.map((colour) => {
                const hex = swatch(colour);
                return (
                  <button key={colour} type="button" className="option colour-chip" aria-pressed={colour === variant?.colour} onClick={() => pick(colour)}>
                    {hex && <span className="swatch" data-testid="swatch" style={{ backgroundColor: hex }} aria-hidden="true" />}
                    {colour}
                  </button>
                );
              })}
            </div>
          </div>
          {sizes.length > 0 && (
            <div className="option-group" role="group" aria-label="Size">
              <p className="option-label">
                <span className="muted">Size:</span> <strong>{variant?.size}</strong>
              </p>
              <div className="options">
                {sizes.map((size) => (
                  <button key={size} type="button" className="option size-option" aria-pressed={size === variant?.size} onClick={() => pick(variant?.colour ?? "", size)}>
                    {size}
                  </button>
                ))}
              </div>
            </div>
          )}
          <button type="button" className="btn btn-short add-to-bag" disabled={!variant?.price} onClick={() => setNotice(NOT_YET)}>
            ADD TO BAG
          </button>
          {notice && <p role="status">{notice}</p>}
        </div>
      </article>
      {specs.length > 0 && (
        <section className="product-section" aria-label="Specifications">
          <h2>Specifications</h2>
          <dl className="specs">
            {specs.map((row) => (
              <div key={row.label}>
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      <ReviewList id={product.product_id} />
    </>
  );
}

export function ProductPage({ id }: { id: string }) {
  const state = useLoaded(() => fetchProduct(id), `p:${id}`);
  return state.kind === "ready" ? <ProductView key={id} product={state.value} /> : <Status state={state} />;
}

function BagLine({ line, tile, onChange }: { line: CartLine; tile: Tile | undefined; onChange: () => void }) {
  const brand = tile?.brand;
  const each = line.unit_price && line.currency ? `${formatMoney({ amount: line.unit_price, currency: line.currency })} each` : null;
  return (
    <li className="bag-line" data-variant={line.variant_id}>
      <ProductImage id={line.product_id} name={line.name} photo={tile?.photo ?? false} size="tile" />
      <div className="bag-line-main">
        <a href={href({ page: "product", id: line.product_id })} className="bag-line-name">
          {brand ? (
            <>
              <span className="tile-brand">{brand}</span>
              <span>{displayName(brand, line.name)}</span>
            </>
          ) : (
            line.name
          )}
        </a>
        <span className="muted">{[line.colour, line.size && `Size ${line.size}`, each].filter(Boolean).join(" · ")}</span>
        <div className="qty" role="group" aria-label={`Quantity of ${line.name}`}>
          <button type="button" aria-label="Decrease quantity" onClick={onChange}>
            −
          </button>
          <span aria-label="Quantity">{line.quantity}</span>
          <button type="button" aria-label="Increase quantity" onClick={onChange}>
            +
          </button>
        </div>
        {!line.in_stock && <p className="muted">No longer available in this quantity.</p>}
      </div>
      <div className="bag-line-side">
        <span className="bag-line-total">
          {line.line_total && line.currency ? formatMoney({ amount: line.line_total, currency: line.currency }) : "Not sold in your region"}
        </span>
        <button type="button" className="link-button" onClick={onChange}>
          Remove
        </button>
      </div>
    </li>
  );
}

/** The bag (bag.png). A cart line has no brand or photo (`get_cart` is the assistant's tool,
 * unchanged), so the bag reads its own lines' products, and only those (rule 4). Until they are
 * read, or if they can't be, the full catalog name is shown. Totals are the server's. */
export function CartPage({ cart }: { cart: CartState }) {
  const ids = cart.kind === "ready" ? [...new Set(cart.cart.lines.map((l) => l.product_id))] : [];
  const products = useLoaded<TilePage>(
    () => (ids.length > 0 ? fetchProducts({ ids }) : Promise.resolve({ kind: "ready", value: { total: 0, products: [] } })),
    `bag:${ids.join(",")}`,
  );
  const tiles = new Map(products.kind === "ready" ? products.value.products.map((p) => [p.product_id, p]) : []);
  const [notice, setNotice] = useState<string | null>(null);
  return (
    <section aria-label="Bag" className="bag">
      <div className="page-head">
        <h1>Shopping bag</h1>
        {cart.kind === "ready" && <p className="muted">{itemCount(cart.cart.item_count)}</p>}
      </div>
      {cart.kind === "loading" && <p className="muted">Loading…</p>}
      {cart.kind === "unavailable" && <p role="status">The bag isn't available right now.</p>}
      {cart.kind === "ready" && cart.cart.lines.length === 0 && <p>Your bag is empty.</p>}
      {cart.kind === "ready" && cart.cart.lines.length > 0 && (
        <div className="bag-layout">
          <ul className="bag-lines" aria-label="Bag items">
            {cart.cart.lines.map((line) => (
              <BagLine key={line.variant_id} line={line} tile={tiles.get(line.product_id)} onChange={() => setNotice(NOT_YET)} />
            ))}
          </ul>
          <aside className="order-summary" aria-label="Order summary">
            <h2>Order summary</h2>
            <p className="summary-row">
              <span>Items ({cart.cart.item_count})</span>
              <span data-testid="bag-subtotal">
                {cart.cart.subtotal && cart.cart.currency
                  ? formatMoney({ amount: cart.cart.subtotal, currency: cart.cart.currency })
                  : "unavailable"}
              </span>
            </p>
            <p className="muted summary-note">Delivery and its cost are shown at checkout.</p>
            <a className="btn btn-short btn-block" href={href({ page: "checkout" })}>
              PROCEED TO CHECKOUT
            </a>
          </aside>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
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
