# `assemblage` package map

The re-architected core. Behavior is frozen (queue names, wire JSON, DB
conventions, S3 layout, metadata keys); see `../alembic/README.md`.
Everything here passes ruff + mypy strict except the
frozen `legacy/` subpackage, which is excluded from all gates.

## Foundation

| Module | Responsibility |
|---|---|
| `enums.py` | `BuildStatus`, `CloneStatus`, `SupportedCompiler`, `OptLevel`, `WorkerType`, … (wire values are lowercase; DB stores names) |
| `constants.py` | queue names, dispatch intervals/thresholds, bundle size |
| `settings.py` | pydantic-settings v2 (`CoordinatorSettings`, `BuilderSettings`, `ScraperSettings`); env aliases preserved, `RABBITMQ_USER/PASS` added |
| `messages.py` | pydantic v2 wire messages; golden-tested against `tests/fixtures/messages/` |

## Runtime substrate

| Package | Responsibility |
|---|---|
| `runtime/` | `Service` ABC + `Supervisor` (named threads, restart-with-backoff, graceful SIGTERM) |
| `mq/` | `topology`, `connection` (retry/backoff), `consumer` (exactly one ack/nack; `ack_early` only for the builder task queue), `publisher` (confirms on, raises on failure) |
| `db/` | `engine`, `models` (live-schema truth), `store` (session-per-op, re-raises), `bootstrap` |
| `storage/` | `s3` client + `layout.py` — the single source of S3-key truth |

## Build path

| Package | Responsibility |
|---|---|
| `build/` | `commands` (`run_command` with `start_new_session` + killpg fix), `detect`, `discovery`, `strategy` (factory — the only lazy `legacy.windows` import), `linux.py` |
| `dwarf/extract.py` | shared DWARF extractor (builder + dataset pipeline), with duplicate-function dedup |

## Workers (composition roots under `../scripts/start_worker.py`)

| Package | `TYPE` | Responsibility |
|---|---|---|
| `coordinator/` | `coordinator` | `app` supervises consumers + `dispatch` (one thread per buildopt) + `scraper_requests`; `ingest`, `registration` |
| `builder/` | `builder` | `app`, `source` (S3 restore-or-clone), `pipeline`, `artifacts` (frozen metadata keys), `report` |
| `scraper/` | `scraper` | `github` (rate-limited search client), `app` (crawl service) |
| `dataset/` | — | host-side CLI absorbed from the old submodule: `cli`, `orm`, `store`, `construct`, `pipeline`, `daily` |
| `legacy/` | `legacy_conan` | FROZEN Windows/MSVC + Conan; excluded from gates, lazy-imported only |

## Adding a build target

1. Add enums in `enums.py` (`SupportedPlatform`, `SupportedCompiler`, …).
2. Add a `BuildStrategy` implementation under `build/` and register it in
   `build/strategy.py`'s factory.
3. Ensure `builder/artifacts.py` handles the new platform's outputs.
4. Add a docker-compose builder stanza (`TYPE=builder`, `compiler`, `language`,
   `COMPILER_FLAG`) — it auto-registers a new buildopt.
