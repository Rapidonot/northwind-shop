import { useCallback, useEffect, useState } from "react";
import { Cart } from "./Cart";
import { type CartState, fetchCart } from "./cartApi";
import { Chat } from "./Chat";

/** The chat with the shopper's cart beside it (row #16, CHK-01). The cart is read on mount and
 * again whenever a turn ends, whatever the outcome, since a turn may have changed it. SA-01: each
 * read is also passed to `onCart`, so the shop's bag count follows a cart the chat changed. */
export function ChatPanel({
  onCart,
  suggestions,
}: { onCart?: (cart: CartState) => void; suggestions?: readonly string[] } = {}) {
  const [cart, setCart] = useState<CartState>({ kind: "loading" });
  const refresh = useCallback(() => {
    void fetchCart().then((next) => {
      setCart(next);
      onCart?.(next);
    });
  }, [onCart]);

  useEffect(refresh, [refresh]);

  return (
    <div aria-label="Chat panel">
      <Chat onTurnEnd={refresh} suggestions={suggestions} />
      <Cart state={cart} />
    </div>
  );
}
