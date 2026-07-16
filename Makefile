.PHONY: lint typecheck test e2e e2e-down up down

lint:
	uv run ruff check . && uv run ruff format --check .

typecheck:
	uv run mypy backend/assemblage

test:
	uv run pytest tests/

# Golden-repo end-to-end gate: hermetic compose stack, injector exit code
# is the verdict. Always tears down (volumes included) afterwards.
e2e:
	mkdir -p tests/fixtures/golden
	docker compose -f docker-compose.e2e.yml up \
		--abort-on-container-exit --exit-code-from injector; \
	status=$$?; \
	docker compose -f docker-compose.e2e.yml down -v --remove-orphans; \
	exit $$status

e2e-down:
	docker compose -f docker-compose.e2e.yml down -v --remove-orphans

up:
	docker compose up --build -d

down:
	docker compose down
