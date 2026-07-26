.DEFAULT_GOAL := help
PY := ./.venv/bin/python
PIP := ./.venv/bin/pip

.PHONY: help venv install install-dev db-up db-down migrate lint fmt typecheck test \
        export train recategorize clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	python3 -m venv .venv

install: ## Install the package + service deps in editable mode
	$(PIP) install -e '.[svc,dev]'

install-dev: ## Install the development tools only (lint/test)
	$(PIP) install -e '.[dev]'

db-up: ## Start Postgres (docker-compose)
	docker compose -f deploy/docker-compose.yml up -d

db-down: ## Stop Postgres
	docker compose -f deploy/docker-compose.yml down

migrate: ## Apply the Alembic migrations
	./.venv/bin/alembic upgrade head

lint: ## Ruff (lint)
	./.venv/bin/ruff check .

fmt: ## Ruff (format + autofix)
	./.venv/bin/ruff format . && ./.venv/bin/ruff check --fix .

typecheck: ## Mypy
	./.venv/bin/mypy src

test: ## Unit tests (pytest)
	CASHATO_CONFIG_DIR=config $(PY) -m pytest

export: ## Unified export (LANG=it|en)
	CASHATO_CONFIG_DIR=config $(PY) -m cashato.cli.export --lang $(or $(LANG),it)

train: ## Build the embedding index (M2)
	CASHATO_CONFIG_DIR=config CASHATO_MODEL_DIR=models $(PY) -m cashato.ml.train --include-rules --stamp "$$(date +%Y%m%d-%H%M)"

recategorize: ## Re-categorize the DB with the model (M3)
	CASHATO_CONFIG_DIR=config CASHATO_MODEL_DIR=models $(PY) -m cashato.ml.recategorize

clean: ## Remove caches and temporary artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
