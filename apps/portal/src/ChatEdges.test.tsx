import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

function streamOf(...parts: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const part of parts) controller.enqueue(encoder.encode(part));
      controller.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

function mockFetch(...responses: Response[]) {
  const queue = [...responses];
  const fetchMock = vi.fn(() => Promise.resolve(queue.shift() as Response));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-02] Chat edges", () => {
  it("keeps the AI notice on screen while streaming and after an error", async () => {
    mockFetch(streamOf(frame("error", { code: "internal" })));
    render(<Chat />);
    ask("hi");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/chatting with an AI assistant/)).toBeInTheDocument();
  });

  it("keeps the text already streamed when an error follows it", async () => {
    mockFetch(streamOf(frame("token", { text: "Wash it " }), frame("error", { code: "internal" })));
    render(<Chat />);
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Something went wrong/);
    expect(screen.getByTestId("answer")).toHaveTextContent("Wash it");
  });

  it("shows no source list when none were cited", async () => {
    mockFetch(streamOf(frame("token", { text: "No idea." }), frame("citations", { source_ids: [] })));
    render(<Chat />);
    ask("hi");
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent("No idea."));
    expect(screen.queryByRole("list", { name: "Sources" })).not.toBeInTheDocument();
  });

  it("lists every cited source once, in order", async () => {
    mockFetch(streamOf(frame("citations", { source_ids: ["FAQ-002", "P0011", "R00001"] })));
    render(<Chat />);
    ask("hi");
    const list = await screen.findByRole("list", { name: "Sources" });
    expect(Array.from(list.querySelectorAll("li")).map((li) => li.textContent)).toEqual([
      "FAQ-002",
      "P0011",
      "R00001",
    ]);
  });

  it("clears the last answer, sources and notice when a new question is sent", async () => {
    mockFetch(
      streamOf(
        frame("token", { text: "First answer" }),
        frame("citations", { source_ids: ["FAQ-001"] }),
        frame("error", { code: "turn_limit" }),
      ),
      streamOf(frame("token", { text: "Second answer" }), frame("done", {})),
    );
    render(<Chat />);
    ask("one");
    expect(await screen.findByText("FAQ-001")).toBeInTheDocument();
    ask("two");
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent("Second answer"));
    expect(screen.getByTestId("answer")).not.toHaveTextContent("First answer");
    expect(screen.queryByText("FAQ-001")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders streamed text and source IDs as plain text, never as markup", async () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><b>bold</b>';
    mockFetch(
      streamOf(
        frame("token", { text: hostile }),
        frame("citations", { source_ids: [hostile] }),
        frame("done", {}),
      ),
    );
    const { container } = render(<Chat />);
    ask("hi");
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent("<b>bold</b>"));
    expect(container.querySelector("img, b")).toBeNull();
  });

  it("does not send an empty or blank question", () => {
    const fetchMock = mockFetch();
    render(<Chat />);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    ask("   ");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["constructor", "toString", "__proto__", "hasOwnProperty"])(
    "shows the fixed generic text for the error code %s",
    async (code) => {
      mockFetch(streamOf(frame("error", { code })));
      render(<Chat />);
      ask("hi");
      expect(await screen.findByRole("alert")).toHaveTextContent(/Something went wrong/);
    },
  );

  it("shows fixed text, not the body, for a server failure", async () => {
    mockFetch(new Response("Traceback: password=hunter2", { status: 500 }));
    render(<Chat />);
    ask("hi");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Something went wrong/);
    expect(document.body).not.toHaveTextContent("hunter2");
  });

  it("shows a generic refusal when a 422 has no usable body", async () => {
    mockFetch(new Response("<html>nope</html>", { status: 422 }));
    render(<Chat />);
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That message could not be accepted.",
    );
  });

  it("shows fixed text when an event's data is not JSON", async () => {
    mockFetch(streamOf("event: token\ndata: {not json\n\n"));
    render(<Chat />);
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Something went wrong/);
  });

  it("does not echo the typed message back into the page as the answer", async () => {
    mockFetch(streamOf(frame("done", {})));
    render(<Chat />);
    ask("my question text");
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeDisabled());
    expect(screen.getByTestId("answer")).toBeEmptyDOMElement();
  });
});
