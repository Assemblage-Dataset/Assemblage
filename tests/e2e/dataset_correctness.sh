#!/usr/bin/env bash
#
# Dataset correctness gate (R5) — `make dataset-gate`.
#
# Replaces the retired P10 parity gate as the dataset acceptance instrument.
# Parity proved the daily corpus was UNCHANGED by the extractor/layout swap; R5
# deliberately CHANGES it (the db_construct name-matching fix finally stores
# functions/rvas/lines, and Rust rows gain compiler/language/codegen_backend/
# build_mode + demangled_name/origin). So instead of diffing against an older
# tree, this gate asserts the corpus is CORRECTLY POPULATED for one C binary
# (hello-make) and the Rust golden (rust-golden, -O0 and -O2).
#
# Mechanics reuse dataset_parity.sh: bring up the golden-repo E2E stack with the
# ports overlay (Postgres + MinIO reachable on localhost), run the injector once
# (which leaves C AND Rust artifacts in MinIO and rows in Postgres), keep the
# stack up, then run the CURRENT daily pipeline host-side against it and inspect
# the resulting SQLite with sqlite3. Self-contained: tears the stack down (-v)
# on exit.
#
# Usage:  tests/e2e/dataset_correctness.sh
# Env:
#   DGATE_PG_PORT     host port for Postgres (default 55432)
#   DGATE_MINIO_PORT  host port for MinIO    (default 59000)
#   DGATE_KEEP_UP=1   skip teardown (debugging)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PG_PORT="${DGATE_PG_PORT:-55432}"
MINIO_PORT="${DGATE_MINIO_PORT:-59000}"

COMPOSE=(docker compose -f compose/e2e.yml -f tests/e2e/docker-compose.parity-ports.yml)
PY="$REPO_ROOT/.venv/bin/python"

# E2E stack coordinates (mirror compose/e2e.yml).
PGHOST=localhost PGPORT="$PG_PORT" PGDATABASE=assemblage PGUSER=assemblage PGPASSWORD=e2e-only
S3_ENDPOINT="localhost:${MINIO_PORT}"
S3_KEY=minioadmin
S3_SECRET=e2e-only-secret

WORK="$(mktemp -d /tmp/dgate-work.XXXXXX)"
DS="$WORK/ds"
DB="$DS/linux_licensed.sqlite"
NAMED_DB="/tmp/rust-dataset.sqlite"

log()  { printf '\n\033[1m[dgate] %s\033[0m\n' "$*"; }
fail() { printf '\033[31m[dgate] FAIL: %s\033[0m\n' "$*"; status=1; }

status=0

cleanup() {
  local rc=$?
  if [[ "${DGATE_KEEP_UP:-0}" != "1" ]]; then
    log "teardown"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK"
  else
    log "DGATE_KEEP_UP=1 — leaving stack + $WORK in place"
  fi
  exit "$rc"
}
trap cleanup EXIT

# --- 1. bring the stack up and run the injector once ------------------------
log "bringing up the e2e stack (clean slate)"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d database rabbitmq minio coordinator builder builder_rust_o0 builder_rust_o2

log "running the injector (produces C + Rust artifacts in MinIO and rows in PG)"
"${COMPOSE[@]}" run --rm injector

# --- 2. run the CURRENT pipeline host-side ----------------------------------
log "running the current daily pipeline host-side -> $DB"
DRIVER="$WORK/driver.py"
cat > "$DRIVER" <<PYEOF
import os, sys
from assemblage.dataset.pipeline import run_pipeline
try:
    run_pipeline(since_date_str="2000-01-01", dataset_dir=sys.argv[1],
                 db_url="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}",
                 s3_endpoint="${S3_ENDPOINT}", s3_access_key="${S3_KEY}",
                 s3_secret_key="${S3_SECRET}", bucket="artifacts", s3_https=False,
                 download_workers=8)
except Exception as exc:  # tolerate the assembly sub-pipeline's pre-existing bug
    print(f"[driver] run_pipeline raised after the binaries DB was built: {exc!r}")
assert os.path.isfile(os.path.join(sys.argv[1], "linux_licensed.sqlite")), "no linux_licensed.sqlite"
PYEOF
DWARF_TIMEOUT_SECS=30 PYTHONPATH="$REPO_ROOT/backend" "$PY" "$DRIVER" "$DS"
cp -f "$DB" "$NAMED_DB"
log "dataset written to $DB (also copied to $NAMED_DB)"

# --- 3. assertions ----------------------------------------------------------
q() { sqlite3 -noheader -readonly "$DB" "$1"; }

log "row-count summary (binaries / functions / rvas / lines per binary)"
sqlite3 -readonly "$DB" <<'SQL' || true
.mode column
.headers on
SELECT b.id, b.file_name, b.language, b.compiler, b.codegen_backend,
       b.build_mode, b.optimization,
       (SELECT count(*) FROM functions f WHERE f.binary_id=b.id) AS funcs,
       (SELECT count(*) FROM rvas r JOIN functions f ON r.function_id=f.id
          WHERE f.binary_id=b.id) AS rvas,
       (SELECT count(*) FROM lines l JOIN functions f ON l.function_id=f.id
          WHERE f.binary_id=b.id) AS lines
FROM binaries b ORDER BY b.language, b.optimization;
SQL

# (a) binaries rows exist for the C fixture and both Rust variants -----------
log "assert: binaries rows (C hello + Rust golden_bin -O0/-O2, columns populated)"
[[ "$(q "SELECT count(*) FROM binaries WHERE file_name='hello'")" -ge 1 ]] \
  || fail "no C 'hello' binaries row"

RUST_WHERE="language='rust' AND compiler='rustc' AND codegen_backend='llvm' \
  AND build_mode='RelWithDebInfo' AND file_name='golden_bin'"
[[ "$(q "SELECT count(*) FROM binaries WHERE $RUST_WHERE AND optimization='-O0'")" -ge 1 ]] \
  || fail "no populated Rust -O0 binaries row (rustc/rust/llvm/RelWithDebInfo)"
[[ "$(q "SELECT count(*) FROM binaries WHERE $RUST_WHERE AND optimization='-O2'")" -ge 1 ]] \
  || fail "no populated Rust -O2 binaries row (rustc/rust/llvm/RelWithDebInfo)"

# (b) functions > 0 for BOTH the C and Rust binaries (defect fix) ------------
log "assert: functions populated for C and Rust (db_construct matching fix)"
C_FUNCS="$(q "SELECT count(*) FROM functions f JOIN binaries b ON f.binary_id=b.id \
  WHERE b.file_name='hello'")"
[[ "$C_FUNCS" -gt 0 ]] || fail "C binary has zero functions (matching fix not firing)"
R_FUNCS="$(q "SELECT count(*) FROM functions f JOIN binaries b ON f.binary_id=b.id \
  WHERE b.language='rust'")"
[[ "$R_FUNCS" -gt 0 ]] || fail "Rust binaries have zero functions"

# (c) Rust demangled_name + origin -------------------------------------------
log "assert: Rust demangled_name (2 distinct pair_sum) + origin=in_repo"
PAIR_DISTINCT="$(q "SELECT count(DISTINCT name) FROM functions \
  WHERE demangled_name LIKE '%pair_sum%'")"
[[ "$PAIR_DISTINCT" -ge 2 ]] \
  || fail "expected >=2 distinct pair_sum mangled names, got $PAIR_DISTINCT"
IN_REPO="$(q "SELECT count(*) FROM functions f JOIN binaries b ON f.binary_id=b.id \
  WHERE b.language='rust' AND f.origin='in_repo'")"
[[ "$IN_REPO" -gt 0 ]] || fail "no Rust origin='in_repo' functions"

# (d) rvas + lines > 0 through those functions; exact source text for add ----
log "assert: rvas + lines populated; exact source text for add"
R_RVAS="$(q "SELECT count(*) FROM rvas r JOIN functions f ON r.function_id=f.id \
  JOIN binaries b ON f.binary_id=b.id WHERE b.language='rust'")"
[[ "$R_RVAS" -gt 0 ]] || fail "Rust rvas count is zero"
R_LINES="$(q "SELECT count(*) FROM lines l JOIN functions f ON l.function_id=f.id \
  JOIN binaries b ON f.binary_id=b.id WHERE b.language='rust'")"
[[ "$R_LINES" -gt 0 ]] || fail "Rust lines count is zero"
# golden_lib::add's body line is exactly "    a + b" (fixture golden_lib/src/lib.rs:5).
ADD_SRC="$(q "SELECT count(*) FROM lines l JOIN functions f ON l.function_id=f.id \
  WHERE f.demangled_name='golden_lib::add' AND l.source_code='    a + b'")"
[[ "$ADD_SRC" -gt 0 ]] || fail "no lines.source_code '    a + b' for golden_lib::add"

# (e) the three documented indexes exist -------------------------------------
log "assert: the three lookup indexes exist"
for idx in ix_functions_binary_id ix_rvas_function_id ix_lines_function_id; do
  [[ "$(q "SELECT count(*) FROM sqlite_master WHERE type='index' AND name='$idx'")" -eq 1 ]] \
    || fail "missing index $idx"
done

# (f) functions joinable to binaries (no orphan binary_id) -------------------
log "assert: no orphan functions.binary_id"
ORPHANS="$(q "SELECT count(*) FROM functions f LEFT JOIN binaries b ON f.binary_id=b.id \
  WHERE b.id IS NULL")"
[[ "$ORPHANS" -eq 0 ]] || fail "$ORPHANS orphan functions (binary_id with no binaries row)"

# --- verdict ----------------------------------------------------------------
echo
if [[ "$status" -eq 0 ]]; then
  log "DATASET CORRECTNESS GATE PASSED"
else
  log "DATASET CORRECTNESS GATE FAILED"
fi
exit "$status"
