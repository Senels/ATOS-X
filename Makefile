.PHONY: install dev test lint shell

install:
	python -m venv .venv && .venv/bin/pip install -e "backend[dev]"

dev:
	.venv/bin/uvicorn app.main:app --reload --app-dir backend

test:
	.venv/bin/python -m pytest backend/tests

lint:
	.venv/bin/ruff check backend

shell:
	.venv/bin/python -c "import app; print('ATOS X shell ok')"
