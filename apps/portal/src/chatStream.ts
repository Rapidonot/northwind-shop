// POST /api/chat, read as a stream. EventSource is GET only, so this reads the body with fetch.

export type ErrorCode =
  | "retrieval_failed"
  | "model_unavailable"
  | "replay_miss"
  | "internal";

/** A cited source, as the gateway describes it (row #17, SA-02). Title, brand and department come
 * only from trusted sources; a review has its ID and type only. There is no price or stock field. */
export type SourceCard = {
  id: string;
  type: string;
  title?: string;
  brand?: string;
  department?: string;
};

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "reset" }
  | { type: "citations"; sourceIds: string[]; sources: SourceCard[] }
  | { type: "error"; code: ErrorCode }
  | { type: "refused"; message: string }
  // Row #23 (SA-06): the gateway's deterministic trigger read the message as a request for a person.
  // No model saw it; the portal then asks for the handoff itself (`requestHandoff`).
  | { type: "handoff" }
  | { type: "done" };

const GENERIC_REFUSAL = "That message could not be accepted.";

const CARD_TEXT = ["title", "brand", "department"] as const;

/** A card is kept only with a string `id` and `type`; of the rest, only string fields are copied
 * (row #17, security review L1), so a malformed item can neither crash the chat nor make a card. */
function toCard(item: unknown): SourceCard[] {
  if (typeof item !== "object" || item === null) return [];
  const raw = item as Record<string, unknown>;
  if (typeof raw.id !== "string" || typeof raw.type !== "string") return [];
  const card: SourceCard = { id: raw.id, type: raw.type };
  for (const key of CARD_TEXT) {
    const value = raw[key];
    if (typeof value === "string") card[key] = value;
  }
  return [card];
}

function toEvent(name: string, data: string): ChatEvent | null {
  const body = JSON.parse(data) as Record<string, unknown>;
  switch (name) {
    case "token":
      return { type: "token", text: body.text as string };
    case "reset":
      return { type: "reset" };
    case "citations":
      return {
        type: "citations",
        sourceIds: body.source_ids as string[],
        sources: Array.isArray(body.sources) ? body.sources.flatMap(toCard) : [],
      };
    case "error":
      return { type: "error", code: body.code as ErrorCode };
    case "handoff":
      return { type: "handoff" };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

function parseFrame(frame: string): ChatEvent | null {
  let name = "";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return name && data.length > 0 ? toEvent(name, data.join("\n")) : null;
}

/** Splits off every complete frame; `rest` is the part still waiting for its blank line. */
export function parseFrames(buffer: string): { events: ChatEvent[]; rest: string } {
  const frames = buffer.replaceAll("\r\n", "\n").split("\n\n");
  const rest = frames.pop() ?? "";
  const events = frames.map(parseFrame).filter((event) => event !== null);
  return { events, rest };
}

async function refusal(response: Response): Promise<ChatEvent> {
  try {
    const body = (await response.json()) as { message?: string };
    return { type: "refused", message: body.message ?? GENERIC_REFUSAL };
  } catch {
    return { type: "refused", message: GENERIC_REFUSAL };
  }
}

export async function sendMessage(
  message: string,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (response.status === 422) {
    onEvent(await refusal(response));
    return;
  }
  if (!response.ok || !response.body) {
    onEvent({ type: "error", code: "internal" });
    return;
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let ended = false; // a stream that stops with neither `done` nor `error` is a cut-off answer
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      if (!ended) onEvent({ type: "error", code: "internal" });
      return;
    }
    const { events, rest } = parseFrames(buffer + value);
    buffer = rest;
    for (const event of events) {
      ended ||= event.type === "done" || event.type === "error";
      onEvent(event);
    }
  }
}

/** One turn of the conversation as the shopper saw it, for the handoff summary only (row #23). */
export type HandoffTurn = { role: "shopper" | "assistant"; text: string };

export type HandoffResult =
  | { kind: "queued"; reference: string }
  | { kind: "failed"; message: string };

export const HANDOFF_FAILED = "A person can't be reached from here right now. Please try again.";
const REFERENCE = /^H\d{6}$/;

/** POST /api/handoff (row #23, SA-06). Only a well-formed reference counts as queued; anything else
 * is the fixed failure text, or the gateway's own fixed refusal for a 422. */
export async function requestHandoff(
  turns: HandoffTurn[],
  trigger: "button" | "message",
): Promise<HandoffResult> {
  try {
    const response = await fetch("/api/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turns, trigger }),
    });
    if (response.status === 422) {
      const refused = await refusal(response);
      return { kind: "failed", message: refused.type === "refused" ? refused.message : HANDOFF_FAILED };
    }
    if (response.status !== 202) return { kind: "failed", message: HANDOFF_FAILED };
    const body = (await response.json()) as { status?: unknown; reference?: unknown };
    if (body.status !== "queued" || typeof body.reference !== "string" || !REFERENCE.test(body.reference)) {
      return { kind: "failed", message: HANDOFF_FAILED };
    }
    return { kind: "queued", reference: body.reference };
  } catch {
    return { kind: "failed", message: HANDOFF_FAILED };
  }
}
