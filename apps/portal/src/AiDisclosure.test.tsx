import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AI_DISCLOSURE } from "./AiDisclosure";
import { Chat } from "./Chat";
import type { ChatEvent, ErrorCode } from "./chatStream";

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

function respondWith(...responses: (() => Response)[]) {
  const queue = [...responses];
  const fetchMock = vi.fn(() => {
    const next = queue.shift();
    return next ? Promise.resolve(next()) : Promise.reject(new Error("no response queued"));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

const sendButton = () => screen.getByRole("button", { name: "Send" });

/** The turn has finished: `busy` is false again, and the question is still typed. */
const turnOver = () => waitFor(() => expect(sendButton()).toBeEnabled());

function expectDisclosureShown() {
  const note = screen.getByRole("note", { name: "AI disclosure" });
  expect(note).toBeVisible();
  expect(note).toHaveTextContent(AI_DISCLOSURE);
  expect(note).toHaveTextContent(/AI assistant, not a person/);
  // Outside the parts of the page a turn rewrites, so no turn state can clear it.
  expect(screen.getByTestId("answer")).not.toContainElement(note);
  for (const alert of screen.queryAllByRole("alert")) expect(alert).not.toContainElement(note);
}

// One response per event type. A new `ChatEvent` type fails `tsc` here until it is listed.
// `refused` has no SSE frame: the gateway sends it as a 422.
const ONE_OF_EACH_EVENT: Record<ChatEvent["type"], () => Response> = {
  token: () => streamOf(frame("token", { text: "Wash it cold." }), frame("done", {})),
  reset: () =>
    streamOf(frame("token", { text: "part" }), frame("reset", {}), frame("done", {})),
  citations: () => streamOf(frame("citations", { source_ids: ["FAQ-001"] }), frame("done", {})),
  error: () => streamOf(frame("error", { code: "internal" })),
  refused: () =>
    Response.json({ isError: true, message: "That message could not be accepted." }, { status: 422 }),
  done: () => streamOf(frame("done", {})),
};

// Every error code. A new `ErrorCode` fails `tsc` here until it is listed.
const EVERY_ERROR_CODE: Record<ErrorCode, true> = {
  retrieval_failed: true,
  model_unavailable: true,
  replay_miss: true,
  internal: true,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[NFR-08] AI disclosure", () => {
  it("is shown when the chat opens, before anything is sent", () => {
    const fetchMock = respondWith();
    render(<Chat />);
    expectDisclosureShown();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stays shown while an answer is still streaming", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
      },
    });
    respondWith(() => new Response(body, { headers: { "Content-Type": "text/event-stream" } }));
    render(<Chat />);
    ask("hi");
    controller.enqueue(new TextEncoder().encode(frame("token", { text: "Half an" })));
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent("Half an"));
    expect(sendButton()).toBeDisabled();
    expectDisclosureShown();
    controller.enqueue(new TextEncoder().encode(frame("done", {})));
    controller.close();
    await turnOver();
    expectDisclosureShown();
  });

  it.each(Object.entries(ONE_OF_EACH_EVENT))("stays shown after a %s event", async (_type, reply) => {
    respondWith(reply);
    render(<Chat />);
    ask("hi");
    await turnOver();
    expectDisclosureShown();
  });

  it.each(Object.keys(EVERY_ERROR_CODE))("stays shown after the error code %s", async (code) => {
    respondWith(() => streamOf(frame("error", { code })));
    render(<Chat />);
    ask("hi");
    await screen.findByRole("alert");
    await turnOver();
    expectDisclosureShown();
  });

  it("stays shown after an error code it doesn't know", async () => {
    respondWith(() => streamOf(frame("error", { code: "something new" })));
    render(<Chat />);
    ask("hi");
    await screen.findByRole("alert");
    expectDisclosureShown();
  });

  it("stays shown when the gateway can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("network down"))));
    render(<Chat />);
    ask("hi");
    await screen.findByRole("alert");
    expectDisclosureShown();
  });

  it("stays shown through a second turn", async () => {
    respondWith(ONE_OF_EACH_EVENT.error, ONE_OF_EACH_EVENT.token);
    render(<Chat />);
    ask("one");
    await screen.findByRole("alert");
    expectDisclosureShown();
    ask("two");
    await waitFor(() => expect(screen.getByTestId("answer")).toHaveTextContent("Wash it cold."));
    await turnOver();
    expectDisclosureShown();
  });
});
