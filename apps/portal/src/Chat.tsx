import { type FormEvent, useState } from "react";
import { AiDisclosure } from "./AiDisclosure";
import { type ChatEvent, type ErrorCode, sendMessage } from "./chatStream";

const ERROR_TEXT: Record<ErrorCode, string> = {
  retrieval_failed: "Search isn't available right now, so I can't answer that.",
  model_unavailable: "The assistant isn't available right now. Please try again.",
  replay_miss: "This question has no recorded answer yet.",
  internal: "Something went wrong. Please try again.",
};

function errorText(code: ErrorCode): string {
  return Object.hasOwn(ERROR_TEXT, code) ? ERROR_TEXT[code] : ERROR_TEXT.internal;
}

type Turn = { answer: string; sources: string[]; notice: string | null };

const EMPTY: Turn = { answer: "", sources: [], notice: null };

function apply(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case "token":
      return { ...turn, answer: turn.answer + event.text };
    case "reset":
      return { ...turn, answer: "" };
    case "citations":
      return { ...turn, sources: event.sourceIds };
    case "error":
      return { ...turn, notice: errorText(event.code) };
    case "refused":
      return { ...turn, notice: event.message };
    case "done":
      return turn;
  }
}

export const PRICE_NOTE = "Ask me for a price or stock check: I look them up live.";

/** `onTurnEnd` (row #16): called when a turn has ended, however it ended, so the cart can be read
 * again. */
export function Chat({ onTurnEnd }: { onTurnEnd?: () => void } = {}) {
  const [message, setMessage] = useState("");
  const [turn, setTurn] = useState<Turn>(EMPTY);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setTurn(EMPTY);
    try {
      await sendMessage(message, (chatEvent) => setTurn((current) => apply(current, chatEvent)));
    } catch {
      setTurn((current) => apply(current, { type: "error", code: "internal" }));
    } finally {
      setBusy(false);
      onTurnEnd?.();
    }
  }

  return (
    <section aria-label="Chat">
      <AiDisclosure />
      {/* Row #12 (RAG-09): the assistant looks up price and stock live, through its catalog tools. */}
      <p>{PRICE_NOTE}</p>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          Your question{" "}
          <input value={message} onChange={(event) => setMessage(event.target.value)} />
        </label>{" "}
        <button type="submit" disabled={busy || message.trim() === ""}>
          Send
        </button>
      </form>
      <p data-testid="answer" aria-live="polite">
        {turn.answer}
      </p>
      {turn.notice && <p role="alert">{turn.notice}</p>}
      {turn.sources.length > 0 && (
        <ul aria-label="Sources">
          {turn.sources.map((sourceId) => (
            <li key={sourceId}>{sourceId}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
