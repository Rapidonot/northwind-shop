import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import questions from "../e2e/questions.json" with { type: "json" };
import { App } from "./App";
import { Chat } from "./Chat";
import { SUGGESTIONS } from "./suggestions";

// SA-01 PR 2: the assistant panel's suggested questions. They are recorded smoke questions, word for
// word, and each replays only as the first message of a fresh conversation (the user's change to
// Amendment 3). Look round 1: they show only while the chat is empty; "New conversation" empties it
// again, and a click sends exactly the question.

const frame = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

function answer(text: string): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(frame("token", { text })));
      controller.enqueue(encoder.encode(frame("citations", { source_ids: [], sources: [] })));
      controller.enqueue(encoder.encode(frame("done", {})));
      controller.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

type Call = { url: string; body: unknown };

/** Every request with its JSON body; chat answers are numbered so each turn is told apart. */
function serve() {
  const calls: Call[] = [];
  let n = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
      if (url === "/api/chat") return Promise.resolve(answer(`Answer ${++n}.`));
      if (url === "/api/handoff") return Promise.resolve(Response.json({ reference: "HO-1" }, { status: 202 }));
      if (url === "/api/cart") return Promise.resolve(Response.json({ cart: { region: "US", lines: [], item_count: 0, subtotal: null, currency: null } }));
      if (url === "/api/departments") return Promise.resolve(Response.json({ departments: [], counts: {}, total: 0 }));
      return Promise.resolve(new Response("", { status: 503 }));
    }),
  );
  return calls;
}

const chats = (calls: Call[]) => calls.filter((c) => c.url === "/api/chat");

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("[SA-01] suggested questions are the recorded ones, word for word", () => {
  it("are the product, care and return-policy smoke questions from questions.json", () => {
    expect(SUGGESTIONS).toEqual([questions.product, questions.care, questions.false_policy]);
  });

  it("are shown as buttons with exactly that text", () => {
    serve();
    render(<Chat suggestions={SUGGESTIONS} />);
    const group = screen.getByRole("group", { name: "Suggested questions" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual([...SUGGESTIONS]);
  });

  it("a click sends exactly the question, as the only field", async () => {
    const calls = serve();
    render(<Chat suggestions={SUGGESTIONS} />);
    fireEvent.click(screen.getByRole("button", { name: questions.care }));
    expect(await screen.findByText("Answer 1.")).toBeInTheDocument();
    expect(chats(calls).map((c) => c.body)).toEqual([{ message: questions.care }]);
  });
});

describe("[SA-01] a suggestion after an answer is a fresh conversation's first message", () => {
  it("shows the suggestions only while the chat is empty, and New conversation brings them back", async () => {
    serve();
    render(<Chat suggestions={SUGGESTIONS} />);
    expect(screen.getByRole("group", { name: "Suggested questions" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: questions.care }));
    await screen.findByText("Answer 1.");
    expect(screen.queryByRole("group", { name: "Suggested questions" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByRole("group", { name: "Suggested questions" })).toBeInTheDocument();
    expect(screen.queryByText("Answer 1.")).toBeNull();
    expect(screen.queryByLabelText("Your message")).toBeNull();
  });

  it("drops the last question and answer, and sends the same request as a first message", async () => {
    const calls = serve();
    render(<Chat suggestions={SUGGESTIONS} />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Do you sell tents?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Answer 1.")).toBeInTheDocument();
    expect(screen.getByLabelText("Your message")).toHaveTextContent("Do you sell tents?");

    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    fireEvent.click(screen.getByRole("button", { name: questions.product }));
    expect(await screen.findByText("Answer 2.")).toBeInTheDocument();
    expect(screen.queryByText("Answer 1.")).toBeNull();
    expect(screen.queryByText("Do you sell tents?")).toBeNull();
    expect(screen.getByLabelText("Your message")).toHaveTextContent(questions.product);
    expect(screen.getByLabelText("Your question")).toHaveValue("");
    // Byte for byte the request a first message would send: nothing of the earlier turn rides along.
    const [, second] = chats(calls);
    expect(second?.body).toEqual({ message: questions.product });
  });

  it("a handoff afterwards sends only the new conversation's turns", async () => {
    const calls = serve();
    render(<Chat suggestions={SUGGESTIONS} />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "Do you sell tents?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Answer 1.");
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    fireEvent.click(screen.getByRole("button", { name: questions.false_policy }));
    await screen.findByText("Answer 2.");
    fireEvent.click(screen.getByRole("button", { name: "Talk to a person" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/handoff")).toBe(true));
    const handoff = calls.find((c) => c.url === "/api/handoff");
    expect(handoff?.body).toEqual({
      turns: [
        { role: "shopper", text: questions.false_policy },
        { role: "assistant", text: "Answer 2." },
      ],
      trigger: "button",
    });
  });

  it("nothing can start a new conversation while an answer is streaming", async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => (release = resolve))),
    );
    render(<Chat suggestions={SUGGESTIONS} />);
    fireEvent.click(screen.getByRole("button", { name: questions.care }));
    expect(screen.queryByRole("group", { name: "Suggested questions" })).toBeNull();
    expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
    release(answer("Done."));
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New conversation" })).toBeEnabled();
  });

  it("a question that looks like card data is never shown back", async () => {
    serve();
    render(<Chat suggestions={SUGGESTIONS} />);
    fireEvent.change(screen.getByLabelText("Your question"), { target: { value: "my card 4242 4242 4242 4242" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Answer 1.");
    expect(screen.queryByLabelText("Your message")).toBeNull();
    expect(document.body.textContent).not.toMatch(/4242/);
  });
});

describe("[SA-01] [NFR-08] the assistant panel", () => {
  it("is labelled as the AI assistant, with no DEMO badge, and offers the suggestions", async () => {
    serve();
    render(<App />);
    const panel = screen.getByRole("complementary", { name: "Ask Northwind" });
    expect(within(panel).getByRole("heading", { name: "Northwind AI assistant" })).toBeInTheDocument();
    expect(within(panel).getByRole("note", { name: "AI disclosure" })).toBeInTheDocument();
    expect(panel.textContent).not.toMatch(/\bdemo\b/i);
    expect(within(panel).getByRole("group", { name: "Suggested questions" })).toBeInTheDocument();
  });

  it("opens with ASK NORTHWIND and closes with Close", async () => {
    serve();
    render(<App />);
    const button = screen.getByRole("button", { name: "ASK NORTHWIND" });
    const panel = screen.getByRole("complementary", { name: "Ask Northwind" });
    expect(panel).toHaveAttribute("inert");
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-expanded", "true"));
    expect(panel).not.toHaveAttribute("inert");
    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(button).toHaveAttribute("aria-expanded", "false"));
    expect(panel).toHaveAttribute("inert");
  });

  it("starts open on a desktop-width screen, so the AI disclosure shows from the first load", () => {
    serve();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(min-width: 900px)" }));
    render(<App />);
    expect(screen.getByRole("button", { name: "ASK NORTHWIND" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("complementary", { name: "Ask Northwind" })).not.toHaveAttribute("inert");
  });
});
