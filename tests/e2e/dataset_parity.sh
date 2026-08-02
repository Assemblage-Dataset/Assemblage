#!/usr/bin/env bash
#
# HISTORICAL — retired as of R5 (2026-07-16). DO NOT RUN as an acceptance gate.
#
# This script proved the P10 DWARF-extractor/S3-layout swap left the daily
# corpus BYTE-IDENTICAL (parity vs the pre-P10 tree). R5 deliberately CHANGES
# that output: the db_construct name-matching fix makes the daily pipeline store
# functions/rvas/lines for the first time (previously zero), and Rust rows add
# demangled_name/origin columns. Parity-vs-HEAD~ would therefore rightly FAIL.
# The R5 acceptance instrument is tests/e2e/dataset_correctness.sh
# (`make dataset-gate`), which asserts the corpus is *correctly populated*
# rather than *unchanged*. This file is kept only for provenance of the P10
# claim; it is not wired into any current gate.
#
# ----------------------------------------------------------------------------
# Dataset parity gate (P10).
#
# Proves that sharing the DWARF extractor (assemblage.dwarf.extract) and the S3
# key layout (assemblage.storage.layout) did NOT change the dataset the daily
# pipeline produces. It brings up the golden-repo E2E stack, runs the dataset
# pipeline twice against the identical stack state — once from the pre-P10 tree
# (old embedded extractor, slash-only download) and once from the current tree
# (shared extractor, flat-key download) — and diffs the results.
#
# The gate has two comparisons, both of which must show an EMPTY diff:
#
#   (A) End-to-end SQLite row-identity. Dumps both linux_licensed.sqlite files
#       with explicit column lists (surrogate ids / timestamps / the derived
#       `length` excluded) and diffs them. This proves the layout change is
#       neutral: PRE reaches the binary via a legacy slash key, POST via the
#       builder's flat key, and the resulting corpus rows are identical.
#
#   (B) Extractor corpus-identity. Runs BOTH DWARF extractors on the E2E ELF and
#       compares function / source_file / RVA-range / line rows directly.
#
# Why (B) exists: db_construct() currently drops every Binary_info_list entry
# because build_staging_entry writes the raw download name (e.g. "42_hello")
# into `file` while db_construct matches against the cleaned staged name
# ("hello") — a PRE-EXISTING defect (both trees; documented in
# backend/assemblage/dataset/README.md, out of P10's approved-delta scope). So
# the SQLite functions/rvas/lines tables are empty in BOTH runs, and (A) alone
# would not exercise the extractor swap at all. (B) closes that gap by comparing
# the extractor output directly — which is exactly the corpus the swap touches.
#
# The only field that legitimately differs between the two extractors is the
# per-line `length` heuristic (embedded: last line of a function = 0; shared:
# gap to the next code address, the builder's convention adopted by the P7
# unification). The E2E golden itself masks `length` as "<len>" for this reason,
# so both comparisons exclude it. `intersect_ratio` also differs ("0.00%" vs
# "0%") but db_construct never stores it.
#
# Re-runnable and self-contained. Usage:
#     tests/e2e/dataset_parity.sh
# Env overrides:
#     PARITY_PRE_COMMIT   git ref of the tree BEFORE this phase (default below)
#     PARITY_PG_PORT      host port for Postgres  (default 55432)
#     PARITY_MINIO_PORT   host port for MinIO     (default 59000)
#     PARITY_KEEP_UP=1    skip teardown (debugging)
#
set -euo pipefail

# --- config -----------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Commit before the first P10 change (the tree that still has the embedded
# extractor and the slash-only download path). Post-P9, so its dataset pipeline
# already lives at backend/assemblage/dataset — no submodule dance is needed.
PARITY_PRE_COMMIT="${PARITY_PRE_COMMIT:-0e5e7d5d36dcd1af128a64ba3655581dc7350039}"
PG_PORT="${PARITY_PG_PORT:-55432}"
MINIO_PORT="${PARITY_MINIO_PORT:-59000}"

COMPOSE=(docker compose -f compose/e2e.yml -f tests/e2e/docker-compose.parity-ports.yml)
PY="$REPO_ROOT/.venv/bin/python"     # the uv venv interpreter (deps only; the
                                     # assemblage package is chosen via PYTHONPATH)

# E2E stack credentials / coordinates (mirror compose/e2e.yml).
export PGHOST=localhost PGPORT="$PG_PORT" PGDATABASE=assemblage \
       PGUSER=assemblage PGPASSWORD=e2e-only
S3_ENDPOINT="localhost:${MINIO_PORT}"
S3_KEY=minioadmin
S3_SECRET=e2e-only-secret

WORK="$(mktemp -d /tmp/parity-work.XXXXXX)"
PRE_TREE="$(mktemp -d /tmp/parity-pre.XXXXXX)"
PRE_DS="$WORK/pre-ds"
POST_DS="$WORK/post-ds"
PRE_DB="$PRE_DS/linux_licensed.sqlite"
POST_DB="$POST_DS/linux_licensed.sqlite"

log() { printf '\n\033[1m[parity] %s\033[0m\n' "$*"; }

cleanup() {
  local rc=$?
  if [[ "${PARITY_KEEP_UP:-0}" != "1" ]]; then
    log "teardown"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    git worktree remove --force "$PRE_TREE" >/dev/null 2>&1 || true
    rm -rf "$WORK"
  else
    log "PARITY_KEEP_UP=1 — leaving stack + $WORK + $PRE_TREE in place"
  fi
  exit "$rc"
}
trap cleanup EXIT

# --- 1. bring the stack up and run the injector once ------------------------
log "bringing up the e2e stack (clean slate)"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d database rabbitmq minio coordinator builder

log "running the injector (its exit code is the E2E gate)"
"${COMPOSE[@]}" run --rm injector

# --- 2. discover the built artifacts and mirror them to legacy slash keys ----
# PRE's download_binary only knows the slash layout, so copy every flat artifact
# object to the legacy key it will request. POST finds the flat key first; both
# then read byte-identical bytes, isolating the extractor as the only variable.
log "mirroring flat artifact objects to legacy slash keys (for the PRE download path)"
MIRROR_JSON="$WORK/mirror.json"
S3_ENDPOINT="$S3_ENDPOINT" S3_KEY="$S3_KEY" S3_SECRET="$S3_SECRET" \
MIRROR_JSON="$MIRROR_JSON" PYTHONPATH="$REPO_ROOT/backend" "$PY" - <<'PY'
import io, json, os
import boto3
import zstandard
from assemblage.storage import layout
from assemblage.dataset.pipeline import parse_github_owner_project
s3 = boto3.client("s3", endpoint_url=f"http://{os.environ['S3_ENDPOINT']}",
                  aws_access_key_id=os.environ["S3_KEY"],
                  aws_secret_access_key=os.environ["S3_SECRET"], region_name="us-east-1")
import psycopg2
conn = psycopg2.connect(host=os.environ["PGHOST"], port=os.environ["PGPORT"],
                        dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
                        password=os.environ["PGPASSWORD"])
cur = conn.cursor()
# The exact rows query_new_binaries selects: linux platform, real file, non-empty sha.
cur.execute("""
    SELECT r.url, LOWER(o.compiler_name), o.compiler_flag, s.commit_hexsha, b.file_name
    FROM binaries b JOIN b_status s ON b.status_id=s.id
    JOIN projects r ON s.repo_id=r.id JOIN buildopt o ON s.build_opt_id=o.id
    WHERE o.platform='linux' AND s.commit_hexsha != ''
""")
elf_objs = []
for url, compiler, flag, sha, file_name in cur.fetchall():
    owner, project = parse_github_owner_project(url)
    base = os.path.basename(file_name)
    # v2 stores compressed under a build dir; the legacy slash mirror below is
    # what the OLD tree reads, so it must be the decompressed ELF.
    v2 = layout.binary_key(
        layout.build_dir(owner, project, sha, flag, compiler, "RelWithDebInfo"), base)
    flat = layout.artifact_key(layout.artifact_prefix(owner, project, sha, compiler, flag), base)
    slash = f"{owner}/{project}/{sha}/{compiler}/{flag}/{base}"
    try:
        s3.head_object(Bucket=layout.ARTIFACTS_BUCKET, Key=v2)
    except Exception:
        try:
            s3.head_object(Bucket=layout.ARTIFACTS_BUCKET, Key=flat)
        except Exception:
            continue  # not every binaries row has an S3 object (e.g. .s intermediates)
        s3.copy_object(Bucket=layout.ARTIFACTS_BUCKET,
                       CopySource={"Bucket": layout.ARTIFACTS_BUCKET, "Key": flat}, Key=slash)
        print(f"  mirrored {flat} -> {slash}")
        if not base.lower().endswith((".s", ".asm")):
            elf_objs.append(flat)
        continue
    body = s3.get_object(Bucket=layout.ARTIFACTS_BUCKET, Key=v2)["Body"].read()
    s3.put_object(Bucket=layout.ARTIFACTS_BUCKET, Key=slash,
                  Body=zstandard.ZstdDecompressor().stream_reader(io.BytesIO(body)).read())
    print(f"  mirrored {v2} -> {slash} (decompressed)")
    flat = v2
    if not base.lower().endswith((".s", ".asm")):
        elf_objs.append(flat)
json.dump(elf_objs, open(os.environ["MIRROR_JSON"], "w"))
PY

# --- 3. run the dataset pipeline from both trees ----------------------------
# A tiny driver that calls run_pipeline directly (the daily runner's core) with
# the e2e stack coordinates. run_pipeline ends by invoking run_assembly_pipeline,
# which trips a pre-existing Dataset_DB.bulk_add_repos bug — irrelevant here
# (identical in both trees, and linux_licensed.sqlite is already flushed), so
# the driver tolerates it and asserts the binaries DB exists.
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

log "PRE  pipeline (worktree @ ${PARITY_PRE_COMMIT:0:12}, embedded extractor)"
git worktree add --force --detach "$PRE_TREE" "$PARITY_PRE_COMMIT" >/dev/null
DWARF_TIMEOUT_SECS=30 PYTHONPATH="$PRE_TREE/backend" "$PY" "$DRIVER" "$PRE_DS"

log "POST pipeline (current tree, shared extractor)"
DWARF_TIMEOUT_SECS=30 PYTHONPATH="$REPO_ROOT/backend" "$PY" "$DRIVER" "$POST_DS"

# --- 4. (A) end-to-end SQLite row-identity ----------------------------------
# Explicit column lists; surrogate ids, filesystem path, and the derived
# `length` excluded. Function / rva / line rows are joined up to the binary's
# content (file_name + hash) so nothing depends on autoincrement ids.
Q_BIN="SELECT file_name,platform,build_mode,toolset_version,github_url,optimization,repo_last_update,size,license,hash,repo_commit,binary_format FROM binaries ORDER BY 1,10;"
Q_FUN="SELECT b.file_name,b.hash,f.name,f.source_file,f.hash FROM functions f JOIN binaries b ON f.binary_id=b.id ORDER BY 1,2,3,4,5;"
Q_RVA="SELECT b.file_name,f.name,f.source_file,r.start,r.end FROM rvas r JOIN functions f ON r.function_id=f.id JOIN binaries b ON f.binary_id=b.id ORDER BY 1,2,3,4,5;"
Q_LIN="SELECT b.file_name,f.name,f.source_file,l.line_number,l.source_file,l.source_code,l.rva FROM lines l JOIN functions f ON l.function_id=f.id JOIN binaries b ON f.binary_id=b.id ORDER BY 1,2,3,4,5,6,7;"

sqlite_rows() { sqlite3 -readonly "$1" "$2"; }

status=0
log "comparison (A): end-to-end SQLite row-identity"
for name in "binaries:$Q_BIN" "functions:$Q_FUN" "rvas:$Q_RVA" "lines:$Q_LIN"; do
  tbl="${name%%:*}"; q="${name#*:}"
  pre_out="$WORK/pre.$tbl.csv"; post_out="$WORK/post.$tbl.csv"
  sqlite_rows "$PRE_DB" "$q"  > "$pre_out"
  sqlite_rows "$POST_DB" "$q" > "$post_out"
  n_pre=$(wc -l < "$pre_out"); n_post=$(wc -l < "$post_out")
  if diff -q "$pre_out" "$post_out" >/dev/null; then
    printf '  %-10s IDENTICAL  (pre=%s post=%s rows)\n' "$tbl" "$n_pre" "$n_post"
  else
    printf '  %-10s DIFFERS    (pre=%s post=%s rows)\n' "$tbl" "$n_pre" "$n_post"
    diff "$pre_out" "$post_out" | head -40
    status=1
  fi
done

# --- 5. (B) extractor corpus-identity ---------------------------------------
log "comparison (B): DWARF extractor corpus-identity (on the E2E ELF)"
S3_ENDPOINT="$S3_ENDPOINT" S3_KEY="$S3_KEY" S3_SECRET="$S3_SECRET" \
MIRROR_JSON="$MIRROR_JSON" WORK="$WORK" PYTHONPATH="$REPO_ROOT/backend" "$PY" - <<'PY'
import json, os
import boto3
elf = json.load(open(os.environ["MIRROR_JSON"]))
s3 = boto3.client("s3", endpoint_url=f"http://{os.environ['S3_ENDPOINT']}",
                  aws_access_key_id=os.environ["S3_KEY"],
                  aws_secret_access_key=os.environ["S3_SECRET"], region_name="us-east-1")
work = os.environ["WORK"]
for i, key in enumerate(elf):
    dest = os.path.join(work, f"elf_{i}")
    s3.download_file("artifacts", key, dest)
    with open(os.path.join(work, "elf_manifest.txt"), "a") as fh:
        fh.write(f"{i}\t{key}\n")
print(f"downloaded {len(elf)} ELF object(s) for extractor comparison")
PY

# Corpus-identity fields only (drop the golden-masked `length` and the unstored
# `intersect_ratio`); run each extractor via its own tree on PYTHONPATH.
CMP="$WORK/extract_and_norm.py"
cat > "$CMP" <<'PYEOF'
import json, os, sys
os.environ.setdefault("DWARF_TIMEOUT_SECS", "30")
from assemblage.dataset.pipeline import extract_dwarf_info
def norm(item):
    if not item:
        return []
    out = []
    for f in sorted(item["functions"], key=lambda x: (x["function_name"], x["source_file"])):
        out.append({
            "function_name": f["function_name"],
            "source_file": f["source_file"],
            "function_info": f["function_info"],
            "lines": [{k: ln[k] for k in ("line_number", "rva", "source_code", "source_file")}
                      for ln in f["lines"]],
        })
    return out
result = {}
work = sys.argv[1]
for line in open(os.path.join(work, "elf_manifest.txt")):
    i, key = line.rstrip("\n").split("\t")
    result[key] = norm(extract_dwarf_info(os.path.join(work, f"elf_{i}")))
json.dump(result, open(sys.argv[2], "w"), indent=1, sort_keys=True)
PYEOF

DWARF_TIMEOUT_SECS=30 PYTHONPATH="$PRE_TREE/backend" "$PY" "$CMP" "$WORK" "$WORK/pre.extract.json"
DWARF_TIMEOUT_SECS=30 PYTHONPATH="$REPO_ROOT/backend" "$PY" "$CMP" "$WORK" "$WORK/post.extract.json"

n_fun=$("$PY" - "$WORK/post.extract.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(sum(len(v) for v in d.values()))
PY
)
if diff -q "$WORK/pre.extract.json" "$WORK/post.extract.json" >/dev/null; then
  printf '  extractor  IDENTICAL  (%s function rows across %s ELF object(s))\n' \
         "$n_fun" "$(wc -l < "$WORK/elf_manifest.txt")"
else
  printf '  extractor  DIFFERS\n'
  diff "$WORK/pre.extract.json" "$WORK/post.extract.json" | head -60
  status=1
fi

# --- verdict ----------------------------------------------------------------
echo
if [[ "$status" -eq 0 ]]; then
  log "DATASET PARITY GATE PASSED — dataset output is row-identical before/after"
else
  log "DATASET PARITY GATE FAILED"
fi
exit "$status"
