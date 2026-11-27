import type { CartLine, CartState } from "./cartApi";

export const CART_UNAVAILABLE = "Cart unavailable";
export const CART_EMPTY = "Your cart is empty.";

function variantText(line: CartLine): string {
  return [line.colour, line.size, line.fit].filter((part) => part).join(", ");
}

function money(amount: string | null, currency: string | null): string {
  return amount === null ? "not sold in your region" : `${amount} ${currency ?? ""}`.trim();
}

/** The cart in the chat panel (row #16, CHK-01): only what GET /api/cart returned. */
export function Cart({ state }: { state: CartState }) {
  return (
    // SA-01 PR 2 (look round 1): a compact block at the bottom of the panel; the region, texts and
    // list are the ones the CHK-01 journey reads.
    <section aria-label="Cart" className="chat-cart">
      <h2>Cart{state.kind === "ready" ? ` (${state.cart.item_count})` : ""}</h2>
      {state.kind === "loading" && <p>Loading cart…</p>}
      {state.kind === "unavailable" && <p role="status">{CART_UNAVAILABLE}</p>}
      {state.kind === "ready" && state.cart.lines.length === 0 && <p>{CART_EMPTY}</p>}
      {state.kind === "ready" && state.cart.lines.length > 0 && (
        <>
          <ul aria-label="Cart items">
            {state.cart.lines.map((line) => (
              <li key={line.variant_id} data-variant={line.variant_id}>
                {line.name} ({variantText(line)}) × {line.quantity}:{" "}
                {money(line.line_total, line.currency)}
                {!line.in_stock && " (out of stock)"}
              </li>
            ))}
          </ul>
          <p data-testid="cart-subtotal">
            Subtotal: {state.cart.subtotal === null ? "unavailable" : money(state.cart.subtotal, state.cart.currency)}
          </p>
        </>
      )}
    </section>
  );
}
