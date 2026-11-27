import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { AiDisclosure } from "./AiDisclosure";
import { mightBeCardData } from "./cardGuard";
import {
  type ChatEvent,
  type ErrorCode,
  type HandoffTurn,
  requestHandoff,
  type SourceCard,
  sendMessage,
} from "./chatStream";
import { HandoffNotice, type HandoffState, type HandoffStatus, NO_STAFF_YET } from "./HandoffNotice";

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
    case "handoff":
    case "done":
      return turn;
  }
}

export const PRICE_NOTE = "Ask me for a price or stock check: I look them up live.";

// Row #23 (SA-06): the turns a handoff sends, as the shopper saw them; never shown again.
export const MAX_HANDOFF_TURNS = 10;
// Each at most what the gateway takes (`MAX_TURN_CHARS`), or a long answer would make every later
// handoff a refusal no retry could fix (test-writer finding, row #23).
export const MAX_HANDOFF_TURN_CHARS = 2000;

/** `onTurnEnd` (row #16): called when a turn has ended, however it ended, so the cart can be read
 * again. `handoffStatus` (row #23): tells the chat when a person has taken over; by default no one
 * ever does, because no staff side exists yet. `suggestions` (SA-01 PR 2): questions offered as
 * buttons while the chat is empty; a click sends the question exactly as written, as the first
 * message of a fresh conversation. "New conversation" empties the chat again. */
export function Chat({
  onTurnEnd,
  handoffStatus = NO_STAFF_YET,
  suggestions = [],
}: { onTurnEnd?: () => void; handoffStatus?: HandoffStatus; suggestions?: readonly string[] } = {}) {
  const [message, setMessage] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [turn, setTurn] = useState<Turn>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [handoff, setHandoff] = useState<HandoffState>({ kind: "idle" });
  const history = useRef<HandoffTurn[]>([]);
  const lastTrigger = useRef<"button" | "message">("button");
  const unwatch = useRef<() => void>(() => {});

  useEffect(() => () => unwatch.current(), []);

  function remember(...turns: HandoffTurn[]) {
    const kept = turns
      .filter((t) => t.text !== "")
      .map((t) => ({ ...t, text: t.text.slice(0, MAX_HANDOFF_TURN_CHARS) }));
    history.current = [...history.current, ...kept].slice(-MAX_HANDOFF_TURNS);
  }

  async function startHandoff(trigger: "button" | "message") {
    lastTrigger.current = trigger;
    setHandoff({ kind: "requesting" });
    const sent = history.current;
    const result = await requestHandoff(sent, trigger);
    if (result.kind === "failed") {
      // Security review L1: drop the refused turns. Only those (re-review N4): a turn remembered
      // while the request was in flight was never part of it.
      if (result.turnsRefused) history.current = history.current.filter((t) => !sent.includes(t));
      setHandoff({ kind: "failed", message: result.message });
      return;
    }
    setHandoff({ kind: "queued", reference: result.reference });
    unwatch.current();
    unwatch.current = handoffStatus.watch(result.reference, () =>
      setHandoff({ kind: "joined", reference: result.reference }),
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await ask(message);
  }

  /** SA-01 PR 2: a fresh conversation, as the recordings were made. Nothing of the last one is kept:
   * the shown turn, the question box and the turns a handoff would send. The gateway keeps no
   * conversation (`/api/chat` takes one message), so this is all a new conversation needs. */
  function newConversation() {
    unwatch.current();
    unwatch.current = () => {};
    history.current = [];
    setHandoff({ kind: "idle" });
    setMessage("");
    setAsked(null);
    setTurn(EMPTY);
  }

  async function suggest(question: string) {
    newConversation();
    await ask(question);
  }

  async function ask(question: string) {
    setBusy(true);
    setTurn(EMPTY);
    // Shown back only if it can't be card data; a refused message is dropped from the screen too.
    setAsked(mightBeCardData(question) ? null : question);
    let shown = "";
    let refused = false;
    let handedOff = false;
    try {
      await sendMessage(question, (chatEvent) => {
        if (chatEvent.type === "token") shown += chatEvent.text;
        else if (chatEvent.type === "reset") shown = "";
        else if (chatEvent.type === "refused") refused = true;
        else if (chatEvent.type === "handoff") handedOff = true;
        setTurn((current) => apply(current, chatEvent));
      });
    } catch {
      setTurn((current) => apply(current, { type: "error", code: "internal" }));
    } finally {
      // A refused message was never sent, so it is never part of a handoff either (hard rule 1).
      if (refused) setAsked(null);
      else remember({ role: "shopper", text: question }, { role: "assistant", text: shown });
      setBusy(false);
      onTurnEnd?.();
    }
    if (handedOff) await startHandoff("message");
  }

  const handoffOpen =
    handoff.kind === "requesting" || handoff.kind === "queued" || handoff.kind === "joined";

  // Only a cited source gets a card: an entry for an ID outside `source_ids` has no anchor to link.
  const cited = new Set(turn.sources);
  const cards = new Map(
    turn.cards.filter((card) => cited.has(card.id)).map((card) => [card.id, card]),
  );
  const products = [...cards.values()].filter(
    (card) => PRODUCT_TYPES.has(card.type) && card.title,
  );

  // SA-01 PR 2 (look round 1): the suggestions show only while the chat is empty. "New
  // conversation" empties it again, so a recorded question is always a fresh conversation's first.
  const empty = asked === null && turn.answer === "" && turn.notice === null && handoff.kind === "idle" && !busy;
  const answered = turn.answer !== "" || products.length > 0 || turn.sources.length > 0;

  return (
    <section aria-label="Chat" className="chat">
      <div className="chat-intro">
        <AiDisclosure />
        {/* Row #12 (RAG-09): the assistant looks up price and stock live, through its catalog tools. */}
        <p className="chat-note">{PRICE_NOTE}</p>
      </div>
      <div className="chat-messages">
        {asked !== null && (
          <p className="chat-asked" aria-label="Your message">
            {asked}
          </p>
        )}
        <div className={answered ? "chat-answer" : "chat-answer is-empty"}>
          <p data-testid="answer" aria-live="polite">
            {linked(turn.answer, cited)}
          </p>
          {products.length > 0 && (
            <div aria-label="Products" className="chat-products">
              {products.map((card) => (
                <ProductCard key={card.id} card={card} />
              ))}
            </div>
          )}
          {turn.sources.length > 0 && (
            <ul aria-label="Sources" className="chat-sources">
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
        </div>
        {turn.notice && (
          <p role="alert" className="chat-notice">
            {turn.notice}
          </p>
        )}
        <div className="chat-handoff">
          <HandoffNotice state={handoff} onRetry={() => void startHandoff(lastTrigger.current)} />
        </div>
      </div>
      <div className="chat-bottom">
        {empty && suggestions.length > 0 && (
          <div className="chat-suggestions" role="group" aria-label="Suggested questions">
            {suggestions.map((question) => (
              <button
                key={question}
                type="button"
                className="btn btn-outline suggestion"
                disabled={busy || handoffOpen}
                onClick={() => void suggest(question)}
              >
                {question}
              </button>
            ))}
          </div>
        )}
        {!empty && suggestions.length > 0 && (
          <button type="button" className="btn btn-outline chat-new" disabled={busy} onClick={newConversation}>
            New conversation
          </button>
        )}
        <form className="chat-form" onSubmit={(event) => void submit(event)}>
          <label className="chat-field">
            <span className="visually-hidden">Your question</span>
            <input
              value={message}
              placeholder="Ask about shells, sizing or care…"
              onChange={(event) => setMessage(event.target.value)}
            />
          </label>
          <button type="submit" className="btn btn-short chat-send" disabled={busy || message.trim() === ""}>
            Send
          </button>
        </form>
        <button
          type="button"
          className="btn btn-outline chat-talk"
          disabled={handoffOpen}
          onClick={() => void startHandoff("button")}
        >
          Talk to a person
        </button>
      </div>
    </section>
  );
}
