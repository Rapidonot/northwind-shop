// OPS-1: the only staff code in the shop's first bundle. It asks the gateway whether the console
// exists; with DEMO_STAFF_DASHBOARD off the answer is the same 404 as any unknown path, and the page
// is the shop's own not-found page. Only a 200 loads the console's chunk.
import { lazy, Suspense, useEffect, useState } from "react";
import { NotFoundPage } from "../pages";
import { checkGate, fetchView, type Gate } from "./staffApi";
import type { ViewName } from "./types";

const StaffConsole = lazy(() => import("./StaffConsole"));

export const STAFF_UNAVAILABLE = "The demo console is unavailable right now.";

export function StaffGate({ view }: { view: ViewName }) {
  const [gate, setGate] = useState<Gate>({ kind: "checking" });

  useEffect(() => {
    let live = true;
    void checkGate().then((g) => {
      if (live) setGate(g);
    });
    return () => {
      live = false;
    };
  }, []);

  switch (gate.kind) {
    case "checking":
      return <p className="muted">Loading…</p>;
    case "off":
      return (
        <main className="content">
          <NotFoundPage />
        </main>
      );
    case "unavailable":
      return (
        <main className="content">
          <p role="alert">{STAFF_UNAVAILABLE}</p>
        </main>
      );
    case "on":
      return (
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <StaffConsole view={view} overview={gate.overview} load={fetchView} />
        </Suspense>
      );
  }
}
