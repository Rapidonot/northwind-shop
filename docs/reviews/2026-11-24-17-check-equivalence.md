# Row #17: the output check after the promote gives the same shown text on every recording

- **Why this file exists:** the user promoted the window-4 candidate with check `74c8ba9d` (`7bcc221`'s
  `output_check.py`). After the promote, the scoped security review's F1/F2 and its re-review's lows 1–2 changed the
  check (`afc49e2`, `373f22c`), and the hash is now `a1e0e605`.
- **What `windows-3-4.md` said:** `prompt.py` and `output_check.py` would be frozen from window 4 on. `prompt.py`
  is. `output_check.py` changed for these findings only.
- **The gate:** it only notes a changed check part (`gate.py:172-173`).
- **What the script checks:** every recorded answer call with text, in `eval-answers`, `assistant-smoke`,
  `assistant-cart` and `assistant-redteam`. It feeds the call's own text deltas, with the sources from its own
  prompt, through the old check and the new one. It then compares the shown text and the counts.
- **Result:** at `afc49e2`, and again at `373f22c`: `compared 357 differ 0`. So no judge verdict, cassette or score
  depends on the change.

## How to run it ($0, no LLM)

Save the script block below as `equiv.py`, and run it from the repository root: it reads `cassettes/` by relative
path.

    git show 7bcc221:services/assistant/src/northwind_assistant/output_check.py > "$TMPDIR/oc_74c8ba9d.py"
    uv run --offline python equiv.py "$TMPDIR/oc_74c8ba9d.py"

## Script

```python
"""Old (argv[1]) and current output check on every recorded answer call with text."""

import glob
import importlib.util
import json
import re
import sys

from northwind_assistant import output_check as new
from northwind_assistant.tools import parse_sources

spec = importlib.util.spec_from_file_location("oc_old", sys.argv[1])
old = importlib.util.module_from_spec(spec)
sys.modules["oc_old"] = old
spec.loader.exec_module(old)
DELTA = re.compile(r'"type":"text_delta","text":"((?:[^"\\]|\\.)*)"')
n = diff = 0
for suite in ("eval-answers", "assistant-smoke", "assistant-cart", "assistant-redteam"):
    for f in sorted(glob.glob(f"cassettes/{suite}/*.json")):
        c = json.load(open(f))
        if c["test_id"] == "eval-judge":
            continue
        deltas = [json.loads(f'"{d}"') for d in DELTA.findall(c["body"])]
        if not deltas:
            continue
        first = c["match"]["body"]["messages"][0]["content"]
        if isinstance(first, str):
            prompt = first
        else:
            prompt = "".join(b.get("text", "") for b in first if isinstance(b, dict))
        sources = parse_sources(prompt)
        outs = []
        for mod in (old, new):
            check = mod.OutputCheck({s.source_id for s in sources}, sources)
            for d in deltas:
                check.feed(d)
            check.finish()
            outs.append((check.shown, dict(check.counts)))
        n += 1
        if outs[0] != outs[1]:
            diff += 1
            print("DIFF", suite, c["test_id"], "\n  old:", outs[0], "\n  new:", outs[1])
print("compared", n, "differ", diff)
```
