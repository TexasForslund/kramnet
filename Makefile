.PHONY: up down logs migrate shell restart

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

migrate:
	docker compose exec app alembic upgrade head

shell:
	docker compose exec app python

restart:
	docker compose restart app
