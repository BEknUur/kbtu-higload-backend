up:
	docker compose up --build -d 

down:
	docker compose down 

clean:
	docker compose down -v 

psql:
	docker compose exec postgres psql -U catalog -d catalog

schema:
	docker compose exec -T postgres psql -U catalog -d catalog -v ON_ERROR_STOP=1 < schema.sql


COUNT ?= 10000

seed:
	docker compose exec -T postgres psql -U catalog -d catalog -v ON_ERROR_STOP=1 -v count=$(COUNT) < seed.sql