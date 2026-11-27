// SA-01 PR 2: the department tabs (home-tabs.png). They are the only department picker (rule 1 of
// the trial build): All, then every department in catalog order with its count, the selected tab
// filled black. Counts are the gateway's; a department without one shows its name only.
//
// Look round 2: when the tabs don't fit (a narrow screen, or the panel open on a small desktop) the
// bar scrolls sideways, with small outlined "‹" and "›" buttons at the ends that can scroll (no
// fade: the look has no gradients). The selected tab is always scrolled into view.
import { useEffect, useRef, useState } from "react";
import { href, type Route } from "./router";
import type { Departments, Loaded } from "./shopApi";

/** Which ends can still scroll. A pixel of slack, so rounding never shows a button for nothing. */
export function scrollEdges(scrollLeft: number, clientWidth: number, scrollWidth: number): { left: boolean; right: boolean } {
  return { left: scrollLeft > 1, right: scrollLeft + clientWidth < scrollWidth - 1 };
}

function TabList({ names, counts, total, route }: Departments & { route: Route }) {
  const scroller = useRef<HTMLUListElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const selected = route.page === "department" ? route.name : route.page === "home" ? "" : null;

  // Measured only in callbacks (scroll, resize, the first frame), never synchronously in render.
  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const measure = () => {
      const next = scrollEdges(element.scrollLeft, element.clientWidth, element.scrollWidth);
      setEdges((current) => (current.left === next.left && current.right === next.right ? current : next));
    };
    const frame = window.requestAnimationFrame?.(measure);
    element.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => {
      if (frame !== undefined) window.cancelAnimationFrame?.(frame);
      element.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [names.length]);

  // The selected tab is always in view, whichever way the shopper got to its page.
  useEffect(() => {
    scroller.current?.querySelector<HTMLElement>('[aria-current="page"]')?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selected]);

  const page = (direction: 1 | -1) => {
    const element = scroller.current;
    if (element) element.scrollBy?.({ left: direction * Math.max(element.clientWidth * 0.6, 120), behavior: "smooth" });
  };

  return (
    <div className="tabs">
      {edges.left && (
        <button type="button" className="tab-scroll tab-scroll-left" aria-label="Scroll departments left" onClick={() => page(-1)}>
          ‹
        </button>
      )}
      <ul ref={scroller} className="tab-scroller">
        <li>
          <a href={href({ page: "home" })} aria-current={route.page === "home" ? "page" : undefined}>
            All <span className="tab-count">{total}</span>
          </a>
        </li>
        {names.map((name) => (
          <li key={name}>
            <a href={href({ page: "department", name })} aria-current={name === selected ? "page" : undefined}>
              {name}
              {counts[name] !== undefined && <span className="tab-count"> {counts[name]}</span>}
            </a>
          </li>
        ))}
      </ul>
      {edges.right && (
        <button type="button" className="tab-scroll tab-scroll-right" aria-label="Scroll departments right" onClick={() => page(1)}>
          ›
        </button>
      )}
    </div>
  );
}

export function DepartmentTabs({ departments, route }: { departments: Loaded<Departments>; route: Route }) {
  return (
    <nav aria-label="Departments" className="departments">
      {departments.kind === "ready" && <TabList {...departments.value} route={route} />}
    </nav>
  );
}
