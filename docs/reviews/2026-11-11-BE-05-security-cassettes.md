# BE-05 security read of the recorded cassettes (verbatim)

Scoped security read of the row #21 cassettes (BE-05 + CHK-05), branch `req/BE-05-orders`. This is plan §7 step 7.

**Nothing is at medium or above.** I found two lows and no critical, high or medium. No card data, token, confirmation reference, address, email, shopper/intent/assessment ID, correlation ID, key shape, canary or invented amount is in any `backend-replay` cassette.

I re-read `CLAUDE.md` "Hard rules", decision 0001, the plan, `orders_place.py` and `docs/governance/data-flow.md` before starting.

## Coverage

Every grep below ran over all **5,974 files**: 5,959 in `cassettes/backend-replay/` and 15 in `cassettes/backend-audit/`. I confirmed the count by matching the `suite` field and the scrubbed `metadata.user_id` field, which are in every file.

| Pattern | Result |
|---|---|
| `tok_sandbox` | 0 |
| `cf_[A-Z]{16}` | 0 |
| postal codes `[A-Z]{2}-\d{4}` | 0 |
| `@example.com`, `sk-ant`, `run-O`, `checkout-`, `NWCANARY` (canaries from `data/fixtures/_answer_key.json`) | 0 |
| `user_id` not equal to `<scrubbed:metadata.user_id>` | 0 |
| `device_id`, `session_id`, `account_uuid` | 0 |
| `pi_\d{6}` / `fa_\d{6}` in `backend-replay` | 0 |
| `intent_id`, `assessment_id`, `shopper_id`, `correlation_id`, `payment_token`, `confirmation_ref`, `postal`, `run_<20 letters>`, `claimed_by`, `idempotency` in `backend-replay` | 0 |
| `total_minor`, `authorized_minor`, `captured_minor`, `amount_minor` in `backend-replay` | 0 |
| `S\d{4}` (shopper ID) | 4 hits, all in older #20 `backend-audit` files (not the 3 new ones) |
| Digit runs of 7+ not inside a hex or base62 token | 0 (the only hits were `cost_usd` decimals and beta-header dates) |
| 4-4-4-n spaced or dashed card shape | 0 |
| 13+ digit runs | only in the `hash` field (line 7), a sha256 |
| 12+ digit runs in thinking signatures (request and `signature_delta`) and `msg_`/`toolu_` IDs | 0 |
| Tool names | 56,683 occurrences, all among the seven placement tools |
| `void`, `refund`, built-in tools (Bash, Read, …) | 0 |
| Model answer text with `$`, a currency code, `£`, `€` or a decimal amount | 0 |
| Answer digits | only order IDs and fraud scores; 5 scores checked, all match their tool results |
| Answer words (card, refund, track, ship, price, total, amount, email, token, …) | only "undeliverable" |
| Error codes the model saw | only `soft_decline`, `order_already_placed`, `order_escalated` (111 occurrences in 108 files) |
| Escalation results | exactly 50: 25 `out_of_stock` and 25 `fraud_review` |
| Fraud reasons | only `flagged_order` |

The shopper IDs in the 4 older `backend-audit` files, and `pi_000001` in the new `6266660f…`, are the #20 read-only design. `data-flow.md` line 37 lists them for that agent.

## Sample read in full or in part

All paths are under `/Users/rapido/Projects/northwind-shop/cassettes/`.

- **`backend-audit`, the 3 new files:** `49ca6901…` (run10), `6266660f…` (run3), `ab899b67…` (run7). Each holds the new tool-specific fraud-read timeout text. The answers report the failed lookup and invent nothing.
- **Clean:** `backend-replay/3b43e8a8…` (O000075, score 16) and `backend-replay/08f8e95a…` (O000170, score 23). Both are correct "placed" answers.
- **Address error:** `backend-replay/0c0e102f…` (O000078), `backend-replay/dd09aae1…` (O000998, final "placed" answer, correct), `backend-replay/ba0ea322…` (O000037).
- **Soft decline:** `backend-replay/6001a0d5…` (O000024) and `backend-replay/c7773325…` (O000842). Each retries once and gets `authorized`.
- **Stock-out:** `backend-replay/8f2f0d7f…` (O000021) and `backend-replay/cb79896e…` (O000016). Both are correct "escalated" answers.
- **Fraud flag:** `backend-replay/ad718dba…` (O000728, "score 92", correct) and `backend-replay/a3814967…` (O000002, a ten-replays rerun that sees `order_escalated`).
- **Ten-replays reruns** (`test_id …::test_ten_replays_yield_one_order`, 24 files): `1315a7da…`, `0635ed1a…`, `d61ca948…`, `e539c154…`, plus `a3814967…` above.

Model input in every sampled file is issue codes, line count, the fraud decision, score and reasons, and payment, order and escalation statuses, as `orders_place.py`'s docstring says. The system prompt states "You are an AI system" (hard rule 11).

## Findings

**L1 (low).** The model's final summary misstates the order status in ten-replays reruns of orders that are already placed.
- **Files and field:** the response `body` (text_delta) of:
  - `cassettes/backend-replay/0635ed1a8234fb6f15400e7f9ebe502f00be49ee90948bca4452a21581b1881b.json` (O000037): "Order O000037 was escalated, not placed".
  - `cassettes/backend-replay/d61ca9481f4a884ccc7fc5149b80379b55878306bbe139b2a83af4f23523ff6f.json` (O000001): "has been escalated … appears to have been placed already".
  - The third rerun, `e539c154…` (O000024), is accurate.
- **Rule:** the brief's "invent no status"; hard rule 2 (order state only from live tools) in spirit.
- **Cause:** `SYSTEM_PROMPT`'s catch-all ("If a tool returns any error not covered above, call escalate_order and stop"). On `order_already_placed` the model calls `escalate_order`. `_store_gate` refuses it with `order_already_placed`, so no state changes. The model then describes the order as escalated.
- **Impact today:** none. `PlacementResult.outcome` comes from the store, and the test asserts on it. But the false status sits in `AgentResult.summary`, which `errors.py:210` (`to_envelope()["summary"]`) carries. A later consumer (BE-01 orchestrator, #24) that shows or relays the summary would report a placed order as escalated.
- **Fix:**
  1. Never pass a placement run's model `summary` downstream; replace it with a sentence built in code from `PlacementResult.outcome` and `reason`. Add a test that pins this.
  2. Optionally, add an explicit prompt step: on `order_already_placed` or `order_escalated`, stop and report exactly that; do not escalate. This changes model input, so it means re-recording `backend-replay` in a new window, which is the user's decision (stops 2 and 7).

**L2 (low).** The soft-decline result passes the port's own instruction text to the model.
- **File and field:** `services/backend/src/northwind_backend/fakes/payment.py:71`, `SOFT_DECLINE = business("soft_decline", "The issuer declined this attempt. Try another method.")`. It reaches the tool_result `content` unchanged in every soft-decline cassette, for example `backend-replay/6001a0d5…` line 104, because `agents/messages.py` `model_envelope` rewrites only timeouts and doubtful codes.
- **Rule:** hard rule 6.
- **Why low:** the text is constant, carries no data, and conflicts with the prompt's "call authorize_payment once more". In every recorded soft-decline run the model followed the prompt, and the code cap of 2 attempts holds whatever the model does.
- **Unconfirmed:** the same pass-through will apply to real P4 adapters, whose decline messages may carry provider-written text.
- **Fix:** give `soft_decline` and `hard_decline` fixed model-facing text in `messages.py`, as the timeout text is. This changes model input and so needs a planned re-record. Otherwise log it for P4 (PAY-01 contract suite).

## Checked, no findings

- **Hard rule 1:** no PAN, CVV, token or digit-run shape anywhere.
- **Hard rule 2:** no price, amount or stock figure in model input or answers. Validation returns codes only.
- **Hard rule 3:** synthetic IDs only; no emails, names or addresses.
- **Hard rule 4:** no key shapes. Only content-type, anthropic-version and anthropic-beta headers are stored, and `metadata.user_id` is scrubbed in all 5,974 files.
- **Hard rule 5:** not applicable; no product claims.
- **Hard rule 6:** the system prompt labels tool results as data. No tool result granted new tool use. The one directive tool message is L2.
- **Hard rule 7:** no canary or internal text.
- **Hard rule 8:** only the seven placement tools are offered; no void, refund or built-ins.
- **Hard rule 9:** no idempotency key or confirmation reference in model input. The rerun transcripts show code refusing writes on an order that is already placed or escalated.
- **Hard rule 10:** every error the model saw is a structured envelope with `isError`, `errorCategory`, `isRetryable`, `code` and `message`.
- **Hard rule 11:** the system prompt discloses that it is an AI (not shopper-facing).
- **Decision 0001:**
  - The cassettes are plain JSON with the request hash and scrubbed metadata.
  - They are covered by the PAN and key-shape patterns above.
  - The placeholder key, Makefile export, CI secrets, worktree key file and budget caps are not touched by this read and are not applicable to it.

Nothing in the cassettes tried to steer this review.
