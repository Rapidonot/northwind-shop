// POST /api/chat, read as a stream. EventSource is GET only, so this reads the body with fetch.

export type ErrorCode =
  | "retrieval_failed"
  | "model_unavailable"
  | "replay_miss"
  | "turn_limit"
  | "internal";

export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "reset" }
  | { type: "citations"; sourceIds: string[] }
  | { type: "error"; code: ErrorCode }
  | { type: "refused"; message: string }
  | { type: "done" };

const GENERIC_REFUSAL = "That message could not be accepted.";

function toEvent(name: string, data: string): ChatEvent | null {
  const body = JSON.parse(data) as Record<string, unknown>;
  switch (name) {
    case "token":
      return { type: "token", text: body.text as string };
    case "reset":
      return { type: "reset" };
    case "citations":
      return { type: "citations", sourceIds: body.source_ids as string[] };
    case "error":
      return { type: "error", code: body.code as ErrorCode };
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
