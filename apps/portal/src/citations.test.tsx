import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import cases from "./citationCases.json";

// Row #30, finding M1: the portal reads a citation group as the server does. `citationCases.json`
// is also run by services/assistant/tests/test_30_citations.py.

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

async function answer(text: string, ids: string[]) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const frames = [
        frame("token", { text }),
        frame("citations", { source_ids: ids, sources: ids.map((id) => ({ id, type: "review" })) }),
        frame("done", {}),
      ];
      controller.enqueue(new TextEncoder().encode(frames.join("")));
      controller.close();
    },
  });
  const response = new Response(body, { headers: { "Content-Type": "text/event-stream" } });
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response)));
  render(<Chat />);
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "q" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  const shown = await screen.findByTestId("answer");
  await vi.waitFor(() => expect(shown).toHaveTextContent(/\S/));
  await vi.waitFor(() =>
    expect(within(shown).queryAllByRole("link")).toHaveLength(ids.length),
  );
  return shown;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-02] a citation group is read as the server reads it", () => {
  it.each(cases)("[$group]", async ({ group, ids }) => {
    const text = `Warm [${group}].`;
    const shown = await answer(text, ids);
    const links = within(shown).queryAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual(
      ids.map((id) => `#source-${id}`),
    );
    expect(shown.textContent).toBe(text);
  });
});
