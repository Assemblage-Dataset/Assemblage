# Alembic migrations — ground truth and rules

## The 2026-07-15 audit (read this before touching models.py)

After the repo's `.git` was destroyed and recovered, the live database
(docker volume `assemblage_db-data`) reported `alembic_version =
d3e4f5a6b7c8` — a revision whose migration file had been lost with the old
tree. The file was **reconstructed** on 2026-07-15
(`2026_03_16_0000-d3e4f5a6b7c8_enum_columns_to_varchar_add_build_type.py`)
by diffing a schema-only `pg_dump` of the live database against a scratch
database migrated to the previous head `b1c2d3e4f5a6`. The reconstructed
upgrade reproduces the live schema **byte-identically**.

Facts about the live schema this establishes:

- **No column uses a PG enum type.** Migration `fa6e74da04d4` converted
  several columns to PG enums; `d3e4f5a6b7c8` converted them all back to
  VARCHAR. Seven enum *types* (`buildstatus`, `clonestatus`,
  `prioritystatus`, `supported*`) still exist in `pg_type` as **orphans** —
  on the live DB and, by design, on any freshly-migrated DB.
- **Status/priority/platform columns store enum member NAMES** (uppercase:
  `'SUCCESS'`, `'NOT_STARTED'`, `'LOW'`) — SQLAlchemy's `Enum` type
  serializes by name into the varchar columns. The RabbitMQ wire format, by
  contrast, carries enum **values** (lowercase `"success"`); both
  conventions are frozen.
- `projects.language` holds both `'c++'` (current, 28k rows) and `'CPP'`
  (1,594 rows from the enum era). Treat as historical data; do not migrate.
- `binaries.optimization` is VARCHAR(16) NOT NULL and every row is `''` —
  `insert_binary` never populates it; compiler/flag are recovered by joining
  `binaries → b_status → buildopt`. Frozen behavior.
- `buildopt.build_type` VARCHAR(32) NOT NULL DEFAULT `'RelWithDebInfo'`
  exists in the DB but (post-.git-loss) nowhere in the code. All rows are
  `'RelWithDebInfo'`. The column is kept; code re-grows support for it only
  as a deliberate feature, not as part of the re-architecture.

## Rules

1. **Never commit `alembic revision --autogenerate` output.** The models
   are SQLModel classes whose Python-side enum typing can bait autogenerate
   (via `alembic-postgresql-enum`) into enum conversions or column drops.
   Autogenerate is run only to *prove the diff is empty* (CI drift gate).
2. Migrations are handwritten, reviewed, and applied with
   `docker exec -it assemblage-coordinator-1 alembic upgrade head`.
3. The schema is frozen to the live database for the duration of the
   re-architecture (see RE-ARCHITECTURE.md).
