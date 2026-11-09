// NFR-08, hard rule 11: the shopper always knows the assistant is an AI. No props and no state, so
// nothing a turn does (tokens, errors, refusals, a new question) can change or clear it.

export const AI_DISCLOSURE = "You're chatting with an AI assistant, not a person.";

export function AiDisclosure() {
  return (
    <p role="note" aria-label="AI disclosure">
      {AI_DISCLOSURE}
    </p>
  );
}
