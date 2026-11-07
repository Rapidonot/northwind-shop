# One command per job, the same for humans, Claude and CI. Run `make` to list targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Free text for `eval-promote`, so make must never expand it: `$(VAR)` would run a `$(shell ...)` in
# the value, and so would exporting a command-line value. Pin the raw text; the recipe tests it in
# the shell instead. Keep this above every `$(shell ...)`: make 4.4 and later export variables to
# them, which expands these too (decision 0006).
override ACCEPT_REGRESSION := $(value ACCEPT_REGRESSION)
override BOOTSTRAP := $(value BOOTSTRAP)
override APPROVED_BY := $(value APPROVED_BY)
override CHECKOUT := $(value CHECKOUT)
override CHECK := $(value CHECK)
override CANDIDATE_SHA := $(value CANDIDATE_SHA)
export ACCEPT_REGRESSION BOOTSTRAP APPROVED_BY CHECKOUT CHECK CANDIDATE_SHA

# The same for `ship` and `finish`, which run in your terminal with your GitHub login (AUTO-1, AUTO-2).
override PR := $(value PR)
override SHA := $(value SHA)
override BODY := $(value BODY)
export PR SHA BODY

export COREPACK_ENABLE_DOWNLOAD_PROMPT := 0
PNPM ?= corepack pnpm
# --env-file only when the file exists: compose exits 1 on a missing one, and linked worktrees never
# have one (FOUND-2 review #12). Without it, compose uses the defaults in docker-compose.yml.
COMPOSE := docker compose $(if $(wildcard .env),--env-file .env )-f infra/docker-compose.yml

# Load .env (if present) so DATABASE_URL etc. reach the gateway and tests.
# The API key is the exception: it does not belong here, and any copy is blanked and never exported
# to recipes (hard rule 4, decision 0001). Only the runner reads it, from its own file (decision 0005).
# -include: Claude's sandbox denies reads of .env, which must not stop every target (CC-27).
ifneq (,$(wildcard .env))
-include .env
export
endif
override ANTHROPIC_API_KEY :=
unexport ANTHROPIC_API_KEY

# The compose file's own project name, pinned after the include above: the runner clone and every
# worktree must drive one stack and one data volume (decision 0005).
export COMPOSE_PROJECT_NAME := northwind

# The clean clone that holds the key (decision 0005). Inside it nothing uses the uv cache the sandbox
# can write, nothing re-resolves dependencies, and the shell's PYTHONPATH is dropped. What touches
# the key runs on .venv-runner, which holds only llm-replay and common: a workspace member's src is
# on sys.path through a .pth, so a sitecustomize.py in any member would otherwise load with the key.
# `model` runs beside the key file, so it gets its own small .venv-model. The clone has no full .venv.
RUNNER_DIR ?= $(HOME)/.northwind/runner
RUNNER_PYTHON := .venv-runner/bin/python -I
SCAN_PYTHON := uv run python
MODEL_PYTHON := uv run python
ifeq ($(CURDIR),$(realpath $(RUNNER_DIR)))
export UV_CACHE_DIR := $(HOME)/.northwind/uv-cache
export UV_FROZEN := 1
unexport PYTHONPATH
SCAN_PYTHON := $(RUNNER_PYTHON)
MODEL_PYTHON := .venv-model/bin/python -I
endif

.PHONY: help setup up down dev dev-gateway dev-portal data model ingest ingest-enriched enrich fmt lint test check \
	test-integration ci scan-cassettes llm-spend llm-proxy llm-runner runner-sync runner-check \
	ship finish review-diff record eval-live eval-rescore eval-gate eval-integrity eval-gate-stale-ok eval-promote \
	eval-snapshot eval-review-list eval-enrichment-report e2e e2e-scripted

# A stable cache, not fastembed's temp-dir default: Claude's sandbox has a different temp dir and
# cannot reach the model's download hosts, so `make -C ~/.northwind/runner model` fills this (CC-27).
export FASTEMBED_CACHE_PATH ?= $(HOME)/.cache/fastembed

# Linked git worktrees never get a .env: nothing there needs one (FOUND-2 addendum).
IN_LINKED_WORKTREE := $(shell test "$$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)" != "$$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" && echo 1)

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-18s %s\n", $$1, $$2}'

setup: ## Install Python + Node deps; create .env from env.example if missing (main checkout only)
	uv sync
	$(PNPM) install
ifeq ($(IN_LINKED_WORKTREE),1)
	@echo "linked worktree: no .env here; nothing needs one"
else
	@test -f .env || (cp env.example .env && echo "created .env from env.example")
endif

up: ## Start the local stack (Postgres + pgvector) and wait until healthy
	$(COMPOSE) up -d --wait

down: ## Stop the local stack (data volume is kept)
	$(COMPOSE) down

dev: ## Run gateway (:8000) and portal (:5173) together
	$(MAKE) -j2 dev-gateway dev-portal

dev-gateway:
	uv run uvicorn northwind_gateway.app:app --reload --port 8000

dev-portal:
	$(PNPM) --filter @northwind/portal dev

data: ## Generate synthetic fixtures into data/fixtures (deterministic; FOUND-1)
	uv run python -m northwind_data.cli --out data/fixtures

model: ## Pre-fetch the embedding model (own terminal, from the runner clone only; CC-27)
	$(MODEL_PYTHON) -c "from northwind_rag.embedding import FastEmbedEmbedder; FastEmbedEmbedder().embed(['warm'])"

ingest: ## Load data/fixtures into the RAG index (needs `make up`; idempotent; RAG-01)
	uv run python -m northwind_rag.cli --fixtures data/fixtures

ingest-enriched: ## Load data/fixtures with cached context into its own database (RAG-02; no LLM)
	uv run python -m northwind_rag.cli --fixtures data/fixtures --enriched

fmt: ## Auto-format and auto-fix Python
	uv run ruff format
	uv run ruff check --fix

lint: ## Lint + type-check Python and TypeScript
	uv run ruff check
	uv run ruff format --check
	uv run mypy services data evals
	$(PNPM) -r lint
	$(PNPM) -r typecheck

# Prints the filter that skips the slow ship and pre-push tests, only when it is sure that nothing they
# cover differs from the merge base with main. Anything it cannot tell prints nothing, so they run.
# GitHub CI's plain `uv run pytest` takes `addopts`, which does not name the marker, so it always runs
# them (AUTO-2, decision 0011). Through the environment so that the recipe stays one pytest call: a
# command-line `-m` still wins, so `test-integration` is not touched.
SHIP_TEST_PATHS := tools tests/ship .github Makefile
SHIP_FILTER := base=$$(git merge-base origin/main HEAD 2>/dev/null) && git diff --quiet "$$base" -- $(SHIP_TEST_PATHS) 2>/dev/null && untracked=$$(git ls-files --others --exclude-standard -- $(SHIP_TEST_PATHS) 2>/dev/null) && [[ -z "$$untracked" ]] && echo '-m "not integration and not e2e and not ship"'

# LLM_MODE is pinned on the recipe line, so neither the shell, the key file nor
# `make test LLM_MODE=…` can turn these targets into spending ones (decision 0001).
test: ## Unit tests (Python + portal), always in replay; ship tests only when what they cover changed
	@if [[ -n "$$($(SHIP_FILTER))" ]]; then echo "make test: ship tests skipped, nothing they cover differs from origin/main; GitHub CI runs them"; fi
	LLM_MODE=replay PYTEST_ADDOPTS="$$PYTEST_ADDOPTS $$($(SHIP_FILTER))" uv run pytest
	$(PNPM) -r test

# For during a row, and never evidence: only `make ci` is (AUTO-2, decision 0011).
check: lint ## Lint, then only the tests for changed paths, always in replay
	@plan=$$(uv run python tools/changed_tests.py) || exit 2; \
	echo "make check: $${plan:-no tests for these paths}"; \
	if [[ "$$plan" == all ]]; then $(MAKE) --no-print-directory test; exit $$?; fi; \
	py=$${plan//portal/}; \
	if [[ -n "$${py// /}" ]]; then LLM_MODE=replay uv run pytest $$py || exit $$?; fi; \
	if [[ "$$plan" == *portal* ]]; then $(PNPM) -r test; fi

test-integration: ## Integration tests against the local stack (run `make up` first), always in replay
	LLM_MODE=replay uv run pytest -m integration

# CASSETTES_DIR scans a worktree's cassettes from the runner clone, where the key-fragment check can
# run. The walk follows no symlink and findings carry no matched text (decision 0005).
scan-cassettes: ## Secret/PAN scan of cassettes, data/enrichment, ledger and proxy log (FOUND-2; CASSETTES_DIR=/abs/path)
	@[[ -z "$$CASSETTES_DIR" || "$$CASSETTES_DIR" =~ ^/[A-Za-z0-9._/-]+$$ ]] || (echo "usage: make scan-cassettes CASSETTES_DIR=/absolute/path" && exit 2)
	$(SCAN_PYTHON) -m northwind_llm_replay.cli scan $${CASSETTES_DIR:+--cassettes-dir "$$CASSETTES_DIR"}

llm-spend: ## LLM spend from the shared ledger: total, per suite/model, 20-call checkpoint (FOUND-2)
	$(SCAN_PYTHON) -m northwind_llm_replay.cli spend

llm-proxy: ## Dev LLM proxy on this worktree's port (LLM_MODE=replay by default; FOUND-2)
	uv run python -m northwind_llm_replay.cli serve

# THE ONLY TARGET THAT SENDS THE KEY ANYWHERE. Your own terminal, from the clean clone, always as
# `make -C ~/.northwind/runner llm-runner`: run from a worktree, this file is one the sandbox can
# edit. It opens one window; `make record` or `make eval-live` then spend through it (decision 0005).
# -I drops PYTHONPATH, user site and the working directory from the import path.
llm-runner: ## Open one spending window (SUITE=name RUN_BUDGET=usd; own terminal, runner clone only)
	@[[ "$$SUITE" =~ ^[a-z0-9_-]+$$ && "$$RUN_BUDGET" =~ ^[0-9]+(\.[0-9]+)?$$ ]] || (echo "usage: make -C $(RUNNER_DIR) llm-runner SUITE=<suite> RUN_BUDGET=<usd>" && exit 2)
	@[[ "$${RUNNER_MINUTES:-30}" =~ ^[0-9]+$$ && "$${RUNNER_IDLE_MINUTES:-10}" =~ ^[0-9]+$$ ]] || (echo "RUNNER_MINUTES and RUNNER_IDLE_MINUTES are whole minutes" && exit 2)
	@$(RUNNER_SYNC) --check >/dev/null || (echo ".venv-runner does not match uv.lock: run runner-sync" && exit 2)
	$(RUNNER_PYTHON) -m northwind_llm_replay.cli runner --suite "$$SUITE" --run-budget "$$RUN_BUDGET" --minutes "$${RUNNER_MINUTES:-30}" --idle-minutes "$${RUNNER_IDLE_MINUTES:-10}"

# Key-loaded code, what builds the environment, what `model` and `up` run beside the key, what `ship`
# runs with the user's GitHub login, and what `eval-promote` loads and trusts (row 5b).
RUNNER_SYNC_PATHS := services/llm-replay services/common services/rag data/src evals/src evals/fixtures/starter_set.jsonl evals/fixtures/known_failures.json evals/baselines infra Makefile tools/ship.sh tools/finish.sh tools/refused-paths.sh pyproject.toml uv.lock uv.toml .python-version .gitattributes .gitignore ':(glob)**/.gitignore' ':(glob)**/pyproject.toml' ':(glob)**/sitecustomize.py' ':(glob)**/usercustomize.py'
RUNNER_INCOMING := refs/runner/incoming
# --icase-pathspecs: on a case-insensitive clone `Services/common/x.py` lands on the reviewed path.
RUNNER_DIFF := git --no-pager --icase-pathspecs diff --text --no-ext-diff --no-textconv
RUNNER_SYNC := UV_PROJECT_ENVIRONMENT=.venv-runner uv sync --frozen --no-dev --package northwind-llm-replay
# Succeeds while a window is open: the runner must not have its code or environment moved under it.
RUNNER_BUSY := (exec 3<>/dev/tcp/127.0.0.1/$${LLM_RUNNER_PORT:-19990}) 2>/dev/null

# Shows what the key-holding code gains before it gains it, since HEAD, the last sync: every commit
# and changed file, then the full diff of the paths that load with the key or build the environment.
# The fetch lands on its own ref, so answering no leaves origin/main, and so the runner, working.
# `cat -v` because a commit subject is text the sandbox wrote.
runner-sync: ## Show what changed in the runner clone, ask, then fast-forward to origin/main
	@[[ "$(CURDIR)" == "$(realpath $(RUNNER_DIR))" ]] || (echo "only in the clone: make -C $(RUNNER_DIR) runner-sync" && exit 2)
	@! $(RUNNER_BUSY) || (echo "a spending window is open: close it first" && exit 2)
	git fetch --refmap= origin +refs/heads/main:$(RUNNER_INCOMING)
	@git --no-pager log --oneline HEAD..$(RUNNER_INCOMING) | cat -v
	@$(RUNNER_DIFF) --stat HEAD $(RUNNER_INCOMING) | cat -v
	@$(RUNNER_DIFF) HEAD $(RUNNER_INCOMING) -- $(RUNNER_SYNC_PATHS) | cat -v
	@/bin/bash tools/refused-paths.sh HEAD $(RUNNER_INCOMING)
	@read -r -p "Fast-forward the runner to origin/main? [y/N] " answer; [[ "$$answer" =~ ^[yY]$$ ]] || (echo "not merged" && exit 1)
	git merge --ff-only $(RUNNER_INCOMING)
	git update-ref refs/remotes/origin/main $(RUNNER_INCOMING)
	$(RUNNER_SYNC)
	UV_PROJECT_ENVIRONMENT=.venv-model uv sync --frozen --no-dev --package northwind-rag

# Your own terminal, from the clean clone, as `make -C ~/.northwind/runner ship PR=<n> SHA=<reviewed>`:
# from a worktree, this file and the script are ones the sandbox can edit. SHA is the commit the reviewers
# read. Merges only after every check in the script passes and you confirm; runner-sync then asks again
# before the clone moves (AUTO-1).
ship: ## Check a PR, wait for CI, ask, merge, then runner-sync (PR=<n> SHA=<reviewed>; runner clone only)
	@[[ "$(CURDIR)" == "$(realpath $(RUNNER_DIR))" ]] || (echo "only in the clone: make -C $(RUNNER_DIR) ship PR=<n> SHA=<reviewed>" && exit 2)
	@[[ "$$PR" =~ ^[0-9]+$$ && "$$SHA" =~ ^[0-9a-f]{40}$$ ]] || (echo "usage: make -C $(RUNNER_DIR) ship PR=<n> SHA=<reviewed>" && exit 2)
	@! $(RUNNER_BUSY) || (echo "a spending window is open: close it first" && exit 2)
	@/bin/bash tools/ship.sh "$$PR" "$$SHA" $(RUNNER_SYNC_PATHS)
	@$(MAKE) --no-print-directory runner-sync

# Your own terminal, from the clean clone, as `make -C ~/.northwind/runner finish CHECKOUT=$HOME/… SHA=<reviewed>
# BODY=docs/reviews/<file>`: after `/req-done`, commits what it wrote, pushes, opens the PR from the body file,
# adds `ci-change` if the diff needs it, then runs `ship`, which still asks. Git trusts a checkout's `.git`, so
# CHECKOUT must be one that `denyWrite` protects: tools/finish.sh checks. Safe to rerun. `baseline-change`, `gate-change` and NW_ALLOW_CONTROL stay yours to give (AUTO-2, decision 0011).
finish: ## Commit /req-done's files, push, open the PR, then ship (CHECKOUT=/abs/path SHA=<reviewed> BODY=<file>; runner clone only)
	@[[ "$(CURDIR)" == "$(realpath $(RUNNER_DIR))" ]] || (echo "only in the clone: make -C $(RUNNER_DIR) finish CHECKOUT=/abs/path SHA=<reviewed> BODY=docs/reviews/<file>" && exit 2)
	@! $(RUNNER_BUSY) || (echo "a spending window is open: close it first" && exit 2)
	@RUNNER_DIR="$(RUNNER_DIR)" /bin/bash tools/finish.sh

# The one diff advisor-reviewer accepts: base and head SHA first, so it can tell what it read. Refuses
# uncommitted work, because the head line would not describe it (AUTO-1).
REVIEW_DIFF ?= $(or $(TMPDIR),/tmp)/northwind-review.diff
review-diff: ## Write the committed diff against origin/main for review, SHAs first
	@[[ -z "$$(git status --porcelain)" ]] || (echo "commit first: review-diff covers commits only" && exit 2)
	@base=$$(git merge-base origin/main HEAD) && head=$$(git rev-parse HEAD) && { echo "base $$base"; echo "head $$head"; git --no-pager diff --text --no-ext-diff --no-textconv --no-renames "$$base" "$$head"; } > "$(REVIEW_DIFF)" && echo "$(REVIEW_DIFF)"

# Must run sandboxed: from a terminal every write succeeds, and it reports exactly that.
runner-check: ## From the sandbox: measure that nothing the runner loads is writable here (CC-27)
	uv run python -m northwind_llm_replay.cli runner-check

# SPENDS API CREDIT, through the window you opened with `llm-runner`; with none open it stops at once.
# Unchanged requests replay for free, new ones go to the runner, which holds the caps. The run stays
# in the sandbox and SUITE is checked before use, so 'x; cmd' cannot reach a shell (CC-27).
record: ## Re-record one LLM cassette suite (SUITE=name; spends API credit)
	@[[ "$$SUITE" =~ ^[a-z0-9_-]+$$ ]] || (echo "usage: make record SUITE=<suite>" && exit 2)
	LLM_MODE=record uv run pytest -m "" --llm-suite "$$SUITE"

# SPENDS API CREDIT, through the window you opened with `llm-runner SUITE=eval-answers`. Records
# answers and verdicts into a baseline candidate. EVAL_SLICE is required (a number of items; one at
# or above the item count is a full run), so a bare `make eval-live` cannot start a full run.
# Unchanged requests replay for free (RAG-12, decision 0001). EVAL_SLICE is checked before use, so
# 'x; cmd' cannot reach a shell (CC-27).
eval-live: ## Run answer evals live and record them (EVAL_SLICE=<n>; spends API credit)
	@[[ "$$EVAL_SLICE" =~ ^[0-9]+$$ ]] || (echo "usage: make eval-live EVAL_SLICE=<n>" && exit 2)
	LLM_MODE=record uv run python -m northwind_evals.cli run --slice "$$EVAL_SLICE"

# SPENDS API CREDIT, through the window you opened with `llm-runner SUITE=rag-enrichment`. Fills
# data/enrichment; chunks with an entry make no call, so a second run spends nothing (RAG-02,
# decision 0010).
enrich: ## Generate chunk context with Haiku (spends API credit through an open window)
	LLM_MODE=live uv run python -m northwind_evals.enrich

# Replay only. Needs the assistant-smoke cassette, and a browser (`playwright install chromium`).
e2e: ingest ## Smoke test: assistant in replay, then Playwright against a replayed answer (SA-02)
	LLM_MODE=replay uv run pytest -m e2e
	LLM_MODE=replay E2E_MODE=replay $(PNPM) --filter @northwind/portal exec playwright test

e2e-scripted: ingest ## The Playwright smoke test on a canned model: needs no recording (SA-02)
	LLM_MODE=replay E2E_MODE=scripted $(PNPM) --filter @northwind/portal exec playwright test

eval-gate: ingest ## Regression gate: live retrieval metrics + replayed answers vs the baseline (RAG-12)
	LLM_MODE=replay EVAL_ALLOW_STALE= uv run python -m northwind_evals.cli gate

# Local only: reports stale items but lets them pass. CI never runs this (decision 0001).
eval-gate-stale-ok: ingest ## The gate, but stale items pass and are reported as stale
	LLM_MODE=replay EVAL_ALLOW_STALE=1 uv run python -m northwind_evals.cli gate

# Never spends: a request with no recording is a failed item.
eval-rescore: ingest ## Rebuild the baseline candidate from the recordings after a scoring change
	LLM_MODE=replay uv run python -m northwind_evals.cli rescore

eval-integrity: ## Refuse edited cassettes and lost overrides, against origin/main (decision 0003)
	uv run python -m northwind_evals.cli integrity

# Your own terminal, from the clean clone, as `make -C ~/.northwind/runner eval-promote CHECKOUT=$HOME/…`:
# from a worktree, this file is one the sandbox can edit, so it refuses there. CHECKOUT is read as data.
# Everything free-text reaches the code through the environment, so nothing here can expand it.
# CHECK=1 is a dry run, and prints the candidate's hash. A regression or a relabel needs
# ACCEPT_REGRESSION="reason", APPROVED_BY="name" and CANDIDATE_SHA from that run (decision 0009).
# `origin/main` moves only at runner-sync, so the real main is asked as well.
eval-promote: ## Replace the baseline in CHECKOUT with its candidate (CHECKOUT=/abs/path; runner clone only)
	@[[ "$(CURDIR)" == "$(realpath $(RUNNER_DIR))" ]] || (echo "only in the clone: make -C $(RUNNER_DIR) eval-promote CHECKOUT=/abs/path APPROVED_BY=<name>" && exit 2)
	@! $(RUNNER_BUSY) || (echo "a spending window is open: close it first" && exit 2)
	@state=$$(git status --porcelain) && [[ -z "$$state" ]] || (echo "the clone is not clean" && exit 2)
	@head=$$(git rev-parse --verify HEAD) && main=$$(git rev-parse --verify origin/main) && remote=$$(git ls-remote origin refs/heads/main) && [[ "$$head" == "$$main" && "$$head" == "$${remote%%[[:space:]]*}" ]] || (echo "the clone is not at origin/main: run runner-sync" && exit 2)
	@$(RUNNER_PYTHON) -m northwind_llm_replay.cli promote

eval-snapshot: ## Save each eval item's retrieved context, the input for answers (no LLM; RAG-12)
	uv run python -m northwind_evals.cli snapshot

eval-enrichment-report: ## Compare the enriched index with the plain one; changes neither (RAG-02)
	uv run python -m northwind_evals.cli enrichment-report

eval-review-list: ## Write the list of eval items waiting for human review (RAG-12)
	uv run python -m northwind_evals.cli review-list

# `make test` here can skip the ship tests when nothing they cover changed; GitHub CI runs them all.
ci: lint scan-cassettes test test-integration eval-integrity eval-gate ## What CI runs, in the same order, except its browser job
