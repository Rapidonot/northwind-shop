// SA-01: the shop's hash routes. No router dependency: `package.json` is on the control surface.
import { useEffect, useState } from "react";
import { mightBeCardData } from "./cardGuard";

export type Route =
  | { page: "home" }
  | { page: "department"; name: string }
  | { page: "search"; q: string }
  | { page: "product"; id: string }
  | { page: "cart" }
  | { page: "checkout" }
  | { page: "orders" }
  | { page: "not-found" };

const PRODUCT_ID = /^P\d{4}$/;

function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

/** The route for a location hash. Anything unknown or malformed is `not-found`, never a guess. A
 * hash that might hold card data is `not-found` too, so nothing from it reaches a request URL
 * (security review F1). */
export function parseHash(hash: string): Route {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (mightBeCardData(decode(raw) ?? raw)) return { page: "not-found" };
  const [path = "", query = ""] = raw.split("?", 2);
  const parts = path.split("/").filter((part) => part !== "");
  if (parts.length === 0) return { page: "home" };
  const [head, arg, ...rest] = parts;
  if (rest.length > 0) return { page: "not-found" };
  if (head === "d" && arg !== undefined) {
    const name = decode(arg);
    return name ? { page: "department", name } : { page: "not-found" };
  }
  if (head === "p" && arg !== undefined && PRODUCT_ID.test(arg)) return { page: "product", id: arg };
  if (arg !== undefined) return { page: "not-found" };
  if (head === "search") return { page: "search", q: new URLSearchParams(query).get("q")?.trim() ?? "" };
  if (head === "cart") return { page: "cart" };
  if (head === "checkout") return { page: "checkout" };
  if (head === "orders") return { page: "orders" };
  return { page: "not-found" };
}

export function href(route: Route): string {
  switch (route.page) {
    case "home":
    case "not-found":
      return "#/";
    case "department":
      return `#/d/${encodeURIComponent(route.name)}`;
    case "search":
      return `#/search?${new URLSearchParams({ q: route.q }).toString()}`;
    case "product":
      return `#/p/${route.id}`;
    case "cart":
      return "#/cart";
    case "checkout":
      return "#/checkout";
    case "orders":
      return "#/orders";
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parseHash(window.location.hash));
      window.scrollTo?.(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
