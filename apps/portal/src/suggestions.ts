// SA-01 PR 2: the assistant panel's suggested questions. They are the recorded smoke questions,
// imported from the same file the recordings were made from, so their text cannot drift from the
// cassettes. Each replays only as the first message of a fresh conversation, so clicking one starts
// a new conversation (the user's change to Amendment 3).
import questions from "../e2e/questions.json" with { type: "json" };

export const SUGGESTIONS: readonly string[] = [questions.product, questions.care, questions.false_policy];
