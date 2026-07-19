.DEFAULT_GOAL := help
PY := ./.venv/bin/python
PIP := ./.venv/bin/pip

.PHONY: help venv install install-dev db-up db-down migrate lint fmt typecheck test \
        export train recategorize clean

help: ## Mostra questo aiuto
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Crea il virtualenv
	python3 -m venv .venv

install: ## Installa le dipendenze runtime
	$(PIP) install -r requirements.txt

install-dev: ## Installa le dipendenze di sviluppo (lint/test)
	$(PIP) install -r requirements-dev.txt

db-up: ## Avvia Postgres (docker-compose)
	docker compose -f deploy/docker-compose.yml up -d

db-down: ## Ferma Postgres
	docker compose -f deploy/docker-compose.yml down

migrate: ## Applica le migrazioni Alembic
	./.venv/bin/alembic upgrade head

lint: ## Ruff (lint)
	./.venv/bin/ruff check .

fmt: ## Ruff (format + autofix)
	./.venv/bin/ruff format . && ./.venv/bin/ruff check --fix .

typecheck: ## Mypy
	./.venv/bin/mypy libs db ml

test: ## Test unitari (pytest)
	$(PY) -m pytest

export: ## Export unificato (LANG=it|en)
	$(PY) export.py --lang $(or $(LANG),it)

train: ## Costruisce l'indice a embedding (M2)
	$(PY) ml/train.py --include-rules --stamp "$$(date +%Y%m%d-%H%M)"

recategorize: ## Ri-categorizza il DB con il modello (M3)
	$(PY) ml/recategorize.py

clean: ## Rimuove cache e artefatti temporanei
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
