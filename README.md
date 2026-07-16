# Assemblage

Assemblage is a distributed binary-corpus generator. It discovers licensed C/C++
repositories on GitHub, builds them with multiple compilers and optimization
levels, and archives the resulting binaries with rich, function-level metadata —
producing labeled training data for machine-learning approaches to binary
analysis (and for static/dynamic analysis and reverse engineering).

Paper: [arxiv.org/abs/2405.03991](https://arxiv.org/abs/2405.03991).
Code is MIT-licensed. The published dataset (permissively-licensed subset only)
is at [assemblage-dataset.net](https://assemblage-dataset.net); see the
[data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf).

## Architecture in one page

```
 GitHub ─▶ scraper ─▶ [scrape] ─▶ coordinator ─▶ [build_opt_{id}] ─▶ builders
                                      │                                  │
                                      ▼                                  ▼
                                 PostgreSQL  ◀── [clone|build|binary] ── MinIO
                                                                          │
                                        host-side daily pipeline ◀────────┘
                                                     │
                                                     ▼
                                         linux_licensed.sqlite (corpus)
```

- **scraper** — date-windowed GitHub search, license-filtered, language
  lowercased; emits bundles of 25 repos.
- **coordinator** — inserts repos, creates one `b_status` row per buildopt, and
  runs one dispatch thread per buildopt (per-opt pacing; builders ack before
  building — at-most-once).
- **builders** — 10 services (gcc/clang × `-O0 -O1 -O2 -O3 -Os`), each a distinct
  buildopt with its own dispatch queue; clone or restore-from-S3, build, extract
  DWARF, upload artifacts + `assemblage_meta.json`.
- **dataset pipeline** — pulls new licensed Linux artifacts from MinIO and
  appends them (with DWARF function/RVA/line info) to a cumulative SQLite corpus.

**Rust support.** Alongside the C/C++ flow, Assemblage builds Rust (cargo)
repositories: a dedicated `scraper_rust` service scrapes `language:rust`, and 9
`builder_rust_*` services compile each repo with `rustc`'s three
`-Zcodegen-backend` targets (LLVM, Cranelift, and GCC/cg_gcc) across build modes
and optimization levels, all from one pinned nightly (`docker/rust/Dockerfile`,
image `assemblage-rust:default`). Rust binaries carry the same DWARF
function/line metadata as the C corpus, plus demangled names and per-function
origin tags (in-repo vs. dependency vs. stdlib). See the worker matrix in
`CLAUDE.md`.

The Python package lives under `backend/assemblage/`; see `CLAUDE.md` for the
module map.

## Quickstart

Requirements: Docker + docker compose. [uv](https://docs.astral.sh/uv/) for the
Python tooling and host-side scripts (`export PATH="$HOME/.local/bin:$PATH"`).

```bash
git clone <this repo> && cd Assemblage
cp secrets.env.example secrets.env      # fill in POSTGRES_PASSWORD, GITHUB_TOKEN,
                                        # S3/MinIO creds, RABBITMQ_* (default guest)
docker compose up -d                    # postgres + rabbitmq + minio + coordinator
                                        # + scraper + the 10-builder matrix
docker exec -it assemblage-coordinator-1 alembic upgrade head   # first run only
```

Artifacts go to MinIO (API on host port **9010**, console on **9011**). The
`./backend` tree is bind-mounted into every worker, so code edits are live
without a rebuild.

Scale the builder matrix without editing the compose file:

```bash
BUILDER_REPLICAS=4 BUILDER_MEM=8g docker compose up -d
docker compose up -d --scale builder_6=20        # more gcc -O2 workers
docker compose down                              # stop (keeps volumes)
```

A minimal terminal UI wraps the above: `python assemblage_tui.py`.

## Worker images

Both toolchain variants build from one Dockerfile:

```bash
docker build --build-arg TOOLCHAIN=gcc   -t assemblage-gcc:default   -f docker/worker/Dockerfile .
docker build --build-arg TOOLCHAIN=clang -t assemblage-clang:default -f docker/worker/Dockerfile .
```

Both install the same union apt set so gcc- and clang-built binaries stay
comparable; the clang variant exposes `gcc`/`cc` via a PATH shim over clang.

## Daily dataset pipeline

Fetches newly-built licensed Linux binaries from MinIO, re-extracts DWARF, and
appends them to `assemblage_dataset/linux_licensed.sqlite`:

```bash
DB_HOST=localhost MINIO_ENDPOINT=localhost:9010 uv run assemblage-daily
# or: python backend/scripts/run_daily_dataset.py [--since YYYY-MM-DD]
```

Host-side runs override the Docker-internal hostnames via env vars (above).
`assemblage_loop.sh` automates the restart + daily-run cycle. To re-stage raw
files already on disk, use `backend/scripts/restage_from_raw.py`.

## Tests

```bash
export PATH="$HOME/.local/bin:$PATH"
uv run pytest tests/                     # unit — green by default
uv run pytest tests/ -m integration      # needs: docker compose up -d database
make e2e                                 # golden-repo end-to-end gate
```

See `tests/README.md` for the marker policy.

## Distributed / Windows builders (optional)

Windows/MSVC builds require a Windows host and are quarantined under
`backend/assemblage/legacy/` + `docker/legacy/`. Point a remote builder's
`MQ_HOST`/`S3_HOST` at the coordinator (expose RabbitMQ 5672 and MinIO 9010) and
run `docker-compose-windows.yml` there.

> RabbitMQ defaults to `guest`/`guest` (override with `RABBITMQ_USER`/
> `RABBITMQ_PASS`). If you expose it, firewall it to your worker hosts and see
> the [RabbitMQ access-control guide](https://www.rabbitmq.com/docs/access-control).

## DeepHistory (legacy Conan corpus)

Standalone multi-version library corpus via Conan; does not use RabbitMQ or the
coordinator:

```bash
python backend/scripts/build_deephistory.py --packages sqlite3 fmt --output ./out
docker compose -f docker-compose-deephistory.yml up --build     # Linux images
```

## Troubleshooting

Set `RUNTIME_ENV=development` for debug logging. Most transient failures clear on
`docker compose restart <service>`. Migrations are handwritten and frozen to the
live DB — never commit `alembic revision --autogenerate` output
(`backend/alembic/README.md`).
