// OPS-1 look phase only: renders the staff console from the fixture so the look can be reviewed
// before the gateway serves real data. Served by `vite` dev at /staff-preview.html; `vite build`
// builds index.html only, so this never ships. Removed once the look is approved.
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "../theme.css";
import { STAFF_VIEWS, type StaffView } from "../router";
import { FIXTURE } from "./fixtures";
import { StaffConsole } from "./StaffConsole";
import type { Loader } from "./types";

export const PREVIEW_BANNER = "Look preview: fixture data from the repo, not the live console.";

const load: Loader = (view) => Promise.resolve(FIXTURE[view]);

function viewOf(hash: string): StaffView {
  const name = hash.replace(/^#\/staff\/?/, "");
  return (STAFF_VIEWS as readonly string[]).includes(name) ? (name as StaffView) : "system";
}

function Preview() {
  const [view, setView] = useState<StaffView>(() => viewOf(window.location.hash));
  useEffect(() => {
    const onChange = () => setView(viewOf(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return <StaffConsole view={view} overview={FIXTURE.overview} load={load} banner={PREVIEW_BANNER} />;
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Preview />
    </StrictMode>,
  );
}
