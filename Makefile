# Convenience targets for local development. Everything here is a thin wrapper
# around the commands in the README — nothing in the project requires make.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
RUFF    := $(VENV)/bin/ruff
DB      := data/archive.db

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup ------------------------------------------------------------------

.PHONY: install
install: $(VENV) ## Install backend, scraper and web dependencies
	$(PIP) install -q -r backend/requirements.txt -r scraper/requirements.txt
	$(PIP) install -q pytest httpx ruff
	cd web && npm install

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip

.PHONY: seed
seed: ## Load demo accounts, jobs and error history into the database
	@mkdir -p data
	@# Migrations are idempotent, so this is safe against an existing database.
	@for f in db/migrations/*.sql; do sqlite3 $(DB) < $$f; done
	sqlite3 $(DB) < db/seed/demo_seed.sql
	@echo "seeded $(DB)"

# --- run --------------------------------------------------------------------

.PHONY: backend
backend: ## Run the API with reload on :8000, and an embedded scraper worker
	# EMBED_SCRAPER starts worker.main as a sibling process so adding an account
	# does not sit forever next to "Scraper never connected". The dedicated
	# `make scraper` target is still the right way to run a standalone worker.
	cd backend && EMBED_SCRAPER=1 ../$(VENV)/bin/uvicorn app.main:app --reload --port 8000

.PHONY: scraper
scraper: ## Run the scraper worker in the foreground
	cd scraper && ../$(VENV)/bin/python -m worker.main

.PHONY: web
web: ## Run the Next.js dev server on :3000
	cd web && npm run dev

.PHONY: dev
dev: ## Install deps, then print the two commands that bring the stack up locally
	@echo "In two terminals:"
	@echo "  make backend    # API on :8000 + embedded scraper"
	@echo "  make web        # dashboard on :3000"

.PHONY: up
up: ## Build and start all three services in Docker
	docker compose up -d --build

.PHONY: down
down: ## Stop all services (leaves the archive and database intact)
	docker compose down

.PHONY: logs
logs: ## Follow container logs
	docker compose logs -f

# --- checks -----------------------------------------------------------------

.PHONY: test
test: test-backend test-scraper test-web ## Run every test suite

.PHONY: test-backend
test-backend: ## Backend tests: scanner, dedup, API surface
	cd backend && ../$(VENV)/bin/python -m pytest

.PHONY: test-scraper
test-scraper: ## Scraper tests: sync engine, dedup guards, pacing across runs
	cd scraper && ../$(VENV)/bin/python -m pytest

.PHONY: test-web
test-web: ## Frontend lint, typecheck and production build
	cd web && npm run lint && npx tsc --noEmit && npm run build

.PHONY: lint
lint: ## Lint and format-check every service
	$(RUFF) check backend scraper
	$(RUFF) format --check backend scraper
	cd web && npm run lint && npx tsc --noEmit

.PHONY: format
format: ## Apply formatting fixes
	$(RUFF) check --fix backend scraper
	$(RUFF) format backend scraper

.PHONY: schema-check
schema-check: ## Apply migrations to a throwaway database and verify integrity
	@rm -f /tmp/schema-check.db
	@for f in db/migrations/*.sql; do echo "-- $$f"; sqlite3 /tmp/schema-check.db < $$f; done
	@sqlite3 /tmp/schema-check.db < db/seed/demo_seed.sql
	@sqlite3 /tmp/schema-check.db "PRAGMA integrity_check;"
	@test -z "$$(sqlite3 /tmp/schema-check.db 'PRAGMA foreign_key_check;')" \
		&& echo "foreign keys ok" || (echo "foreign key violations"; exit 1)

# --- cleanup ----------------------------------------------------------------

.PHONY: clean
clean: ## Remove build artefacts and caches. Never touches /archive or the database.
	rm -rf web/.next web/out web/tsconfig.tsbuildinfo
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .ruff_cache */.ruff_cache */.pytest_cache
