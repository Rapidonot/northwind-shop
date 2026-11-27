// SA-01 PR 2: the demo checkout (CHK-02's portal confirmation control) and the order history.
// Every amount, line and status is the gateway's, shown as sent (hard rule 2). There is no card field
// of any kind (hard rule 1): Pay (demo) uses the gateway's sandbox token. The order is confirmed only
// by pressing the Pay (demo) button, never by chat text (hard rule 9).
import { useEffect, useState } from "react";
import {
  type CheckoutSummary,
  fetchOrders,
  fetchSummary,
  newKey,
  type OrdersState,
  payDemo,
  type Placed,
  signInDemo,
  type SummaryState,
} from "./cartApi";
import { href } from "./router";
import { fetchProducts, formatMoney, type Tile } from "./shopApi";

export const SIGN_IN_TO_PAY = "Sign in to pay. This demo signs you in as its one synthetic shopper.";
export const SIGN_IN_TO_SEE_ORDERS = "Sign in to see your orders.";
export const SIGN_IN_UNAVAILABLE = "Sign-in isn't available in this demo right now.";
export const CHECKOUT_UNAVAILABLE = "Checkout isn't available right now.";
export const ORDERS_UNAVAILABLE = "Your orders aren't available right now.";
export const NOTHING_CHARGED = "Demo only: no real payment is taken and nothing is delivered.";

const ISSUE_TEXT: Record<string, string> = {
  empty: "Your bag is empty.",
  not_sold_here: "An item in your bag isn't sold in your region.",
  out_of_stock: "An item in your bag doesn't have enough stock.",
  over_order_limit: "This order is above the demo's order limit.",
};

/** Names for product IDs the page shows (substitutes, history lines), read for those IDs only. */
function useNames(ids: string[]): Map<string, Tile> {
  const [tiles, setTiles] = useState<Map<string, Tile>>(new Map());
  const key = [...new Set(ids)].sort().join(",");
  useEffect(() => {
    if (!key) return;
    let live = true;
    void fetchProducts({ ids: key.split(",") }).then((found) => {
      if (live && found.kind === "ready") setTiles(new Map(found.value.products.map((t) => [t.product_id, t])));
    });
    return () => {
      live = false;
    };
  }, [key]);
  return tiles;
}

function SignIn({ prompt, onSignedIn }: { prompt: string; onSignedIn: () => void }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="sign-in" role="group" aria-label="Sign in">
      <p>{prompt}</p>
      <button
        type="button"
        className="btn btn-outline"
        onClick={() =>
          void signInDemo().then((ok) => {
            setFailed(!ok);
            if (ok) onSignedIn();
          })
        }
      >
        Sign in (demo)
      </button>
      {failed && <p role="status">{SIGN_IN_UNAVAILABLE}</p>}
    </div>
  );
}

type Outcome =
  | { kind: "placed"; placed: Placed }
  | { kind: "refused"; message: string; code: string | null; substitutes: string[] };

function Summary({ summary }: { summary: CheckoutSummary }) {
  return (
    <aside className="order-summary" aria-label="Order summary">
      <h2>Order summary</h2>
      <ul className="summary-lines" aria-label="Order lines">
        {summary.lines.map((line) => (
          <li key={line.variant_id} className="summary-row">
            <span>
              {line.name}
              <span className="muted">
                {" "}
                · {[line.colour, line.size && `Size ${line.size}`].filter(Boolean).join(" · ")} × {line.quantity}
              </span>
            </span>
            <span>{line.line_total ? formatMoney(line.line_total) : "Not sold in your region"}</span>
          </li>
        ))}
      </ul>
      <p className="summary-row">
        <span>Delivery</span>
        <span className="muted">{summary.delivery}</span>
      </p>
      <p className="summary-row">
        <span>Returns</span>
        <span className="muted">See the returns policy ({summary.return_policy}); the assistant can explain it.</span>
      </p>
      <p className="summary-row summary-total">
        <strong>Total</strong>
        <strong data-testid="checkout-total">{summary.total ? formatMoney(summary.total) : "unavailable"}</strong>
      </p>
    </aside>
  );
}

export function CheckoutPage({ onCartChanged = () => {} }: { onCartChanged?: () => void }) {
  const [state, setState] = useState<SummaryState>({ kind: "loading" });
  const [round, setRound] = useState(0);
  const [paying, setPaying] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  useEffect(() => {
    let live = true;
    void fetchSummary().then((next) => {
      if (live) setState(next);
    });
    return () => {
      live = false;
    };
  }, [round]);
  const reload = () => setRound((n) => n + 1);
  const substituteIds = outcome?.kind === "refused" ? outcome.substitutes : [];
  const names = useNames(substituteIds);

  const press = async (summary: CheckoutSummary) => {
    setPaying(true);
    const done = await payDemo(summary.digest, newKey()); // one key per press of Pay (demo)
    setPaying(false);
    if (done.ok) {
      setOutcome({ kind: "placed", placed: done.value });
      onCartChanged();
      return;
    }
    const subs = done.body.substitutes;
    const ids =
      subs && typeof subs === "object"
        ? Object.values(subs as Record<string, unknown>)
            .flatMap((list) => (Array.isArray(list) ? list : []))
            .flatMap((c) => (c && typeof c === "object" && typeof (c as { product_id?: unknown }).product_id === "string" ? [(c as { product_id: string }).product_id] : []))
        : [];
    setOutcome({ kind: "refused", message: done.message, code: done.code, substitutes: ids });
    if (done.code === "summary_changed") reload();
    if (done.code === "already_placed") {
      // The gateway removed lines of an earlier order from the bag (re-review N4): show it as it is.
      onCartChanged();
      reload();
    }
  };

  if (outcome?.kind === "placed") {
    return (
      <section className="checkout" aria-label="Checkout">
        <div className="page-head">
          <h1>Order placed</h1>
        </div>
        <p role="status">
          Order {outcome.placed.order_id} is placed (demo): {formatMoney(outcome.placed.total)}. {NOTHING_CHARGED}
        </p>
        <a className="btn btn-outline" href={href({ page: "orders" })}>
          See your orders
        </a>
      </section>
    );
  }

  return (
    <section className="checkout" aria-label="Checkout">
      <div className="page-head">
        <h1>Checkout</h1>
      </div>
      {state.kind === "loading" && <p className="muted">Loading…</p>}
      {state.kind === "unavailable" && <p role="status">{CHECKOUT_UNAVAILABLE}</p>}
      {state.kind === "ready" && (
        <div className="bag-layout">
          <div>
            {state.summary.issues.length > 0 && (
              <ul className="checkout-issues" aria-label="Issues">
                {state.summary.issues.map((issue) => (
                  <li key={issue}>{ISSUE_TEXT[issue] ?? "Something in your bag can't be ordered."}</li>
                ))}
              </ul>
            )}
            {outcome?.kind === "refused" && (
              <div role="alert" className="checkout-refusal">
                <p>{outcome.message}</p>
                {outcome.substitutes.length > 0 && (
                  <>
                    <p>You could try:</p>
                    <ul aria-label="Substitutes">
                      {[...new Set(outcome.substitutes)].map((id) => (
                        <li key={id}>
                          <a href={href({ page: "product", id })}>{names.get(id)?.name ?? id}</a>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
            <p className="muted">{NOTHING_CHARGED}</p>
            {state.summary.signed_in ? (
              <button
                type="button"
                className="btn btn-block pay-demo"
                disabled={paying || state.summary.issues.length > 0}
                onClick={() => void press(state.summary)}
              >
                Pay (demo)
              </button>
            ) : (
              <SignIn prompt={SIGN_IN_TO_PAY} onSignedIn={reload} />
            )}
          </div>
          <Summary summary={state.summary} />
        </div>
      )}
    </section>
  );
}

export function OrdersPage() {
  const [state, setState] = useState<OrdersState>({ kind: "loading" });
  const [round, setRound] = useState(0);
  useEffect(() => {
    let live = true;
    void fetchOrders().then((next) => {
      if (live) setState(next);
    });
    return () => {
      live = false;
    };
  }, [round]);
  const ids = state.kind === "ready" ? state.orders.flatMap((o) => o.lines.map((l) => l.product_id)) : [];
  const names = useNames(ids);
  return (
    <section className="orders" aria-label="Orders">
      <div className="page-head">
        <h1>Orders</h1>
        {state.kind === "ready" && <p className="muted">{state.orders.length} orders</p>}
      </div>
      {state.kind === "loading" && <p className="muted">Loading…</p>}
      {state.kind === "unavailable" && <p role="status">{ORDERS_UNAVAILABLE}</p>}
      {state.kind === "signed-out" && <SignIn prompt={SIGN_IN_TO_SEE_ORDERS} onSignedIn={() => setRound((n) => n + 1)} />}
      {state.kind === "ready" && state.orders.length === 0 && <p>You have no orders yet.</p>}
      {state.kind === "ready" && state.orders.length > 0 && (
        <ul className="order-list" aria-label="Your orders">
          {state.orders.map((order) => (
            <li key={order.order_id} className="order" data-order={order.order_id}>
              <div className="order-head">
                <strong>{order.order_id}</strong>
                <span className="muted">{[order.placed_on, order.status_text].filter(Boolean).join(" · ")}</span>
              </div>
              <ul className="order-lines">
                {order.lines.map((line) => (
                  <li key={line.variant_id}>
                    {names.get(line.product_id)?.name ?? line.product_id} × {line.quantity}
                  </li>
                ))}
              </ul>
              {order.total && <p className="order-total">{formatMoney(order.total)}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
