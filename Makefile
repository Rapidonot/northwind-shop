# One command per job, the same for humans, Claude and CI. Run `make` to list targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help

export COREPACK_ENABLE_DOWNLOAD_PROMPT := 0
PNPM ?= corepack pnpm
COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml

# Load .env (if present) so DATABASE_URL etc. reach the gateway and tests.
ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: help setup up down dev dev-gateway dev-portal fmt lint test test-integration ci

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-18s %s\n", $$1, $$2}'

setup: ## Install Python + Node deps; create .env from env.example if missing
	uv sync
	$(PNPM) install
	@test -f .env || (cp env.example .env && echo "created .env from env.example")

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

fmt: ## Auto-format and auto-fix Python
	uv run ruff format
	uv run ruff check --fix

lint: ## Lint + type-check Python and TypeScript
	uv run ruff check
	uv run ruff format --check
	uv run mypy services
	$(PNPM) -r lint
	$(PNPM) -r typecheck

test: ## Unit tests (Python + portal)
	uv run pytest
	$(PNPM) -r test

test-integration: ## Integration tests against the local stack (run `make up` first)
	uv run pytest -m integration

ci: lint test test-integration ## Everything CI runs
