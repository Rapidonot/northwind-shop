import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import { parseFrames } from "./chatStream";

// Row #17 (SA-02) edge cases: crafted markers and malformed `sources` never make a link or card
// that was not cited, and card text renders as text (hard rule 6).

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

function respond(...frames: string[]) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(frames.join("")));
      controller.close();
    },
  });
  const response = new Response(body, { headers: { "Content-Type": "text/event-stream" } });
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response)));
}

function ask() {
  render(<Chat />);
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "q" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

const RUG = { id: "P0178", type: "product", title: "Larchpell Rug", brand: "Larchpell", department: "Living" };
const REVIEW = { id: "R01080", type: "review" };

async function run(text: string, sourceIds: string[], sources: unknown) {
  respond(
    frame("token", { text }),
    frame("citations", { source_ids: sourceIds, sources }),
    frame("done", {}),
  );
  ask();
  await screen.findByTestId("answer");
  await vi.waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent(/\S/));
  return screen.getByTestId("answer");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-02] crafted markers and malformed sources", () => {
  it.each([
    "[<img src=x>]",
    '[P0178"><b>x</b>]',
    "[javascript:alert(1)]",
    "[[P0178",
    "[P0178",
    "P0178]",
    "[]",
  ])("leaves %s as plain text with no link, element or handler", async (marker) => {
    const answer = await run(`Text ${marker} end.`, ["P0178"], [RUG]);
    expect(within(answer).queryAllByRole("link")).toHaveLength(0);
    expect(answer.querySelector("img, b, script")).toBeNull();
    expect(answer).toHaveTextContent(`Text ${marker} end.`);
  });

  it("links a nested marker only to the cited inner ID", async () => {
    const answer = await run("See [[P0178]] now.", ["P0178"], [RUG]);
    const links = within(answer).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["#source-P0178"]);
    expect(answer).toHaveTextContent("See [[P0178]] now.");
  });

  it("links a marker with extra spaces only to the cited ID, as one cited ID", async () => {
    const answer = await run("A [ P0178 ] B", ["P0178"], [RUG]);
    const links = within(answer).queryAllByRole("link");
    for (const l of links) expect(l.getAttribute("href")).toBe("#source-P0178");
    expect(answer).toHaveTextContent("A [ P0178 ] B");
  });

  it("links only the cited IDs in a group marker and keeps the text", async () => {
    const answer = await run("Both [P0178, R01080] and [P0178, P9999].", ["P0178", "R01080"], [RUG, REVIEW]);
    const hrefs = within(answer).getAllByRole("link").map((l) => l.getAttribute("href"));
    expect(hrefs).toEqual(["#source-P0178", "#source-R01080", "#source-P0178"]);
    expect(answer).toHaveTextContent("Both [P0178, R01080] and [P0178, P9999].");
  });

  it("never links a marker whose ID is not among the cited ones, whatever it looks like", async () => {
    const answer = await run("[../x] [P0178#x] [P0178-evil]", ["P0178"], [RUG]);
    expect(within(answer).queryAllByRole("link")).toHaveLength(0);
  });

  it("a cited ID that looks like a path links only to a fragment with that text, as an attribute", async () => {
    const answer = await run("Odd [../x].", ["../x"], [{ id: "../x", type: "document", title: "T" }]);
    const [link] = within(answer).getAllByRole("link");
    expect(link?.getAttribute("href")).toBe("#source-../x");
  });

  it("makes no card and no link for a sources entry whose ID was not cited", async () => {
    const answer = await run("Rug [P0178].", ["R01080"], [RUG, REVIEW]);
    expect(within(answer).queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryByRole("article")).toBeNull();
  });

  it.each([
    ["a string", "P0178"],
    ["an object", { P0178: RUG }],
    ["null", null],
    ["a number", 3],
  ])("treats sources that is %s as no cards", (_name, sources) => {
    const { events } = parseFrames(frame("citations", { source_ids: ["P0178"], sources }));
    expect(events).toEqual([{ type: "citations", sourceIds: ["P0178"], sources: [] }]);
  });

  it("renders when sources is malformed, still linking cited markers and showing no card", async () => {
    const answer = await run("Rug [P0178].", ["P0178"], "nope");
    expect(within(answer).getAllByRole("link")).toHaveLength(1);
    expect(screen.queryByRole("article")).toBeNull();
  });
});

describe("[SA-02] card text is rendered as text", () => {
  it("shows a hostile title and a very long title as text, not markup", async () => {
    const title = `<img src=x onerror=alert(1)><b>x</b> ${"T".repeat(5000)}`;
    await run("Rug [P0178].", ["P0178"], [{ ...RUG, title }]);
    const card = await screen.findByRole("article");
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector("b")).toBeNull();
    expect(card).toHaveTextContent("<img src=x onerror=alert(1)><b>x</b>");
  });

  it("does not show a review as a product card even if a title is sent for it", async () => {
    await run("Warm [R01080].", ["R01080"], [{ ...REVIEW, title: "PLANTED TITLE" }]);
    expect(screen.queryByRole("article")).toBeNull();
    expect(screen.queryByText(/PLANTED TITLE/)).toBeNull();
  });

  it("does not show a document as a product card", async () => {
    await run("Returns [FAQ-019].", ["FAQ-019"], [{ id: "FAQ-019", type: "document", title: "Returns" }]);
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("shows a cited variant as a card", async () => {
    await run("Opt [P0178-OCH].", ["P0178-OCH"], [{ id: "P0178-OCH", type: "variant", title: "Larchpell Rug" }]);
    expect(await screen.findByRole("article", { name: "Larchpell Rug" })).toBeInTheDocument();
  });
});
