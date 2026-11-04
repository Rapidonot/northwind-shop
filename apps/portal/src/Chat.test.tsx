import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import { parseFrames } from "./chatStream";

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

function streamOf(...parts: (string | Uint8Array)[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const part of parts) {
        controller.enqueue(typeof part === "string" ? encoder.encode(part) : part);
      }
      controller.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

function mockFetch(response: Response) {
  const fetchMock = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function ask(text: string) {
  render(<Chat />);
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-02] parseFrames", () => {
  it("reads complete frames and keeps the unfinished one", () => {
    const { events, rest } = parseFrames(`${frame("token", { text: "a" })}event: tok`);
    expect(events).toEqual([{ type: "token", text: "a" }]);
    expect(rest).toBe("event: tok");
  });

  it("ignores keep-alive comments and unknown events", () => {
    const { events } = parseFrames(`: ping\n\n${frame("other", {})}${frame("done", {})}`);
    expect(events).toEqual([{ type: "done" }]);
  });

  it("reads CRLF line endings", () => {
    const { events } = parseFrames('event: done\r\ndata: {}\r\n\r\n');
    expect(events).toEqual([{ type: "done" }]);
  });
});

describe("[SA-02] Chat", () => {
  it("says it is an AI before anything is sent", () => {
    render(<Chat />);
    expect(screen.getByText(/chatting with an AI assistant/)).toBeInTheDocument();
  });

  it("sends only the message", async () => {
    const fetchMock = mockFetch(streamOf(frame("done", {})));
    ask("Is it warm?");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/chat");
    expect(JSON.parse(init.body as string)).toEqual({ message: "Is it warm?" });
  });

  it("builds the answer from tokens and lists the source IDs", async () => {
    mockFetch(
      streamOf(
        frame("token", { text: "Wash it " }),
        frame("token", { text: "cold [FAQ-001]." }),
        frame("citations", { source_ids: ["FAQ-001"] }),
        frame("done", {}),
      ),
    );
    ask("How do I wash it?");
    expect(await screen.findByText("FAQ-001")).toBeInTheDocument();
    expect(screen.getByTestId("answer")).toHaveTextContent("Wash it cold [FAQ-001].");
  });

  it("drops the partial text when a cut-off attempt is retried", async () => {
    mockFetch(
      streamOf(
        frame("token", { text: "partial " }),
        frame("reset", {}),
        frame("token", { text: "whole answer" }),
        frame("done", {}),
      ),
    );
    ask("hi");
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent(/^whole answer$/));
  });

  it("survives a frame and a character split across reads", async () => {
    const whole = new TextEncoder().encode(frame("token", { text: "café" }));
    const cut = whole.indexOf(0xc3) + 1; // between the two bytes of é
    mockFetch(streamOf(whole.slice(0, cut), whole.slice(cut), frame("done", {})));
    ask("hi");
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent("café"));
  });

  it.each([
    ["retrieval_failed", /Search isn't available/],
    ["model_unavailable", /isn't available right now/],
    ["replay_miss", /no recorded answer/],
    ["turn_limit", /too many steps/],
    ["internal", /Something went wrong/],
  ])("shows fixed text for %s", async (code, text) => {
    mockFetch(streamOf(frame("error", { code })));
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
  });

  it("shows fixed text for a code it doesn't know", async () => {
    mockFetch(streamOf(frame("error", { code: "something new" })));
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Something went wrong/);
  });

  it("shows the server's fixed refusal", async () => {
    const body = { isError: true, message: "Please don't share card numbers in chat." };
    mockFetch(Response.json(body, { status: 422 }));
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(/card numbers/);
  });

  it("shows an error when the gateway can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("network down"))));
    ask("hi");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Something went wrong/);
  });
});
