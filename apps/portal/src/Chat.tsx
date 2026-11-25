import { type FormEvent, type ReactNode, useState } from "react";
import { AiDisclosure } from "./AiDisclosure";
import { type ChatEvent, type ErrorCode, type SourceCard, sendMessage } from "./chatStream";

const ERROR_TEXT: Record<ErrorCode, string> = {
  retrieval_failed: "Search isn't available right now, so I can't answer that.",
  model_unavailable: "The assistant isn't available right now. Please try again.",
  replay_miss: "This question has no recorded answer yet.",
  internal: "Something went wrong. Please try again.",
};

function errorText(code: ErrorCode): string {
  return Object.hasOwn(ERROR_TEXT, code) ? ERROR_TEXT[code] : ERROR_TEXT.internal;
}

type Turn = { answer: string; sources: string[]; cards: SourceCard[]; notice: string | null };

const EMPTY: Turn = { answer: "", sources: [], cards: [], notice: null };

// Row #17 (SA-02): "every product claim shows a source link". The prompt and the output check put
// an [ID] marker on every product claim; each ID the gateway cited becomes a link to its entry in
// the Sources list. An ID it did not cite stays plain text. Links change no text.
const MARKER = /\[([^[\]]+)\]/g;
const PRODUCT_TYPES: ReadonlySet<string> = new Set(["product", "variant"]);
const TYPE_LABEL: Record<string, string> = {
  product: "Product",
  variant: "Product option",
  document: "Guide or policy",
  review: "Shopper review",
};

// A group's parts, as the server reads them (`output_check.citation_parts`, row #30, M1).
const citationParts = (group: string) =>
  group
    .split(/[,;]/)
    .map((id) => id.trim())
    .filter((id) => id !== "");

const anchor = (id: string) => `source-${id}`;

function linked(answer: string, cited: ReadonlySet<string>): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of answer.matchAll(MARKER)) {
    const start = match.index;
    const ids = citationParts(match[1] ?? "");
    if (!ids.some((id) => cited.has(id))) continue;
    out.push(answer.slice(last, start));
    if (ids.length === 1 && ids[0] === match[1]?.trim()) {
      out.push(
        <a key={start} href={`#${anchor(ids[0] ?? "")}`}>
          {match[0]}
        </a>,
      );
    } else {
      // "[P0011, R00002]": each cited ID in the group is its own link; separators and spaces are
      // kept exactly as streamed, so links change no text.
      out.push("[");
      (match[1] ?? "").split(/(\s*[,;]\s*)/).forEach((part, n) => {
        out.push(
          n % 2 === 0 && cited.has(part.trim()) ? (
            <a key={`${start}-${n}`} href={`#${anchor(part.trim())}`}>
              {part}
            </a>
          ) : (
            part
          ),
        );
      });
      out.push("]");
    }
    last = start + match[0].length;
  }
  out.push(answer.slice(last));
  return out;
}

function ProductCard({ card }: { card: SourceCard }) {
  const detail = [card.brand, card.department].filter(Boolean).join(" · ");
  return (
    <article aria-label={card.title}>
      <strong>{card.title}</strong>
      {detail && <span> {detail}</span>} <a href={`#${anchor(card.id)}`}>Source {card.id}</a>
    </article>
  );
}

function sourceLabel(card: SourceCard | undefined): string | null {
  if (!card) return null;
  const type = Object.hasOwn(TYPE_LABEL, card.type) ? TYPE_LABEL[card.type] : "Source";
  return card.title && card.type !== "review" ? `${type}: ${card.title}` : (type ?? null);
}

function apply(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case "token":
      return { ...turn, answer: turn.answer + event.text };
    case "reset":
      return { ...turn, answer: "" };
    case "citations":
      return { ...turn, sources: event.sourceIds, cards: event.sources };
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

  // Only a cited source gets a card: an entry for an ID outside `source_ids` has no anchor to link.
  const cited = new Set(turn.sources);
  const cards = new Map(
    turn.cards.filter((card) => cited.has(card.id)).map((card) => [card.id, card]),
  );
  const products = [...cards.values()].filter(
    (card) => PRODUCT_TYPES.has(card.type) && card.title,
  );

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
        {linked(turn.answer, cited)}
      </p>
      {turn.notice && <p role="alert">{turn.notice}</p>}
      {products.length > 0 && (
        <div aria-label="Products">
          {products.map((card) => (
            <ProductCard key={card.id} card={card} />
          ))}
        </div>
      )}
      {turn.sources.length > 0 && (
        <ul aria-label="Sources">
          {turn.sources.map((sourceId) => {
            const label = sourceLabel(cards.get(sourceId));
            return (
              <li key={sourceId} id={anchor(sourceId)}>
                <span>{sourceId}</span>
                {label && <span> {label}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
