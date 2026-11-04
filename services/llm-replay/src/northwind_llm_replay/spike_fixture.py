"""The FOUND-2 spike's fixed prompt (services/llm-replay/SPIKE.md).

The committed cassettes in cassettes/spike/ were recorded with exactly these strings and
`max_output_tokens=SPIKE_MAX_OUTPUT_TOKENS`. Changing anything here is a deliberate cassette miss.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are a fixture for the Northwind Shop record/replay spike. "
    "Answer with a single word and nothing else."
)
PROMPT = "Reply with the word: ready"
SPIKE_MAX_OUTPUT_TOKENS = 1024
