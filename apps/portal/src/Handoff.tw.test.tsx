import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AI_DISCLOSURE } from "./AiDisclosure";
import { Chat } from "./Chat";
import { HANDOFF_FAILED, parseFrames, requestHandoff } from "./chatStream";
import {
  HANDOFF_JOINED,
  HANDOFF_QUEUED,
  HANDOFF_REQUESTING,
  HandoffNotice,
  type HandoffStatus,
  NO_STAFF_YET,
} from "./HandoffNotice";

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

const queued = (reference = "H000007") =>
  Response.json({ status: "queued", reference, notice: "ignored" }, { status: 202 });

function stubFetch(...steps: (() => Response | Promise<Response>)[]) {
  const queue = [...steps];
  const mock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() => {
    const next = queue.shift();
    return next ? Promise.resolve(next()) : Promise.reject(new Error("nothing queued"));
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

const talk = () => screen.getByRole("button", { name: "Talk to a person" });

function disclosed() {
  expect(screen.getByRole("note", { name: "AI disclosure" })).toHaveTextContent(AI_DISCLOSURE);
}

async function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeEnabled());
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-06] [NFR-08] requestHandoff", () => {
  it("posts JSON to /api/handoff with the turns and the trigger, and nothing else", async () => {
    const mock = stubFetch(() => queued());
    const result = await requestHandoff([{ role: "shopper", text: "hi" }], "message");
    expect(result).toEqual({ kind: "queued", reference: "H000007" });
    const [url, init] = mock.mock.calls[0] ?? [];
    expect(url).toBe("/api/handoff");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      turns: [{ role: "shopper", text: "hi" }],
      trigger: "message",
    });
  });

  it.each([
    ["a network error", () => Promise.reject(new Error("offline"))],
    ["a 500", () => Response.json({}, { status: 500 })],
    ["a 202 that is not JSON", () => new Response("<html>", { status: 202 })],
    ["a 202 with no reference", () => Response.json({ status: "queued" }, { status: 202 })],
    [
      "a 202 that is not queued",
      () => Response.json({ status: "error", reference: "H000001" }, { status: 202 }),
    ],
    ["a reference with trailing text", () => Response.json({ status: "queued", reference: "H000001\nx" }, { status: 202 })],
    ["a numeric reference", () => Response.json({ status: "queued", reference: 1 }, { status: 202 })],
  ])("is the fixed failure for %s", async (_name, step) => {
    stubFetch(step as () => Response);
    expect(await requestHandoff([], "button")).toEqual({ kind: "failed", message: HANDOFF_FAILED });
  });

  it("shows the gateway's fixed refusal text on a 422, and the fixed failure when it has none", async () => {
    stubFetch(() => Response.json({ isError: true, message: "No card details, please." }, { status: 422 }));
    expect(await requestHandoff([], "button")).toEqual({
      kind: "failed",
      message: "No card details, please.",
    });
    stubFetch(() => new Response("nope", { status: 422 }));
    const result = await requestHandoff([], "button");
    expect(result.kind).toBe("failed");
  });
});

describe("[SA-06] [NFR-08] the handoff event", () => {
  it("is read from the stream as an event with no payload", () => {
    const { events } = parseFrames(`${frame("handoff", {})}${frame("done", {})}`);
    expect(events).toEqual([{ type: "handoff" }, { type: "done" }]);
  });
});

describe("[SA-06] [NFR-08] HandoffNotice", () => {
  it("shows nothing when idle", () => {
    const { container } = render(<HandoffNotice state={{ kind: "idle" }} onRetry={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each([
    [{ kind: "requesting" as const }, HANDOFF_REQUESTING],
    [{ kind: "queued" as const, reference: "H000003" }, HANDOFF_QUEUED],
    [{ kind: "joined" as const, reference: "H000003" }, HANDOFF_JOINED],
  ])("states %j in fixed text", (state, text) => {
    render(<HandoffNotice state={state} onRetry={() => {}} />);
    expect(screen.getByRole("status", { name: "Handoff" })).toHaveTextContent(text);
  });

  it("says in the queued state that the shopper is still talking to an AI", () => {
    render(<HandoffNotice state={{ kind: "queued", reference: "H000003" }} onRetry={() => {}} />);
    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toMatch(/still chatting with an AI assistant/);
    expect(text).not.toMatch(/has joined/);
    expect(text).toContain("H000003");
  });

  it("offers a retry when failed, and calls it once per press", () => {
    const retry = vi.fn();
    render(<HandoffNotice state={{ kind: "failed", message: HANDOFF_FAILED }} onRetry={retry} />);
    expect(screen.getByRole("alert", { name: "Handoff failed" })).toHaveTextContent(HANDOFF_FAILED);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("never reports a join through the default status", () => {
    const off = vi.fn();
    const onJoined = vi.fn();
    NO_STAFF_YET.watch("H000001", onJoined)();
    expect(onJoined).not.toHaveBeenCalled();
    expect(off).not.toHaveBeenCalled();
  });
});

describe("[SA-06] [NFR-08] Chat handoff", () => {
  it("keeps the AI disclosure while the request is still pending", async () => {
    let release: (r: Response) => void = () => {};
    stubFetch(() => new Promise<Response>((resolve) => (release = resolve)) as unknown as Response);
    render(<Chat />);
    fireEvent.click(talk());
    expect(await screen.findByRole("status", { name: "Handoff" })).toHaveTextContent(
      HANDOFF_REQUESTING,
    );
    disclosed();
    expect(talk()).toBeDisabled();
    release(queued());
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Handoff" })).toHaveTextContent(HANDOFF_QUEUED),
    );
    disclosed();
  });

  it("sends one handoff however many times the button is pressed", async () => {
    const mock = stubFetch(() => queued());
    render(<Chat />);
    fireEvent.click(talk());
    fireEvent.click(talk());
    await screen.findByText(/Your reference is H000007/);
    fireEvent.click(talk());
    expect(mock).toHaveBeenCalledTimes(1);
  });

  it("does not claim a person has joined until the status says so, and stops watching on unmount", async () => {
    const unwatch = vi.fn();
    let joined: () => void = () => {};
    const status: HandoffStatus = {
      watch: (_reference, onJoined) => {
        joined = onJoined;
        return unwatch;
      },
    };
    stubFetch(() => queued("H000009"));
    const view = render(<Chat handoffStatus={status} />);
    fireEvent.click(talk());
    await screen.findByText(/Your reference is H000009/);
    expect(screen.queryByText(HANDOFF_JOINED, { exact: false })).toBeNull();
    joined();
    await screen.findByText(HANDOFF_JOINED, { exact: false });
    expect(screen.getByRole("status", { name: "Handoff" })).not.toHaveTextContent("still chatting");
    disclosed();
    view.unmount();
    expect(unwatch).toHaveBeenCalled();
  });

  it("includes the message that asked for a person in the handoff's turns", async () => {
    const mock = stubFetch(() => streamOf(frame("handoff", {}), frame("done", {})), () => queued());
    render(<Chat />);
    await ask("please connect me with a human");
    await screen.findByText(/Your reference is H000007/);
    const [, init] = mock.mock.calls[1] ?? [];
    const body = JSON.parse(String(init?.body)) as { turns: { role: string; text: string }[] };
    expect(body.turns.at(-1)).toEqual({ role: "shopper", text: "please connect me with a human" });
    expect(body.turns.every((t) => t.role === "shopper" || t.role === "assistant")).toBe(true);
  });

  it("keeps a turn the model failed on, with the shopper's words and no invented answer", async () => {
    const mock = stubFetch(
      () => streamOf(frame("error", { code: "model_unavailable" })),
      () => queued(),
    );
    render(<Chat />);
    await ask("my parcel is late");
    fireEvent.click(talk());
    await screen.findByText(/Your reference is H000007/);
    const [, init] = mock.mock.calls[1] ?? [];
    const body = JSON.parse(String(init?.body)) as { turns: { role: string; text: string }[] };
    expect(body.turns).toEqual([{ role: "shopper", text: "my parcel is late" }]);
  });

  it("sends turns the gateway will accept: at most 2000 characters each", async () => {
    const long = "w".repeat(2600);
    const mock = stubFetch(
      () => streamOf(frame("token", { text: long }), frame("done", {})),
      () => queued(),
    );
    render(<Chat />);
    await ask(long);
    fireEvent.click(talk());
    await screen.findByText(/Your reference is H000007|can't be reached/);
    const [, init] = mock.mock.calls[1] ?? [];
    const body = JSON.parse(String(init?.body)) as { turns: { text: string }[] };
    for (const turn of body.turns) expect(turn.text.length).toBeLessThanOrEqual(2000);
  });

  it("offers the button from the first render and keeps it after a failed handoff", async () => {
    stubFetch(() => Response.json({}, { status: 503 }));
    render(<Chat />);
    expect(talk()).toBeEnabled();
    fireEvent.click(talk());
    await screen.findByRole("alert", { name: "Handoff failed" });
    expect(talk()).toBeEnabled();
    disclosed();
  });
});
