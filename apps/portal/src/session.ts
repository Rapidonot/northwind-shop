// SA-01 PR 1 (the browser run at 61ea21e): the gateway mints a session for every request on a
// session path that arrives without its cookie, and the browser keeps whichever cookie came last. So
// two such requests in flight at once split the shopper's session: React StrictMode's double mount
// in dev sends the cart read twice, and a chat message or a "Talk to a person" click can leave
// before the first response. The first session-path request of a page therefore goes alone, and
// every other one waits for its response (whatever it was), so the browser holds one cookie before a
// second request leaves. After that, requests go out at once.

let first: Promise<void> | null = null;

export function sessionFetch(input: string, init?: RequestInit): Promise<Response> {
  if (first === null) {
    const request = fetch(input, init);
    first = request.then(
      () => undefined,
      () => undefined,
    );
    return request;
  }
  return first.then(() => fetch(input, init));
}

/** Tests only: a fresh page. */
export function resetSessionOrder(): void {
  first = null;
}
