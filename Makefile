.PHONY: lint typecheck test e2e e2e-down parity dataset-gate up down

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
	docker compose -f compose/e2e.yml up \
		--abort-on-container-exit --exit-code-from injector; \
	status=$$?; \
	docker compose -f compose/e2e.yml down -v --remove-orphans; \
	exit $$status

e2e-down:
	docker compose -f compose/e2e.yml down -v --remove-orphans

# Dataset parity gate (P10) — HISTORICAL, retired at R5. Kept for provenance of
# the P10 "output unchanged" claim; not an acceptance gate (R5 changes output
# on purpose). Use `make dataset-gate` instead.
parity:
	tests/e2e/dataset_parity.sh

# Dataset correctness gate (R5): brings up the golden-repo E2E stack, runs the
# injector, then runs the current daily pipeline host-side and asserts the
# resulting SQLite corpus is correctly populated for a C and a Rust binary
# (functions/rvas/lines, Rust compiler/language/backend + demangled_name/origin).
# Self-contained (brings the stack up and tears it down).
dataset-gate:
	tests/e2e/dataset_correctness.sh

up:
	docker compose up --build -d

down:
	docker compose down
