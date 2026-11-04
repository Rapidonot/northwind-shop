# One command per job, the same for humans, Claude and CI. Run `make` to list targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help

export COREPACK_ENABLE_DOWNLOAD_PROMPT := 0
PNPM ?= corepack pnpm
COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml

# Load .env (if present) so DATABASE_URL etc. reach the gateway and tests.
# The API key is the exception: it is blanked and never exported to recipes (hard rule 4,
# decision 0001). Only the LLM proxy reads it, straight from .env, via dotenv_values (FOUND-2).
ifneq (,$(wildcard .env))
include .env
export
endif
override ANTHROPIC_API_KEY :=
unexport ANTHROPIC_API_KEY

.PHONY: help setup up down dev dev-gateway dev-portal data ingest fmt lint test test-integration ci \
	scan-cassettes llm-spend llm-proxy record eval-live

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

ingest: ## Load data/fixtures into the RAG index (needs `make up`; idempotent; RAG-01)
	uv run python -m northwind_rag.cli --fixtures data/fixtures

fmt: ## Auto-format and auto-fix Python
	uv run ruff format
	uv run ruff check --fix

lint: ## Lint + type-check Python and TypeScript
	uv run ruff check
	uv run ruff format --check
	uv run mypy services data
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
record: ## Re-record one LLM cassette suite (SUITE=name; spends API credit)
	@test -n "$(SUITE)" || (echo "usage: make record SUITE=<suite>" && exit 2)
	LLM_MODE=record uv run pytest --llm-suite $(SUITE)

# SPENDS API CREDIT. Answer/journey evals live (RAG-12 adds the eval suites and harness).
eval-live: ## Run the LLM evals live and record them (spends API credit)
	@if [ -d evals ]; then LLM_MODE=record uv run pytest evals; else echo "no eval suites yet (RAG-12)"; fi

ci: lint scan-cassettes test test-integration ## Everything CI runs, in the same order
