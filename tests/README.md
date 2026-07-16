# tests/

Pytest suite. **Green by default** — the old "`failures=5, skipped=4` is
normal" baseline is retired (that masked a discovery bug that silently skipped
the message tests and a `basic_ack` mock defect, both fixed).

## Layout

- `unit_tests/` — fast, no external services (RabbitMQ/DB/S3 mocked).
- `integration_tests/` — real PostgreSQL/RabbitMQ (`store_test`,
  `coordinator_int_test`, the schema-`drift_test`).
- `e2e/` — the golden-repo end-to-end gate (`injector.py`, driven by `make e2e`).
- `invariants/`, `fixtures/` — wire goldens and frozen fixtures. **Do not edit
  fixtures to make a test pass** — if code can't satisfy a golden, the code is wrong.

## Markers (see `pyproject.toml`)

The default selection deselects the slow/external markers:

```toml
addopts = "-m 'not integration and not e2e and not live_github' --strict-markers"
```

| Marker | Needs | How to run |
|---|---|---|
| (none) | nothing | `uv run pytest tests/` |
| `integration` | live PostgreSQL/RabbitMQ | `docker compose up -d database` then `uv run pytest tests/ -m integration` |
| `e2e` | full compose stack | `make e2e` |
| `live_github` | real GitHub API (rate-limited) | `uv run pytest tests/ -m live_github` (nightly) |

`--strict-markers` means an unknown marker is an error, not a silent skip.

Test files are named `*_test.py`.
