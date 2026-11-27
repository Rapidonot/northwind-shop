// OPS-1: reads for the demo staff console. GET only: the console changes nothing.
import type { StaffViews, ViewName } from "./types";

export const STAFF_API = "/api/staff";

/** What the gate learns from the first read: the console exists, or it does not (404 with the flag
 * off, exactly like an unknown path), or the gateway could not be reached. */
export type Gate = { kind: "checking" } | { kind: "on"; overview: StaffViews["overview"] } | { kind: "off" } | { kind: "unavailable" };

export async function checkGate(): Promise<Gate> {
  try {
    const res = await fetch(`${STAFF_API}/overview`, { headers: { accept: "application/json" } });
    if (res.status === 404) return { kind: "off" };
    if (!res.ok) return { kind: "unavailable" };
    return { kind: "on", overview: (await res.json()) as StaffViews["overview"] };
  } catch {
    return { kind: "unavailable" };
  }
}

/** One view's read; null when it failed, so the view can say so instead of showing nothing. */
export async function fetchView<V extends ViewName>(view: V): Promise<StaffViews[V] | null> {
  try {
    const res = await fetch(`${STAFF_API}/${view}`, { headers: { accept: "application/json" } });
    if (!res.ok) return null;
    return (await res.json()) as StaffViews[V];
  } catch {
    return null;
  }
}
