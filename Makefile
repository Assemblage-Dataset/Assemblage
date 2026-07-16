.PHONY: lint typecheck test e2e e2e-down parity up down

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

# Dataset parity gate (P10): runs the daily pipeline from the pre-P10 tree and
# the current tree against one identical E2E stack state and proves the corpus
# is row-identical. Self-contained (brings the stack up and tears it down).
parity:
	tests/e2e/dataset_parity.sh

up:
	docker compose up --build -d

down:
	docker compose down
