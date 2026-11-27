import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DepartmentTabs, scrollEdges } from "./DepartmentTabs";
import type { Departments, Loaded } from "./shopApi";

// SA-01 PR 2, look round 2: the tab bar scrolls sideways when the tabs don't fit, with outlined
// "‹" / "›" buttons at the ends that can scroll, and the selected tab is always scrolled into view.
// jsdom has no layout, so the scroller's sizes are stubbed.

const DEPARTMENTS: Loaded<Departments> = {
  kind: "ready",
  value: {
    names: ["Jackets", "Fleece", "Base Layers", "Pants", "Shirts", "Knitwear", "Bedding", "Bath", "Living", "Dining"],
    counts: { Jackets: 33, Fleece: 16, "Base Layers": 18, Pants: 18, Shirts: 18, Knitwear: 16, Bedding: 28, Bath: 14, Living: 26, Dining: 12 },
    total: 199,
  },
};

let layout = { scrollWidth: 1000, clientWidth: 1000 };
const scrollIntoView = vi.fn();
const scrollBy = vi.fn();

function stub(name: "scrollWidth" | "clientWidth") {
  Object.defineProperty(HTMLElement.prototype, name, {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains("tab-scroller") ? layout[name] : 0;
    },
  });
}

beforeEach(() => {
  stub("scrollWidth");
  stub("clientWidth");
  Element.prototype.scrollIntoView = scrollIntoView;
  Element.prototype.scrollBy = scrollBy as unknown as Element["scrollBy"];
});

afterEach(() => {
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollWidth;
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientWidth;
  scrollIntoView.mockReset();
  scrollBy.mockReset();
  layout = { scrollWidth: 1000, clientWidth: 1000 };
});

function scroller(): HTMLElement {
  const list = screen.getByRole("navigation", { name: "Departments" }).querySelector<HTMLElement>(".tab-scroller");
  if (!list) throw new Error("no scroller");
  return list;
}

describe("[SA-01] the tab bar's scroll edges", () => {
  it.each([
    [0, 1000, 1000, { left: false, right: false }],
    [0, 800, 1000, { left: false, right: true }],
    [100, 800, 1000, { left: true, right: true }],
    [200, 800, 1000, { left: true, right: false }],
    [199.5, 800, 1000, { left: true, right: false }],
    [0.5, 800, 1000, { left: false, right: true }],
  ])("scrolled %s of %s in %s", (left, client, width, expected) => {
    expect(scrollEdges(left, client, width)).toEqual(expected);
  });
});

describe("[SA-01] the department tabs", () => {
  it("show All, then every department in the order sent, with counts", () => {
    render(<DepartmentTabs departments={DEPARTMENTS} route={{ page: "home" }} />);
    const links = within(screen.getByRole("navigation", { name: "Departments" })).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual([
      "All 199",
      "Jackets 33",
      "Fleece 16",
      "Base Layers 18",
      "Pants 18",
      "Shirts 18",
      "Knitwear 16",
      "Bedding 28",
      "Bath 14",
      "Living 26",
      "Dining 12",
    ]);
    expect(links[0]).toHaveAttribute("aria-current", "page");
  });

  it("have no scroll buttons when every tab fits", async () => {
    render(<DepartmentTabs departments={DEPARTMENTS} route={{ page: "home" }} />);
    await new Promise((done) => setTimeout(done, 40)); // the first frame's measure
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("show › when more tabs lie to the right, and ‹ too once scrolled", async () => {
    layout = { scrollWidth: 1300, clientWidth: 900 };
    render(<DepartmentTabs departments={DEPARTMENTS} route={{ page: "home" }} />);
    const right = await screen.findByRole("button", { name: "Scroll departments right" });
    expect(right).toHaveTextContent("›");
    expect(screen.queryByRole("button", { name: "Scroll departments left" })).toBeNull();

    scroller().scrollLeft = 200;
    fireEvent.scroll(scroller());
    expect(await screen.findByRole("button", { name: "Scroll departments left" })).toHaveTextContent("‹");
    expect(screen.getByRole("button", { name: "Scroll departments right" })).toBeInTheDocument();

    scroller().scrollLeft = 400;
    fireEvent.scroll(scroller());
    await waitFor(() => expect(screen.queryByRole("button", { name: "Scroll departments right" })).toBeNull());
    expect(screen.getByRole("button", { name: "Scroll departments left" })).toBeInTheDocument();
  });

  it("scroll the bar by most of its width, either way", async () => {
    layout = { scrollWidth: 1300, clientWidth: 900 };
    render(<DepartmentTabs departments={DEPARTMENTS} route={{ page: "home" }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Scroll departments right" }));
    expect(scrollBy).toHaveBeenLastCalledWith({ left: 540, behavior: "smooth" });
    scroller().scrollLeft = 200;
    fireEvent.scroll(scroller());
    fireEvent.click(await screen.findByRole("button", { name: "Scroll departments left" }));
    expect(scrollBy).toHaveBeenLastCalledWith({ left: -540, behavior: "smooth" });
  });

  it("scroll the selected tab into view, and again when the selection changes", () => {
    const { rerender } = render(<DepartmentTabs departments={DEPARTMENTS} route={{ page: "department", name: "Dining" }} />);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView.mock.contexts[0]).toHaveTextContent("Dining 12");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
    rerender(<DepartmentTabs departments={DEPARTMENTS} route={{ page: "department", name: "Fleece" }} />);
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    expect(scrollIntoView.mock.contexts[1]).toHaveTextContent("Fleece 16");
  });

  it("render an empty bar, never guessed tabs, until the departments are read", () => {
    render(<DepartmentTabs departments={{ kind: "loading" }} route={{ page: "home" }} />);
    expect(screen.getByRole("navigation", { name: "Departments" })).toBeEmptyDOMElement();
  });
});
