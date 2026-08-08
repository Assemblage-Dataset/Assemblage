# Installing and deploying Assemblage

A step-by-step deployment guide, written to be followed literally by an agent or
an operator. Every step has a verification command and its expected output; do
not proceed past a step whose check fails.

Assemblage is a long-running distributed system, not a one-shot tool. Most of the
difficulty is in operating it after it starts, so read [Operating the
fleet](#5-operating-the-fleet) and [Failure modes](#7-failure-modes-that-actually-happen)
before leaving it unattended.

---

## 1. Prerequisites

| requirement | notes |
|---|---|
| Docker + `docker compose` v2 | the whole system is containers |
| Linux host | builders are Linux-only; Windows/MSVC is quarantined in `legacy/` |
| Disk | **TBs.** A few thousand repos across a build matrix reaches hundreds of GB; artifacts dominate |
| RAM | ~2 GB per builder minimum; DWARF extraction on large Rust binaries has been measured at ~42x binary size |
| CPU | one core per builder is the floor; Rust builders default to `CARGO_BUILD_JOBS=4` |
| [uv](https://docs.astral.sh/uv/) | host-side scripts and tests: `export PATH="$HOME/.local/bin:$PATH"` |
| GitHub token | scraping is rate-limited without one |

Do not size the fleet by core count alone. Extraction is single-threaded and is
the throughput bottleneck, so builders spend much of their time on one core;
past roughly 32 Rust builders the limit observed on one host was disk I/O, not
CPU.

## 2. Configure

```bash
git clone <this repo> && cd Assemblage
cp secrets.env.example secrets.env
```

Fill in `secrets.env`:

| key | purpose |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DATABASE` | metadata DB |
| `DB_HOST` / `DB_PORT` | `database` / `5432` inside compose |
| `GITHUB_TOKEN` | scraper API access |
| `S3_ACCESS_KEY` / `S3_SECRET_ACCESS_KEY` | must match the MinIO root creds |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO admin |
| `S3_HOST` / `S3_HTTPS` | `minio:9000` / `false` inside compose |

RabbitMQ defaults to `guest`/`guest` (`RABBITMQ_USER`/`RABBITMQ_PASS`). If you
expose port 5672, firewall it to your worker hosts first.

## 3. Build the worker images

Images are **not** pulled from a registry — build them locally. Expect ~10 min
each on a cold cache, mostly toolchain download.

```bash
# C/C++ (both variants share one Dockerfile and the same apt set so gcc- and
# clang-built binaries stay comparable)
docker build --build-arg TOOLCHAIN=gcc   -t assemblage-gcc:default   -f docker/worker/Dockerfile .
docker build --build-arg TOOLCHAIN=clang -t assemblage-clang:default -f docker/worker/Dockerfile .

# Rust — one pinned nightly serves all three codegen backends
docker build --build-arg RUST_TOOLCHAIN=nightly-2026-06-15 \
    -t assemblage-rust:default -f docker/rust/Dockerfile .
```

**Verify the Rust image before deploying it.** Cranelift is a gating component —
if the pinned nightly lacks it, Cranelift builders will fail every task:

```bash
docker run --rm --entrypoint bash assemblage-rust:default -c \
  'rustup component list --toolchain nightly-2026-06-15 | grep -i "cranelift.*installed"; \
   for p in openssl glib-2.0 wayland-client libudev alsa dbus-1; do \
     printf "%-14s %s\n" "$p" "$(pkg-config --modversion $p 2>/dev/null || echo MISSING)"; done'
```

Expect the cranelift line plus a version for every library. Any `MISSING` means
a class of `-sys` crates cannot link, and those repos will fail at build time
for reasons that look like bad repos but are packaging gaps — see the dependency
layer in `docker/rust/Dockerfile` for which failures each package fixes.

## 4. First boot

Bring the stack up **in order**. Builders that start before RabbitMQ resolves
will crash-loop on DNS.

```bash
# 4a. infra first
docker compose up -d database rabbitmq minio

# wait for health — do not skip
until [ "$(docker inspect -f '{{.State.Health.Status}}' assemblage-db)" = healthy ] \
   && [ "$(docker inspect -f '{{.State.Health.Status}}' assemblage-rabbitmq-1)" = healthy ]; do
  sleep 3
done

# 4b. schema (first run only)
docker exec -it assemblage-coordinator-1 alembic upgrade head

# 4c. coordinator, then workers
docker compose up -d coordinator scraper_rust
docker compose up -d --no-recreate builder_rust_llvm_o2 builder_rust_clift_o2
```

The coordinator must precede the builders: a buildopt's dispatch thread starts
only when a builder **registers**, so builders that are already running when the
coordinator starts will not be dispatched to. See
[Failure modes](#7-failure-modes-that-actually-happen).

**A bare `docker compose up -d` starts every service in the file** — 10 C/C++
builders at 10 replicas each plus 20 Rust services. Name the services you want.

### Verify the fleet is live

```bash
docker exec assemblage-rabbitmq-1 rabbitmqctl list_queues name messages consumers --quiet \
  | awk '/^build_opt_/ && $3>0 {print; c+=$3} END {print "consumers="c}'
```

Every running builder must appear as a consumer on its own `build_opt_{id}`
queue, with a non-zero message count. **`consumers=N` with `messages=0` on every
queue means the fleet is stranded, not idle** — the coordinator has no
dispatchers. Fix by restarting the builders so they re-register.

Then confirm work is actually landing:

```bash
docker exec assemblage-db psql -U assemblage -d assemblage -t \
  -c "SELECT count(*), max(build_date) FROM binaries;"
```

`max(build_date)` should advance within minutes. If it stalls while containers
look healthy, the fleet is stranded — that is the single most common failure
here and it is silent.

## 5. Operating the fleet

### Scaling

```bash
BUILDER_REPLICAS=4 BUILDER_MEM=8g docker compose up -d   # C/C++ defaults
docker compose up -d --scale builder_6=20 builder_6      # more gcc -O2
```

**`RUST_BUILDER_REPLICAS` is a trap.** It applies to all 20 Rust services,
including any you deliberately left parked, so raising it *starts* services you
did not intend to run. Scale per service and name only the services you want:

```bash
docker compose up -d --no-recreate \
  --scale builder_rust_llvm_o2=2 --scale builder_rust_clift_o2=1 \
  builder_rust_llvm_o2 builder_rust_clift_o2
```

Scaling a service to `0` stops and removes its containers; its queue then
accumulates undispatched tasks until a consumer returns.

### The supervisor loop

`assemblage_loop.sh` is the watchdog. It is **load-bearing**: `database` and
`rabbitmq` carry no restart policy, so nothing else will bring them back.

```bash
mkdir -p var                       # REQUIRED first — see below
nohup setsid flock -n var/loop.lock /bin/bash ./assemblage_loop.sh >/dev/null 2>&1 &
```

Two non-obvious requirements, both of which have silently disabled this watchdog
in production:

1. **`var/` must exist before `flock` runs.** The lock file lives there, and the
   `mkdir -p` that would create it is *inside* the script — so with `var/`
   missing, `flock` cannot create the lock, exits, and the watchdog never starts.
2. **Invoke it via `/bin/bash`.** The script is not executable; `./assemblage_loop.sh`
   fails with `Permission denied` (exit 69).

Make it survive reboots, and verify after every reboot:

```bash
crontab -e   # add:
@reboot sleep 60 && cd /path/to/Assemblage && \
  /usr/bin/flock -n var/loop.lock /bin/bash ./assemblage_loop.sh >/dev/null 2>&1
```

The `sleep 60` lets dockerd come up; `flock -n` keeps a manual start and the boot
start mutually exclusive.

### Blocklist

`backend/blocklist.txt` — one entry per line; a bare name blocks a whole account,
`owner/name` blocks one repo, `#` comments. Re-read every ~30s, so **edits take
effect without restarting the coordinator** (deliberate: restarting the
coordinator strands the fleet). Filtering happens in SQL during dispatch, so a
blocked repo at the head of a queue cannot wedge that dispatcher.

## 6. Publishing a corpus

Host-side, in order. `export_corpus.py` and `export_sources.py` read MinIO
directly, so point them at the host-published API port (9010).

```bash
set -a; . ./secrets.env; set +a

python backend/scripts/export_corpus.py  --out ./assemblage-rust
python backend/scripts/export_sources.py --src ./assemblage-rust --out ../assemblage-hf
python backend/scripts/pack_repos.py     --src ./assemblage-rust --out ../assemblage-hf
python backend/scripts/upload_hf.py      --folder ../assemblage-hf
```

- `export_corpus.py` pulls binaries, metadata and IR into one directory per
  build, joining `repo_url`/`commit`/`license` from PostgreSQL (builders have no
  DB access, so licenses can only be attached here).
- `export_sources.py` stages the source tree each binary was built from, as a
  sibling `sources/` tree rather than a member of the repo tars — putting it
  inside the tars would invalidate every one of them and force a full re-upload.
- `pack_repos.py` bundles builds into one tar per repository. The flat export is
  ~88k files, which exceeds HuggingFace's request rate limits; tars cut that to
  ~3k. It rebuilds a tar only when its member set changed.
- All stages are resumable and safe to interrupt.

Every stage is license-filtered upstream: copyleft and unidentified-license
repositories never reach the export.

## 7. Failure modes that actually happen

**The fleet is stranded but every container is healthy.** The coordinator starts
a buildopt's dispatch thread only when a builder *registers*. Restart the
coordinator alone — or let it be restarted after a crash — and it comes back with
no dispatchers, while builders keep their existing registrations and never
re-register. Queues drain to zero and nothing refills them. `docker ps` shows a
fully healthy fleet producing nothing.

- *Diagnose:* all `build_opt_*` queues show `consumers>0, messages=0`, and
  `max(build_date)` in `binaries` has stopped advancing.
- *Fix:* `docker compose restart <builder services>` — recovers in under 20s.
- *Never* bounce the coordinator or RabbitMQ for routine hygiene; restart workers
  only.

**Builders crash-loop after a host reboot.** `database` and `rabbitmq` have no
restart policy and stay down; builders are `unless-stopped`, so they restart
forever against a RabbitMQ that isn't there and *look* alive in `docker ps`.

- *Diagnose:* a builder log shows `socket.gaierror: Temporary failure in name resolution`.
- *Fix:* `docker compose up -d --no-recreate database rabbitmq coordinator`;
  builders self-heal within ~60s.

**Builds fail on missing system libraries.** Errors naming `openssl`, `protoc`,
`glib-2.0`, `wayland-client`, `libudev`, `libclang.so`, `cmake`, `alsa` or
`dbus` are image packaging gaps, not bad repositories. Add the package to the
dependency layer in `docker/rust/Dockerfile` and rebuild.

**A builder sits at 100% CPU for tens of minutes.** Almost certainly DWARF
extraction or a Rust IR dump. Extraction time tracks DWARF complexity, not file
size — generic instantiations and inlined frames dominate — so `DWARF_SIZE_LIMIT`
does not bound it. Wall-clock is bounded separately by `DWARF_TIMEOUT_S` /
`DWARF_PHASE_TIMEOUT_S`; on timeout the binary is still stored, only its
metadata is dropped.

**Tasks vanish across a restart.** Builders ack **before** building
(at-most-once, deliberate). Recreating a builder mid-build discards that task
permanently; it is not requeued.

## 8. Tests

```bash
export PATH="$HOME/.local/bin:$PATH"
uv run pytest tests/                     # unit — green by default
uv run pytest tests/ -m integration      # needs: docker compose up -d database
make e2e                                 # golden-repo end-to-end gate
```

Migrations are handwritten and frozen to the live schema — **never commit
`alembic revision --autogenerate` output**; see `backend/alembic/README.md`.
Wire-format JSON is frozen too (`tests/fixtures/messages/`), as are queue names.
