.PHONY: install dev test lint shell docker-build docker-up docker-down docker-logs docker-clean

install:
	python -m venv .venv && .venv/bin/pip install -e "backend[dev]"

dev:
	.venv/bin/uvicorn app.main:app --reload --app-dir backend

test:
	.venv/bin/python -m pytest backend/tests

lint:
	.venv/bin/python -m compileall -q backend

shell:
	.venv/bin/python -c "import app; print('ATOS X shell ok')"

# ── Docker hedefleri ──────────────────────────────────────────────────────────

docker-build:
	docker build -t atos-x:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f atos-backend

docker-clean:
	docker compose down -v --rmi local
