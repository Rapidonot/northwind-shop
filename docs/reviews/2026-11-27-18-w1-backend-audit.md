# Row #18, W1: `backend-audit` (2026-11-27)

- **Window:** opened by the user at `ede84a1` with `RUN_BUDGET=0.10`; the freeze is at `2ca787e`. The runner
  was at `origin/main` (`bcc135b`).
- **Hold guard before the window** (09:37:05Z): `live holds: 0 in database 'northwind'`, exit 0. The recording
  builds its catalog in process, so no `DATABASE_URL` catalog took part.
- **Recording:** `make record SUITE=backend-audit`, 09:40:30–09:40:44Z, exit 0, 12 passed.
- **Spend:** `make llm-spend` showed $32.704032 before and **$32.704032 after: $0.00**.
- **Proxy hit log:** `.llm-proxy-log/backend-audit.jsonl` did not exist before the window, so the mark was
  0 lines. After it: 24 lines, every one `hit`. There were no `recorded` lines and no refusals.
- **Cassettes:** 15 before and 15 after. 0 added and 0 removed: all 15 kept, because each was read. Under the
  hit-log rule, nothing is deleted.
- **Replay right after:** 12 passed, no miss.
- **Scan:** `make scan-cassettes` clean (0 findings). The key-fragment check from the runner clone is the
  user's.
- **Freeze diff** (`git diff 2ca787e..HEAD` on the frozen paths): empty. **Correction (2026-11-27, the user's request):** the `git diff` named here matched no path. zsh passed the 24 paths as one argument, so it proved nothing. No sha256 check was run at this window (the sha256 file was made later, at W11). The evidence is the recheck after the fact. Rechecked correctly (`xargs git diff --name-only 2ca787e <commit> -- < paths`): 0 frozen files changed at every commit from `2ca787e` to `71e77c5`, and the control `bcc135b..2ca787e` shows 6.
- **First-byte p50 and p95:** none, because no call went upstream.

**Finding: record mode re-records only misses.** In record mode the proxy serves the existing cassettes and goes
upstream only on a miss (`services/llm-replay/src/northwind_llm_replay/proxy.py`, module docstring). This
suite's model input has not changed since it was recorded, so every call hit. The runbook's estimates assume a
fresh call for every cassette. The same will happen in every suite whose input is unchanged, including W12
`eval-answers` and W12b/W12c `eval-answers-r2`/`-r3`, which already hold #32's recordings. Put to the user
(stop 7) before W2.
