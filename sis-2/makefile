COUNT ?= 10000

up:
	docker compose up -d --wait

down:
	docker compose down

clean:
	docker compose down -v

psql:
	docker compose exec postgres psql -U catalog -d catalog

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

seed:
	docker compose exec -T postgres psql -U catalog -d catalog -v ON_ERROR_STOP=1 -v count=$(COUNT) < seed.sql
rest:
	uv run hypercorn app.rest:app --bind 127.0.0.1:8000 --reload

proto:
	uv run python -m grpc_tools.protoc -Iapp=proto --python_out=. --pyi_out=. --grpc_python_out=. proto/catalog.proto

grpc:
	uv run python -m app.grpc_server
