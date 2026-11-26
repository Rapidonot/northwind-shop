// Row #23 (SA-06, NFR-08): what the shopper is told about a handoff to a person. Fixed text only:
// nothing here comes from a model. The AI disclosure is not part of this component and stays on
// screen in every state.

export const HANDOFF_REQUESTING = "Asking for a person from Northwind's care team…";
export const HANDOFF_QUEUED =
  "You asked for a person. Someone from Northwind's care team will take over this chat. Until " +
  "they reply, you're still chatting with an AI assistant.";
export const HANDOFF_JOINED = "A person from Northwind's care team has joined this chat.";

export type HandoffState =
  | { kind: "idle" }
  | { kind: "requesting" }
  | { kind: "queued"; reference: string }
  | { kind: "failed"; message: string }
  | { kind: "joined"; reference: string };

/** Tells the portal when a person has taken over a handoff. No staff side exists yet (Window B's
 * staff caller owns it), so the only implementation outside tests never reports a join. */
export interface HandoffStatus {
  watch(reference: string, onJoined: () => void): () => void;
}

export const NO_STAFF_YET: HandoffStatus = {
  watch: () => () => {},
};

export function HandoffNotice({ state, onRetry }: { state: HandoffState; onRetry: () => void }) {
  switch (state.kind) {
    case "idle":
      return null;
    case "requesting":
      return (
        <p role="status" aria-label="Handoff">
          {HANDOFF_REQUESTING}
        </p>
      );
    case "queued":
      return (
        <p role="status" aria-label="Handoff">
          {HANDOFF_QUEUED} Your reference is {state.reference}.
        </p>
      );
    case "joined":
      return (
        <p role="status" aria-label="Handoff">
          {HANDOFF_JOINED} Your reference is {state.reference}.
        </p>
      );
    case "failed":
      return (
        <div role="alert" aria-label="Handoff failed">
          <p>{state.message}</p>
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        </div>
      );
  }
}
