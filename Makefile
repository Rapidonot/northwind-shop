# One command per job, the same for humans, Claude and CI. Run `make` to list targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help

export COREPACK_ENABLE_DOWNLOAD_PROMPT := 0
PNPM ?= corepack pnpm
# --env-file only when the file exists: compose exits 1 on a missing one, and linked worktrees never
# have one (FOUND-2 review #12). Without it, compose uses the defaults in docker-compose.yml.
COMPOSE := docker compose $(if $(wildcard .env),--env-file .env )-f infra/docker-compose.yml

# Load .env (if present) so DATABASE_URL etc. reach the gateway and tests.
# The API key is the exception: it is blanked and never exported to recipes (hard rule 4,
# decision 0001). Only the LLM proxy reads it, straight from .env, via dotenv_values (FOUND-2).
# -include: Claude's sandbox denies reads of .env, which must not stop every target (CC-27).
ifneq (,$(wildcard .env))
-include .env
export
endif
override ANTHROPIC_API_KEY :=
unexport ANTHROPIC_API_KEY

.PHONY: help setup up down dev dev-gateway dev-portal data model ingest fmt lint test \
	test-integration ci scan-cassettes llm-spend llm-proxy record eval-live eval-rescore eval-gate \
	eval-integrity eval-gate-stale-ok eval-promote eval-snapshot eval-review-list e2e e2e-scripted

# A stable cache, not fastembed's temp-dir default: Claude's sandbox has a different temp dir and
# cannot reach the model's download hosts, so `make model` fills this once from a terminal (CC-27).
export FASTEMBED_CACHE_PATH ?= $(HOME)/.cache/fastembed

# Linked git worktrees never get a .env: the LLM proxy reads the key from the main checkout's
# file (LLM_ENV_FILE), and nothing else needs one there (FOUND-2 addendum).
IN_LINKED_WORKTREE := $(shell test "$$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)" != "$$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" && echo 1)

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-18s %s\n", $$1, $$2}'

setup: ## Install Python + Node deps; create .env from env.example if missing (main checkout only)
	uv sync
	$(PNPM) install
ifeq ($(IN_LINKED_WORKTREE),1)
	@echo "linked worktree: no .env here; the LLM proxy uses the main checkout's (LLM_ENV_FILE)"
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

model: ## Pre-fetch the embedding model into FASTEMBED_CACHE_PATH (run in your own terminal; CC-27)
	uv run python -c "from northwind_rag.embedding import FastEmbedEmbedder; FastEmbedEmbedder().embed(['warm'])"

ingest: ## Load data/fixtures into the RAG index (needs `make up`; idempotent; RAG-01)
	uv run python -m northwind_rag.cli --fixtures data/fixtures

fmt: ## Auto-format and auto-fix Python
	uv run ruff format
	uv run ruff check --fix

lint: ## Lint + type-check Python and TypeScript
	uv run ruff check
	uv run ruff format --check
	uv run mypy services data evals
	$(PNPM) -r lint
	$(PNPM) -r typecheck

# LLM_MODE is pinned on the recipe line, so neither the shell, the key file nor
# `make test LLM_MODE=…` can turn these targets into spending ones (decision 0001).
test: ## Unit tests (Python + portal), always in replay
	LLM_MODE=replay uv run pytest
	$(PNPM) -r test

test-integration: ## Integration tests against the local stack (run `make up` first), always in replay
	LLM_MODE=replay uv run pytest -m integration

scan-cassettes: ## Secret/PAN scan of cassettes, ledger and proxy log (FOUND-2)
	uv run python -m northwind_llm_replay.cli scan

llm-spend: ## LLM spend from the shared ledger: total, per suite/model, 20-call checkpoint (FOUND-2)
	uv run python -m northwind_llm_replay.cli spend

llm-proxy: ## Dev LLM proxy on this worktree's port (LLM_MODE=replay by default; FOUND-2)
	uv run python -m northwind_llm_replay.cli serve

# SPENDS API CREDIT. Re-records one cassette suite: unchanged requests replay for free, new ones go
# live through the budget guard (LLM_RUN_BUDGET_USD, default 5.00; see services/llm-replay/SPIKE.md).
# SUITE is checked before use: this target runs outside Claude's sandbox, so a value like
# 'x; cmd' must not reach a shell (CC-27).
record: ## Re-record one LLM cassette suite (SUITE=name; spends API credit)
	@[[ "$$SUITE" =~ ^[a-z0-9_-]+$$ ]] || (echo "usage: make record SUITE=<suite>" && exit 2)
	LLM_MODE=record uv run pytest -m "" --llm-suite "$$SUITE"

# SPENDS API CREDIT. Records answers and verdicts into a baseline candidate. EVAL_SLICE is required
# (a number of items; one at or above the item count is a full run), so a bare `make eval-live`
# cannot start a full run. Unchanged requests replay for free (RAG-12, decision 0001).
# EVAL_SLICE is checked before use: this target runs outside Claude's sandbox, so a value like
# 'x; cmd' must not reach a shell (CC-27).
eval-live: ## Run answer evals live and record them (EVAL_SLICE=<n>; spends API credit)
	@[[ "$$EVAL_SLICE" =~ ^[0-9]+$$ ]] || (echo "usage: make eval-live EVAL_SLICE=<n>" && exit 2)
	LLM_MODE=record uv run python -m northwind_evals.cli run --slice "$$EVAL_SLICE"

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

eval-promote: ## Replace the baseline with the candidate (a regression needs ACCEPT_REGRESSION="reason", which is recorded)
	uv run python -m northwind_evals.cli promote $(if $(ACCEPT_REGRESSION),--accept-regression "$$ACCEPT_REGRESSION") $(if $(BOOTSTRAP),--bootstrap "$$BOOTSTRAP")

eval-snapshot: ## Save each eval item's retrieved context, the input for answers (no LLM; RAG-12)
	uv run python -m northwind_evals.cli snapshot

eval-review-list: ## Write the list of eval items waiting for human review (RAG-12)
	uv run python -m northwind_evals.cli review-list

ci: lint scan-cassettes test test-integration eval-integrity eval-gate ## What CI runs, in the same order, except its browser job
