.PHONY: install dev test lint up down logs

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
