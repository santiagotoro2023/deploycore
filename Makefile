.PHONY: install dev test migrate seed down update

install:
	./scripts/setup.sh

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

update:
	git pull && docker compose up -d --build
