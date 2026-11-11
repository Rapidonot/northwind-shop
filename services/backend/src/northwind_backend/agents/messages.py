"""What a backend agent's model reads when a tool call fails (row #21; #20 cassette read C2).

The port's own messages are written for callers that hold a key; a model holds none and can't act on
"retry with the same key". So every tool's doubtful outcomes get fixed, tool-specific text here:

- a read that timed out has already been retried: report it and don't call it again;
- a write whose effect may have happened (`timeout`, `unexpected_error`, `audit_incomplete`) is
  never called again (`recovery.WRITE_POLICY`): the model is told so, and `isRetryable` is false.

Every other error keeps the port's envelope exactly, so this changes model input only where it
must. Text is fixed per tool and code: nothing an argument or exception carried reaches it.
"""

from __future__ import annotations

from typing import Any

from northwind_backend.errors import ToolError

DOUBTFUL = frozenset({"timeout", "unexpected_error", "audit_incomplete"})

# Read tools: what the lookup is called in a sentence.
READS: dict[str, str] = {
    "get_fraud_assessments": "fraud lookup",
    "get_payment_status": "payment status lookup",
    "validate_order": "order check",
}
# Write tools: what may have happened.
WRITES: dict[str, str] = {
    "use_address_on_file": "address change",
    "assess_fraud": "fraud assessment",
    "authorize_payment": "payment authorization",
    "capture_payment": "payment capture",
    "confirm_order": "order confirmation",
    "escalate_order": "escalation",
}


def model_envelope(tool: str, error: ToolError) -> dict[str, Any]:
    envelope = error.to_envelope()
    if tool in READS and error.code == "timeout":
        envelope["message"] = (
            f"The {READS[tool]} did not finish in time, after retries. Report that this lookup "
            "failed; do not call it again."
        )
    elif tool in WRITES and error.code in DOUBTFUL:
        envelope["isRetryable"] = False
        envelope["message"] = (
            f"The {WRITES[tool]} did not finish and may have taken effect. Do not call it again; "
            "the order is escalated for a check."
        )
    return envelope
