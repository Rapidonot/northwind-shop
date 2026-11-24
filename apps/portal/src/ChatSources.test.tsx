import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import { parseFrames } from "./chatStream";

// Row #17 (SA-02): "every product claim shows a source link". Each [ID] marker the gateway cites
// becomes a link to that source's entry, and cited products show as inline cards. Card text comes
// only from the gateway's card fields: never a price or stock figure (hard rule 2), and nothing
// from a review's text (hard rule 6).

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

function ask(text: string) {
  render(<Chat />);
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

const RUG = {
  id: "P0178",
  type: "product",
  title: "Larchpell Larch Area Rug",
  brand: "Larchpell",
  department: "Living",
};
const REVIEW = { id: "R01080", type: "review" };
const FAQ = { id: "FAQ-019", type: "document", title: "Returns (United States)" };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-02] source links and product cards", () => {
  it("reads the cards from the citations event, and none when an older gateway sends none", () => {
    const { events } = parseFrames(
      frame("citations", { source_ids: ["P0178"], sources: [RUG] }) +
        frame("citations", { source_ids: ["FAQ-001"] }),
    );
    expect(events).toEqual([
      { type: "citations", sourceIds: ["P0178"], sources: [RUG] },
      { type: "citations", sourceIds: ["FAQ-001"], sources: [] },
    ]);
  });

  it("links every cited marker to a source entry that exists", async () => {
    respond(
      frame("token", { text: "A flatweave rug [P0178]. Returns take 30 days [FAQ-019]. " }),
      frame("token", { text: "One reviewer found it warm [R01080]." }),
      frame("citations", { source_ids: ["P0178", "FAQ-019", "R01080"], sources: [RUG, FAQ, REVIEW] }),
      frame("done", {}),
    );
    ask("Tell me about the rug");
    const answer = screen.getByTestId("answer");
    const links = await within(answer).findAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["[P0178]", "[FAQ-019]", "[R01080]"]);
    for (const link of links) {
      const target = (link.getAttribute("href") ?? "").replace(/^#/, "");
      expect(document.getElementById(target)).not.toBeNull();
    }
    // The answer reads as it streamed: the links change no text.
    expect(answer).toHaveTextContent(
      "A flatweave rug [P0178]. Returns take 30 days [FAQ-019]. One reviewer found it warm [R01080].",
    );
  });

  it("leaves a marker the gateway did not cite as plain text", async () => {
    respond(
      frame("token", { text: "It is warm [P0178] and fireproof [P9999]." }),
      frame("citations", { source_ids: ["P0178"], sources: [RUG] }),
      frame("done", {}),
    );
    ask("Is it warm?");
    const answer = screen.getByTestId("answer");
    const links = await within(answer).findAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["[P0178]"]);
    expect(answer).toHaveTextContent("fireproof [P9999].");
  });

  it("shows a cited product as an inline card with its name, brand, department and source link", async () => {
    respond(
      frame("token", { text: "A flatweave rug [P0178]." }),
      frame("citations", { source_ids: ["P0178"], sources: [RUG] }),
      frame("done", {}),
    );
    ask("Tell me about the rug");
    const card = await screen.findByRole("article", { name: "Larchpell Larch Area Rug" });
    expect(card).toHaveTextContent("Larchpell");
    expect(card).toHaveTextContent("Living");
    expect(within(card).getByRole("link", { name: "Source P0178" })).toHaveAttribute(
      "href",
      "#source-P0178",
    );
    expect(card.textContent ?? "").not.toMatch(/\$|USD|GBP|EUR|in stock|price/i);
  });

  it("shows a review as a source entry with no product card and no review text", async () => {
    respond(
      frame("token", { text: "One reviewer found it warm [R01080]." }),
      frame("citations", { source_ids: ["R01080"], sources: [REVIEW] }),
      frame("done", {}),
    );
    ask("Is it warm?");
    const sources = await screen.findByRole("list", { name: "Sources" });
    expect(within(sources).getByText("R01080")).toBeInTheDocument();
    expect(within(sources).getByText(/Shopper review/)).toBeInTheDocument();
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("names each source's type and title in the Sources list", async () => {
    respond(
      frame("token", { text: "Returns take 30 days [FAQ-019]." }),
      frame("citations", { source_ids: ["FAQ-019"], sources: [FAQ] }),
      frame("done", {}),
    );
    ask("Can I return it?");
    const sources = await screen.findByRole("list", { name: "Sources" });
    expect(within(sources).getByText("FAQ-019")).toBeInTheDocument();
    expect(sources).toHaveTextContent("Returns (United States)");
  });
});
