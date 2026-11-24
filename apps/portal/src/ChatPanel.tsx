import { useCallback, useEffect, useState } from "react";
import { Cart } from "./Cart";
import { type CartState, fetchCart } from "./cartApi";
import { Chat } from "./Chat";

/** The chat with the shopper's cart beside it (row #16, CHK-01). The cart is read on mount and
 * again whenever a turn ends, whatever the outcome, since a turn may have changed it. */
export function ChatPanel() {
  const [cart, setCart] = useState<CartState>({ kind: "loading" });
  const refresh = useCallback(() => {
    void fetchCart().then(setCart);
  }, []);

  useEffect(refresh, [refresh]);

  return (
    <div aria-label="Chat panel">
      <Chat onTurnEnd={refresh} />
      <Cart state={cart} />
    </div>
  );
}
