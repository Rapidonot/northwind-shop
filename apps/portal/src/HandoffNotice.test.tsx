import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AI_DISCLOSURE } from "./AiDisclosure";
import { Chat, MAX_HANDOFF_TURNS } from "./Chat";
import { HANDOFF_FAILED, HANDOFF_TURNS_REFUSED } from "./chatStream";
import { HANDOFF_JOINED, HANDOFF_QUEUED, type HandoffStatus } from "./HandoffNotice";

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

const queued = (reference = "H000042") =>
  Response.json({ status: "queued", reference, notice: "server text" }, { status: 202 });

function respondWith(...responses: (() => Response)[]) {
  const queue = [...responses];
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() => {
    const next = queue.shift();
    return next ? Promise.resolve(next()) : Promise.reject(new Error("no response queued"));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function bodyOf(fetchMock: ReturnType<typeof respondWith>, call: number) {
  const [url, init] = fetchMock.mock.calls[call] ?? [];
  return { url, body: JSON.parse(String(init?.body)) as Record<string, unknown> };
}

async function ask(text: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeEnabled());
}

const talkButton = () => screen.getByRole("button", { name: "Talk to a person" });

function expectDisclosureShown() {
  const note = screen.getByRole("note", { name: "AI disclosure" });
  expect(note).toBeVisible();
  expect(note).toHaveTextContent(AI_DISCLOSURE);
  const handoff = screen.queryByRole("status", { name: "Handoff" });
  if (handoff) expect(handoff).not.toContainElement(note);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[SA-06] [NFR-08] handoff to a person", () => {
  it("is always offered, and the button asks for a person with the turns shown so far", async () => {
    const fetchMock = respondWith(
      () => streamOf(frame("token", { text: "Wash it cold." }), frame("done", {})),
      () => queued(),
    );
    render(<Chat />);
    expect(talkButton()).toBeEnabled();
    await ask("How do I wash this?");
    fireEvent.click(talkButton());
    const status = await screen.findByRole("status", { name: "Handoff" });
    await waitFor(() => expect(status).toHaveTextContent(HANDOFF_QUEUED));
    expect(status).toHaveTextContent("Your reference is H000042.");
    expect(status).not.toHaveTextContent("server text");
    const { url, body } = bodyOf(fetchMock, 1);
    expect(url).toBe("/api/handoff");
    expect(body).toEqual({
      trigger: "button",
      turns: [
        { role: "shopper", text: "How do I wash this?" },
        { role: "assistant", text: "Wash it cold." },
      ],
    });
    expect(talkButton()).toBeDisabled();
    expectDisclosureShown();
  });

  it("hands off at once when the gateway reads a message as a request for a person", async () => {
    const fetchMock = respondWith(
      () => streamOf(frame("handoff", {}), frame("done", {})),
      () => queued("H000001"),
    );
    render(<Chat />);
    await ask("Can I talk to a real person?");
    const status = await screen.findByRole("status", { name: "Handoff" });
    await waitFor(() => expect(status).toHaveTextContent(HANDOFF_QUEUED));
    expect(screen.getByTestId("answer")).toHaveTextContent("");
    expect(bodyOf(fetchMock, 1).body).toEqual({
      trigger: "message",
      turns: [{ role: "shopper", text: "Can I talk to a real person?" }],
    });
    expectDisclosureShown();
  });

  it.each([
    ["a 503", () => Response.json({ isError: true }, { status: 503 })],
    ["a malformed reference", () => Response.json({ status: "queued", reference: "X1" }, { status: 202 })],
    ["a 200 instead of 202", () => Response.json({ status: "queued", reference: "H000001" })],
  ])("shows the fixed failure and a retry after %s", async (_name, failure) => {
    respondWith(failure, () => queued());
    render(<Chat />);
    fireEvent.click(talkButton());
    const alert = await screen.findByRole("alert", { name: "Handoff failed" });
    expect(alert).toHaveTextContent(HANDOFF_FAILED);
    expect(talkButton()).toBeEnabled();
    expectDisclosureShown();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Handoff" })).toHaveTextContent(HANDOFF_QUEUED),
    );
  });

  it("never sends a refused message in a handoff", async () => {
    const fetchMock = respondWith(
      () =>
        Response.json(
          { isError: true, message: "Please don't share card details in chat." },
          { status: 422 },
        ),
      () => queued(),
    );
    render(<Chat />);
    await ask("my card is 4111 1111 1111 1111");
    fireEvent.click(talkButton());
    await screen.findByRole("status", { name: "Handoff" });
    expect(bodyOf(fetchMock, 1).body.turns).toEqual([]);
  });

  it("sends only the text the shopper was left seeing, and at most the last turns", async () => {
    const answers = Array.from({ length: 7 }, (_, n) => () =>
      streamOf(
        frame("token", { text: "draft" }),
        frame("reset", {}),
        frame("token", { text: `answer ${n}` }),
        frame("done", {}),
      ),
    );
    const fetchMock = respondWith(...answers, () => queued());
    render(<Chat />);
    for (let n = 0; n < 7; n++) await ask(`question ${n}`);
    fireEvent.click(talkButton());
    await screen.findByRole("status", { name: "Handoff" });
    const turns = bodyOf(fetchMock, 7).body.turns as { text: string }[];
    expect(turns).toHaveLength(MAX_HANDOFF_TURNS);
    expect(turns.at(-1)?.text).toBe("answer 6");
    expect(turns.map((t) => t.text)).not.toContain("draft");
    expect(turns[0]?.text).toBe("question 2");
  });

  it("tells the shopper when a person has joined, and keeps the AI disclosure", async () => {
    let joined: () => void = () => {};
    const status: HandoffStatus = {
      watch: (reference, onJoined) => {
        expect(reference).toBe("H000042");
        joined = onJoined;
        return () => {};
      },
    };
    respondWith(() => queued());
    render(<Chat handoffStatus={status} />);
    fireEvent.click(talkButton());
    await screen.findByText(/Your reference is H000042/);
    joined();
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Handoff" })).toHaveTextContent(HANDOFF_JOINED),
    );
    expectDisclosureShown();
  });

  it("by default never reports a join, because no staff side exists yet", async () => {
    respondWith(() => queued());
    render(<Chat />);
    fireEvent.click(talkButton());
    await screen.findByText(/Your reference is H000042/);
    expect(screen.queryByText(HANDOFF_JOINED)).toBeNull();
  });
});

describe("[SA-06] security review L1 and L7", () => {
  it("drops refused turns, so the retry asks for a person without them and succeeds", async () => {
    const fetchMock = respondWith(
      () => streamOf(frame("token", { text: "ok" }), frame("done", {})),
      () => Response.json({ isError: true, message: "chat's text" }, { status: 422 }),
      () => queued(),
    );
    render(<Chat />);
    await ask("my card is 4111 1111");
    fireEvent.click(talkButton());
    const alert = await screen.findByRole("alert", { name: "Handoff failed" });
    expect(alert).toHaveTextContent(HANDOFF_TURNS_REFUSED);
    expect(alert).not.toHaveTextContent("chat's text");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "Handoff" })).toHaveTextContent(HANDOFF_QUEUED),
    );
    expect(bodyOf(fetchMock, 2).body.turns).toEqual([]);
    expectDisclosureShown();
  });

  it("shows a chat refusal's message only when it is a string", async () => {
    respondWith(() => Response.json({ isError: true, message: { html: "<b>x</b>" } }, { status: 422 }));
    render(<Chat />);
    await ask("hi");
    expect(screen.getByRole("alert")).toHaveTextContent("That message could not be accepted.");
    expectDisclosureShown();
  });
});

describe("[SA-06] security re-review N4", () => {
  it("keeps a turn sent while a refused handoff was in flight", async () => {
    let refuse!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      refuse = resolve;
    });
    const queue: (() => Promise<Response>)[] = [
      () => Promise.resolve(streamOf(frame("token", { text: "first" }), frame("done", {}))),
      () => pending,
      () => Promise.resolve(streamOf(frame("token", { text: "second" }), frame("done", {}))),
      () => Promise.resolve(queued()),
    ];
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() => {
      const next = queue.shift();
      return next ? next() : Promise.reject(new Error("no response queued"));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Chat />);
    await ask("question one");
    fireEvent.click(talkButton());
    await ask("question two");
    refuse(Response.json({ isError: true }, { status: 422 }));
    await screen.findByRole("alert", { name: "Handoff failed" });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText(/Your reference is H000042/);
    expect(bodyOf(fetchMock as never, 3).body.turns).toEqual([
      { role: "shopper", text: "question two" },
      { role: "assistant", text: "second" },
    ]);
  });
});
