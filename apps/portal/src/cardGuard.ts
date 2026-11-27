// SA-01 PR 1, security review F1 (hard rule 1): the shop's search text and route names go into a
// URL, and URLs are logged (the dev proxy, the gateway's access log). A value that might be a card
// number is never sent: twelve or more digits, with any non-word characters (spaces, `+`, `-`, `/`,
// `,` and so on) or underscores between them. At least as broad as the server's digit groups
// (`northwind_common.cards`, `[\W_]` between digits; re-review N2), which stays the authority; a
// search for a long number simply isn't sent.
const DIGIT_RUN = /\d(?:[\W_]*\d){11,}/;

export const CARD_REFUSED = "Card details can't be searched. Payment happens only in the secure payment form.";

export function mightBeCardData(text: string): boolean {
  return DIGIT_RUN.test(text);
}
