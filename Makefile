.PHONY: dev test migrate seed down

dev:
	docker compose up --build

down:
	docker compose down

migrate:
	docker compose exec api alembic upgrade head

test:
	docker compose exec api pytest

seed:
	docker compose exec api python scripts/seed.py
