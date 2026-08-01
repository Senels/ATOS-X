.PHONY: install dev test lint up down logs migrate migration shell

install:
	python -m venv .venv && .venv/bin/pip install -e "backend[dev]"

dev:
	.venv/bin/uvicorn app.main:app --reload --app-dir backend

test:
	.venv/bin/python -m pytest backend/tests

lint:
	.venv/bin/ruff check backend

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend

migrate:
	.venv/bin/alembic -c backend/alembic.ini upgrade head

migration:
	.venv/bin/alembic -c backend/alembic.ini revision --autogenerate -m "$(name)"

shell:
	.venv/bin/python -c "import app; print('ATOS X shell ok')"
