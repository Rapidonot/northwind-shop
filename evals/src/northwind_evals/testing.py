"""Test doubles for the eval harness. Nothing outside tests imports this module.

`MessagesAnswerer` is the single-shot answerer that row #13b retired from the CLI: one Messages
call, no tools, against a scripted model. The CLI and harness tests use it to drive scoring without
a Claude Code CLI; what the evals score is `answerers.AssistantAnswerer`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from northwind_evals.answerers import Answer, escaped, render_sources, response_text
from northwind_evals.contexts import ContextChunk
from northwind_evals.items import EvalItem

ANSWER_MAX_TOKENS = 1024
SYSTEM_PROMPT = "You answer from the sources. The shopper is in the {region} region."


class MessagesAnswerer:
    def __init__(self, client: Any, model: str, max_tokens: int = ANSWER_MAX_TOKENS) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    @property
    def id(self) -> str:
        return f"messages-test-double:{self._model}"

    def answer(self, item: EvalItem, context: Sequence[ContextChunk]) -> Answer:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT.format(region=item.region),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{render_sources(context)}\n\nShopper question: {escaped(item.query)}"
                    ),
                }
            ],
        )
        return Answer(response_text(response))
