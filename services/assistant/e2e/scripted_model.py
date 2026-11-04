"""A canned model for `make e2e-scripted` (SA-02): everything real but the model.

Each smoke question takes two requests: the tool call, then the answer. The answer cites P0011,
which real retrieval returns for this search, so the citation check still means something.
"""

from __future__ import annotations

import argparse
import threading

from northwind_assistant.tools import ALLOWED_TOOL
from northwind_llm_replay.scripted import ScriptedLLM

QUESTIONS_SERVED = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with ScriptedLLM(port=args.port) as llm:
        for _ in range(QUESTIONS_SERVED):
            llm.reply_tool_use(
                ALLOWED_TOOL, {"query": "waterproof rating of the Brindlemoor Cairn Storm Jacket"}
            )
            llm.reply_stream("Canned answer ", "for the browser path ", "[P0011].")
        threading.Event().wait()


if __name__ == "__main__":
    main()
