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
  | { page: "staff"; view: StaffView }
  | { page: "not-found" };

// OPS-1: the demo staff console's views. The route exists in every build; the console itself only
// when the gateway answers `/api/staff/overview` (DEMO_STAFF_DASHBOARD=1).
export const STAFF_VIEWS = ["system", "overview", "activity", "handoffs", "orders", "stock"] as const;
export type StaffView = (typeof STAFF_VIEWS)[number];

function staffView(value: string | undefined): StaffView | null {
  if (value === undefined) return "system";
  return (STAFF_VIEWS as readonly string[]).includes(value) ? (value as StaffView) : null;
}

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
  if (head === "staff") {
    const view = staffView(arg);
    return view ? { page: "staff", view } : { page: "not-found" };
  }
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
    case "staff":
      return `#/staff/${route.view}`;
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
