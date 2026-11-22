.PHONY: install run seed test integration check up down

install:
	uv sync --locked

run:
	uv run --locked uvicorn app.main:app --reload

seed:
	uv run --locked python -m scripts.seed

test:
	uv run --locked pytest --cov=app --cov-report=term-missing

integration:
	sh scripts/test-integration.sh

check:
	uv run --locked ruff check .
	uv run --locked ruff format --check .

up:
	docker compose up --build -d --wait

down:
	docker compose down
