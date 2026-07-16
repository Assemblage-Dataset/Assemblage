"""
minio_pipeline.py

Daily dataset pipeline: fetches new licensed Linux binaries from MinIO + PostgreSQL,
re-extracts DWARF debug info, constructs metadata in the format expected by db_construct(),
then appends to the cumulative linux_licensed.sqlite and stores each day's raw binaries
under assemblage_dataset/{date}/binaries/.

Usage:
    python minio_pipeline.py --since 2026-03-08 --dataset-dir /path/to/assemblage_dataset \
        --db-url postgresql://... --s3-endpoint http://localhost:9000 \
        --s3-access-key ... --s3-secret-key ... [--bucket artifacts]
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import boto3
import botocore
from botocore.client import Config
from sqlalchemy import create_engine, text

# dataset_utils lives in the same directory
from assemblage.dataset.construct import METAFILE, db_construct
from assemblage.dataset.orm import init_clean_database, migrate_existing_db
from assemblage.dataset.store import Dataset_DB
from assemblage.dwarf.extract import extract_dwarf_info as _shared_extract_dwarf_info
from assemblage.storage import layout

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DWARF extraction
# ---------------------------------------------------------------------------

# Per-binary DWARF extraction timeout in seconds (0 = skip DWARF entirely).
# Default 30s: a lower value causes most non-trivial binaries to time out and
# persist with empty DWARF.
DWARF_TIMEOUT_SECS = int(os.environ.get("DWARF_TIMEOUT_SECS", "30"))


def extract_dwarf_info(binfile, source_root=None):
    """Extract one Binary_info_list item from an ELF binary.

    Thin wrapper over the shared ``assemblage.dwarf.extract`` extractor (the one
    the builder uses) that preserves the daily pipeline's DWARF_TIMEOUT_SECS
    contract: ``0`` disables extraction entirely (return ``None``); any positive
    value bounds the whole extraction with a SIGALRM wall-clock timeout. The
    embedded ~200-line copy this replaces was a drift-prone fork of the same
    build_method extractor.
    """
    if DWARF_TIMEOUT_SECS == 0:
        return None  # DWARF extraction disabled
    return _shared_extract_dwarf_info(
        binfile, source_root=source_root, timeout_secs=DWARF_TIMEOUT_SECS
    )


# ---------------------------------------------------------------------------
# PostgreSQL query
# ---------------------------------------------------------------------------

NEW_ASSEMBLY_QUERY = text("""
    SELECT
        b.id                        AS binary_id,
        b.file_name,
        r.url                       AS repo_url,
        r.name                      AS repo_name,
        r.description               AS repo_description,
        r.language                  AS repo_language,
        r.build_system,
        r.created_at                AS repo_created_at,
        r.size                      AS repo_size_kb,
        s.commit_hexsha,
        LOWER(opt.compiler_name)    AS compiler,
        opt.compiler_flag
    FROM binaries b
    JOIN b_status s   ON b.status_id    = s.id
    JOIN projects r   ON s.repo_id      = r.id
    JOIN buildopt opt ON s.build_opt_id = opt.id
    WHERE b.build_date > :since
      AND opt.platform = 'linux'
      AND (b.file_name LIKE '%%.s'
           OR b.file_name LIKE '%%.S'
           OR b.file_name LIKE '%%.asm')
      AND b.file_name NOT LIKE '%%CompilerId%%'
      AND b.file_name NOT LIKE '%%CMakeDetermineCompiler%%'
      AND s.commit_hexsha != ''
    ORDER BY b.build_date ASC
""")


NEW_BINARIES_QUERY = text("""
    SELECT
        b.id                        AS binary_id,
        b.file_name,
        b.build_date,
        r.url                       AS repo_url,
        s.commit_hexsha,
        r.updated_at,
        LOWER(opt.compiler_name)    AS compiler,
        opt.library                 AS arch,
        opt.platform,
        r.name                      AS repo_name,
        r.description               AS repo_description,
        r.language                  AS repo_language,
        r.build_system,
        r.created_at                AS repo_created_at,
        r.size                      AS repo_size_kb,
        s.build_time,
        opt.compiler_flag,
        opt.language                AS opt_language,
        opt.codegen_backend,
        opt.build_type
    FROM binaries b
    JOIN b_status s   ON b.status_id    = s.id
    JOIN projects r   ON s.repo_id      = r.id
    JOIN buildopt opt ON s.build_opt_id = opt.id
    WHERE b.build_date > :since
      AND opt.platform = 'linux'
      AND b.file_name NOT LIKE '%%.bc'
      AND b.file_name NOT LIKE '%%.ii'
      AND b.file_name NOT LIKE '%%.o'
      AND b.file_name NOT LIKE '%%.a'
      AND b.file_name NOT LIKE '%%.json'
      AND b.file_name NOT LIKE '%%CMakeDetermineCompiler%%'
      AND b.file_name NOT LIKE '%%CompilerId%%'
      AND s.commit_hexsha != ''
    ORDER BY b.build_date ASC
""")


def query_new_binaries(db_url, since_dt):
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(NEW_BINARIES_QUERY, {"since": since_dt}).fetchall()
    return [dict(row._mapping) for row in rows]


def query_new_assembly(db_url, since_dt, limit=0):
    engine = create_engine(db_url)
    q = NEW_ASSEMBLY_QUERY
    if limit > 0:
        q = text(str(NEW_ASSEMBLY_QUERY.text) + f" LIMIT {int(limit)}")
    with engine.connect() as conn:
        rows = conn.execute(q, {"since": since_dt}).fetchall()
    return [dict(row._mapping) for row in rows]


# ---------------------------------------------------------------------------
# MinIO download
# ---------------------------------------------------------------------------


def make_s3_client(endpoint, access_key, secret_key, https=False):
    scheme = "https" if https else "http"
    endpoint_url = f"{scheme}://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def parse_github_owner_project(url):
    """Extract owner and project name from a GitHub URL (cloneable or API form)."""
    parts = url.rstrip("/").split("/")
    return parts[-2], parts[-1]


_FLAG_TO_OLD_ENUM = {"-O0": "NONE", "-O1": "LOW", "-O2": "MEDIUM", "-O3": "HIGH"}


def is_rust_row(row):
    """A build row is Rust when its buildopt language is rust (equivalently, its
    compiler is rustc). Both are set together at registration, so either alone
    is authoritative; we check both so a partially-populated row still routes."""
    return (row.get("opt_language") or "").lower() == "rust" or (
        row.get("compiler") or ""
    ).lower() == "rustc"


def download_binary(
    s3,
    bucket,
    repo_url,
    commit_hexsha,
    compiler,
    opt_enum,
    filename,
    dest_path,
    *,
    codegen_backend=None,
    build_mode=None,
    language=None,
):
    owner, project = parse_github_owner_project(repo_url)
    basename = os.path.basename(filename)
    # The builder's real key layout (storage.layout, the one source of truth)
    # comes first; the slash-separated candidates below match historical objects
    # written before the builder switched to the flat prefix.
    candidates = []
    # Rust artifacts live under a backend/mode-qualified prefix (frozen in
    # storage.layout), so the Rust key is tried first for rust builds. The C
    # candidates below stay as a fallback and keep C/C++ behaviour unchanged.
    if (language or "").lower() == "rust":
        rust_prefix = layout.rust_artifact_prefix(
            owner, project, commit_hexsha, codegen_backend or "", build_mode or "", opt_enum
        )
        candidates.append(layout.artifact_key(rust_prefix, basename))
    prefix = layout.artifact_prefix(owner, project, commit_hexsha, compiler, opt_enum)
    old_name = _FLAG_TO_OLD_ENUM.get(opt_enum, "")
    candidates += [
        layout.artifact_key(prefix, basename),
        f"{owner}/{project}/{commit_hexsha}/{compiler}/{opt_enum}/{basename}",
    ]
    if old_name:
        candidates.append(f"{owner}/{project}/{commit_hexsha}/{compiler}/opt_{old_name}/{basename}")
    candidates.append(f"{owner}/{project}/{commit_hexsha}/{compiler}/opt_{opt_enum}/{basename}")
    for s3_key in candidates:
        try:
            s3.download_file(bucket, s3_key, dest_path)
            return True
        except botocore.exceptions.ClientError:
            continue
    logger.warning(
        "Failed to download s3://%s/%s (tried %d paths)", bucket, candidates[0], len(candidates)
    )
    return False


def download_rust_binary_info_list(s3, bucket, row):
    """Fetch a Rust build's ``Binary_info_list`` from the builder-written
    ``assemblage_meta.json``.

    Rust functions carry a ``demangled_name`` (rustfilt, v0) and an ``origin``
    (in_repo / dependency / stdlib) that only the builder can produce — it has
    the Rust toolchain and the live clone. Re-running the host-side DWARF
    extractor would recover mangled names, ranges and lines but neither of those
    two, so for Rust we reuse the builder's already-extracted per-binary entries
    verbatim (a passthrough, not new extraction). Returns the list of entries,
    or ``None`` if the metadata object is missing/unreadable.
    """
    owner, project = parse_github_owner_project(row["repo_url"])
    prefix = layout.rust_artifact_prefix(
        owner,
        project,
        row["commit_hexsha"] or "",
        row.get("codegen_backend") or "",
        row.get("build_type") or "",
        row.get("compiler_flag", "") or "",
    )
    key = layout.metadata_key(prefix)
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        meta = json.loads(body)
    except (botocore.exceptions.ClientError, ValueError) as exc:
        logger.warning("Rust metadata unavailable s3://%s/%s : %s", bucket, key, exc)
        return None
    return meta.get("Binary_info_list") or []


def download_source_archive(s3, owner, project, commit_hexsha, dest_path):
    """
    Download the source archive for a repo+commit from the project-archive bucket.
    Key format (storage.layout): {owner}/{project}/{commit_hexsha}.tar.gz
    Returns True on success, False on failure.
    """
    s3_key = layout.archive_key(owner, project, commit_hexsha)
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        s3.download_file(layout.PROJECT_ARCHIVE_BUCKET, s3_key, dest_path)
        logger.info("Downloaded source archive s3://project-archive/%s -> %s", s3_key, dest_path)
        return True
    except botocore.exceptions.ClientError as e:
        logger.info("Source archive not available s3://project-archive/%s : %s", s3_key, e)
        return False


# ---------------------------------------------------------------------------
# Staging directory construction
# ---------------------------------------------------------------------------


def get_md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def build_staging_entry(row, binary_path, staging_dir, source_root=None, binary_info_list=None):
    """
    Place the binary and a generated assemblage_meta.json into a staging
    sub-directory in the format db_construct() expects:
        staging/{identifier}/
            assemblage_meta.json
            {identifier}_{filename}

    `source_root`, if provided, is the path to the extracted source tree for
    this binary's repo (used so DWARF source_file paths can be resolved and
    `lines.source_code` populated).

    `binary_info_list`, if provided, supplies the per-binary DWARF entries
    directly (the builder-written ones, used for Rust — they already carry
    demangled_name/origin/resolved source). When None the daily pipeline
    re-extracts DWARF from `binary_path` itself (the C/C++ path, unchanged).
    """
    repo_url = row["repo_url"]
    compiler = row["compiler"]
    compiler_flag = row.get("compiler_flag", "") or ""
    arch = row["arch"] or "x64"
    commit_hexsha = row["commit_hexsha"] or ""
    language = row.get("opt_language") or row.get("repo_language") or ""
    codegen_backend = row.get("codegen_backend") or ""
    build_mode = row.get("build_type") or ""
    filename = os.path.basename(row["file_name"])

    updated_at = row["updated_at"]
    if hasattr(updated_at, "strftime"):
        pushed_at_str = updated_at.strftime("%m/%d/%Y, %H:%M:%S")
    else:
        pushed_at_str = str(updated_at)

    identifier = f"{get_md5(repo_url)}_{arch}_{compiler}_{compiler_flag}"
    ident_dir = os.path.join(staging_dir, identifier)
    os.makedirs(ident_dir, exist_ok=True)

    # DWARF entries: for Rust use the builder's already-extracted entries
    # (demangled_name/origin/source resolved in the builder); for C/C++
    # re-extract from the downloaded binary (only meaningful for ELF files).
    if binary_info_list is not None:
        dwarf_items = list(binary_info_list)
    else:
        one = extract_dwarf_info(binary_path, source_root=source_root)
        dwarf_items = [one] if one is not None else []

    meta_path = os.path.join(ident_dir, METAFILE)

    # Determine artifact_type based on file extension
    ext = os.path.splitext(filename)[1].lower()
    artifact_type_map = {
        ".s": "assembly",
        ".S": "assembly",
        ".bc": "llvm_ir",
        ".ii": "preprocessed",
        ".i": "preprocessed",
    }
    artifact_type = artifact_type_map.get(ext, "binary")

    # Read existing meta if present (we accumulate Binary_info_list across
    # multiple binaries that share the same identifier dir), or build a new
    # one. db_construct() reads `Binary_info_list` directly from this metafile
    # — the previous design wrote DWARF to a sidecar `.dwarf.json` that
    # db_construct silently ignored, so all daily-pipeline DWARF data was
    # being discarded.
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    else:
        meta = {}

    if not meta:
        meta = {
            "Platform": arch,
            "Compiler": compiler,
            "URL": repo_url,
            "Compiler_flag": compiler_flag,
            "Build_mode": build_mode,
            "Language": language,
            "Codegen_backend": codegen_backend,
            "Pushed_at": pushed_at_str,
            "commit_sha": commit_hexsha,
            "Repo_name": row.get("repo_name", "") or "",
            "Repo_description": row.get("repo_description", "") or "",
            "Repo_language": row.get("repo_language", "") or "",
            "Build_system": row.get("build_system", "") or "",
            "Repo_created_at": str(row.get("repo_created_at", "")) or "",
            "Repo_size_kb": row.get("repo_size_kb", 0) or 0,
            "Build_time": row.get("build_time", 0) or 0,
            "Artifact_type": artifact_type,
        }

    # Append this binary's DWARF entries into Binary_info_list.
    existing = meta.setdefault("Binary_info_list", [])
    seen_files = {entry.get("file") for entry in existing}
    for item in dwarf_items:
        # de-dup by file name in case the same binary is staged twice
        if item.get("file") not in seen_files:
            existing.append(item)
            seen_files.add(item.get("file"))

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Binary name must match the identifier prefix pattern used by db_construct
    dest_bin = os.path.join(ident_dir, f"{identifier}_{filename}")
    shutil.copy2(binary_path, dest_bin)

    return ident_dir


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _download_asm_one(args):
    """Download a single assembly file. Thread worker."""
    row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir = args
    s3 = make_s3_client(s3_endpoint, s3_access_key, s3_secret_key, s3_https)

    filename = os.path.basename(row["file_name"])
    raw_path = os.path.join(download_dir, f"{row['binary_id']}_{filename}")

    if os.path.exists(raw_path):
        return ("cached", row, raw_path)

    ok = download_binary(
        s3,
        bucket,
        repo_url=row["repo_url"],
        commit_hexsha=row["commit_hexsha"],
        compiler=row["compiler"],
        opt_enum=row.get("compiler_flag", ""),
        filename=filename,
        dest_path=raw_path,
    )
    if not ok:
        return ("fail", row, None)

    return ("ok", row, raw_path)


def run_assembly_pipeline(
    since_date_str,
    dataset_dir,
    db_url,
    s3_endpoint,
    s3_access_key,
    s3_secret_key,
    bucket="artifacts",
    s3_https=False,
    download_workers=32,
    limit=0,
):
    """Download assembly files from MinIO and record them in assembly.sqlite."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    since_dt = datetime.datetime.strptime(since_date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    today_str = datetime.date.today().isoformat()

    dataset_dir = Path(dataset_dir)
    asm_dir = dataset_dir / today_str / "assembly"
    asm_dir.mkdir(parents=True, exist_ok=True)

    sqlite_path = str(dataset_dir / "assembly.sqlite")

    logger.info("[asm] Querying assembly files since %s ...", since_date_str)
    rows = query_new_assembly(db_url, since_dt, limit=limit)
    logger.info("[asm] Found %d assembly files", len(rows))

    if not rows:
        logger.info("[asm] Nothing to do.")
        return

    # Init or migrate the assembly SQLite DB
    if not os.path.exists(sqlite_path):
        init_clean_database(f"sqlite:///{sqlite_path}")
    migrate_existing_db(sqlite_path)

    db = Dataset_DB(f"sqlite:///{sqlite_path}")

    # Ensure repos exist first, build url->id map
    repo_map = {}  # github_url -> repo_id
    repo_ds = {}
    for row in rows:
        url = row["repo_url"]
        if url not in repo_ds:
            repo_ds[url] = {
                "github_url": url,
                "name": row.get("repo_name", "") or "",
                "description": row.get("repo_description", "") or "",
                "language": row.get("repo_language", "") or "",
                "build_system": row.get("build_system", "") or "",
                "license": "",
                "created_at": str(row.get("repo_created_at", "")) or "",
                "size_kb": row.get("repo_size_kb", 0) or 0,
                "first_seen": today_str,
            }
    db.bulk_add_repos(list(repo_ds.values()))

    # Build url -> repo_id lookup
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(sqlite_path)
    cursor = conn.cursor()
    for url in repo_ds:
        rid = cursor.execute("SELECT id FROM repos WHERE github_url = ?", (url,)).fetchone()
        if rid:
            repo_map[url] = rid[0]
    conn.close()

    # Download assembly files
    download_dir = str(dataset_dir / today_str / "raw_asm")
    os.makedirs(download_dir, exist_ok=True)

    work_args = [
        (row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir)
        for row in rows
    ]

    downloaded = 0
    failed = 0
    total = len(rows)
    downloaded_rows = []

    logger.info("[asm] Downloading %d files with %d threads...", total, download_workers)
    with ThreadPoolExecutor(max_workers=download_workers) as pool:
        futures = {pool.submit(_download_asm_one, a): a[0] for a in work_args}
        for future in as_completed(futures):
            try:
                status, row, raw_path = future.result()
            except Exception as e:
                failed += 1
                if failed <= 5:
                    logger.error("[asm] Worker exception: %s", e)
                continue
            if status in ("ok", "cached"):
                downloaded += 1
                downloaded_rows.append((row, raw_path))
            else:
                failed += 1

    logger.info("[asm] Downloads done: %d ok, %d failed", downloaded, failed)

    # Move to hash-based layout and record in DB
    asm_records = []
    for row, raw_path in downloaded_rows:
        repo_id = repo_map.get(row["repo_url"])
        if repo_id is None:
            continue
        filename = os.path.basename(raw_path)
        # hash-based subdirectory
        fhash = hashlib.md5(filename.encode()).hexdigest()
        sub = os.path.join(fhash[0:2], fhash[2:4])
        dest_dir = asm_dir / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        shutil.copy2(raw_path, str(dest_path))

        rel_path = str(dest_path.relative_to(dataset_dir))
        asm_records.append({"repo_id": repo_id, "path": rel_path})

        if len(asm_records) >= 1000:
            db.bulk_add_assembly_files(asm_records)
            asm_records = []

    db.bulk_add_assembly_files(asm_records)
    db.shutdown()

    logger.info("[asm] Done. %d assembly files recorded in %s", downloaded, sqlite_path)


def _download_one(args):
    """Download a single binary. Thread worker function (no DWARF — signal unsafe in threads)."""
    row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir = args
    s3 = make_s3_client(s3_endpoint, s3_access_key, s3_secret_key, s3_https)

    filename = os.path.basename(row["file_name"])
    raw_path = os.path.join(download_dir, f"{row['binary_id']}_{filename}")

    if os.path.exists(raw_path):
        return ("cached", row, raw_path)

    ok = download_binary(
        s3,
        bucket,
        repo_url=row["repo_url"],
        commit_hexsha=row["commit_hexsha"],
        compiler=row["compiler"],
        opt_enum=row.get("compiler_flag", ""),
        filename=filename,
        dest_path=raw_path,
        codegen_backend=row.get("codegen_backend"),
        build_mode=row.get("build_type"),
        language="rust" if is_rust_row(row) else row.get("opt_language"),
    )
    if not ok:
        return ("fail", row, None)

    return ("ok", row, raw_path)


def run_pipeline(
    since_date_str,
    dataset_dir,
    db_url,
    s3_endpoint,
    s3_access_key,
    s3_secret_key,
    bucket="artifacts",
    s3_https=False,
    download_workers=32,
):

    from concurrent.futures import ThreadPoolExecutor, as_completed

    since_dt = datetime.datetime.strptime(since_date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    today_str = datetime.date.today().isoformat()

    dataset_dir = Path(dataset_dir)
    date_dir = dataset_dir / today_str
    binaries_dir = date_dir / "binaries"
    binaries_dir.mkdir(parents=True, exist_ok=True)

    sqlite_path = str(dataset_dir / "linux_licensed.sqlite")

    logger.info("Querying new binaries since %s ...", since_date_str)
    rows = query_new_binaries(db_url, since_dt)
    logger.info("Found %d new binaries", len(rows))

    if not rows:
        logger.info("Nothing to do.")
        return

    staging_dir = str(date_dir / "staging")
    os.makedirs(staging_dir, exist_ok=True)

    download_dir = str(date_dir / "raw")
    os.makedirs(download_dir, exist_ok=True)

    archives_dir = str(date_dir / "archives")
    os.makedirs(archives_dir, exist_ok=True)

    downloaded = 0
    failed = 0
    total = len(rows)

    logger.info("Downloading with %d threads...", download_workers)

    work_args = [
        (row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir)
        for row in rows
    ]

    # Phase 1: Parallel downloads (no DWARF — signals not safe in threads)
    downloaded_rows = []  # (row, raw_path) pairs for staging
    with ThreadPoolExecutor(max_workers=download_workers) as pool:
        futures = {pool.submit(_download_one, a): a[0] for a in work_args}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 2000 == 0:
                logger.info(
                    "Downloaded %d/%d files (%.1f%%) ok=%d failed=%d",
                    done_count,
                    total,
                    100.0 * done_count / total,
                    downloaded,
                    failed,
                )
            try:
                status, row, raw_path = future.result()
            except Exception as e:
                failed += 1
                if failed <= 5:
                    logger.error("Worker exception: %s", e)
                continue

            if status in ("ok", "cached"):
                downloaded += 1
                downloaded_rows.append((row, raw_path))
            else:
                failed += 1

    logger.info("Downloads done: %d ok, %d failed. Now staging...", downloaded, failed)

    # Phase 2: Sequential staging with DWARF extraction (main thread, signal-safe)
    staged = 0
    downloaded_archives = set()
    s3_arc = make_s3_client(s3_endpoint, s3_access_key, s3_secret_key, s3_https)
    for idx, (row, raw_path) in enumerate(downloaded_rows, 1):
        if idx % 2000 == 0:
            logger.info(
                "Staging %d/%d (%.1f%%)",
                idx,
                len(downloaded_rows),
                100.0 * idx / len(downloaded_rows),
            )

        try:
            # Rust: reuse the builder's per-binary DWARF entries (with
            # demangled_name/origin) instead of re-extracting host-side.
            binary_info_list = None
            if is_rust_row(row):
                binary_info_list = download_rust_binary_info_list(s3_arc, bucket, row)
            build_staging_entry(row, raw_path, staging_dir, binary_info_list=binary_info_list)
            staged += 1
        except Exception as e:
            if staged == 0:
                logger.error("Staging error: %s", e)
            continue

        # Download source archive once per (repo_url, commit_hexsha)
        archive_key = (row["repo_url"], row["commit_hexsha"])
        if archive_key not in downloaded_archives:
            downloaded_archives.add(archive_key)
            owner, project = parse_github_owner_project(row["repo_url"])
            commit_hexsha = row["commit_hexsha"] or ""
            if commit_hexsha:
                archive_dest = os.path.join(archives_dir, owner, project, f"{commit_hexsha}.tar.gz")
                download_source_archive(s3_arc, owner, project, commit_hexsha, archive_dest)

    logger.info("Staged %d/%d binaries for db_construct() (failed_dl=%d)", staged, total, failed)

    if staged == 0:
        logger.info("No binaries staged; skipping db_construct.")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return

    # Bring an existing cumulative DB up to the current schema BEFORE inserting
    # (older files predate the Rust columns; db_construct now writes them, so the
    # inserts would fail on a stale schema).
    if os.path.exists(sqlite_path):
        migrate_existing_db(sqlite_path)

    logger.info("Running db_construct() -> %s", sqlite_path)
    db_construct(
        dbfile=sqlite_path,
        target_dir=staging_dir,
        include_lines=True,
        include_functions=True,
        include_rvas=True,
        include_pdbs=False,
    )

    # db_construct creates a fresh DB via create_all(), which does not build the
    # imperatively-defined lookup indexes; run the idempotent migration so a
    # freshly-created linux_licensed.sqlite carries the three indexes too.
    migrate_existing_db(sqlite_path)

    # db_construct moves binaries out of staging into its own hash-based layout;
    # copy final processed tree into the date binaries dir
    for item in Path(staging_dir).rglob("*"):
        if item.is_file():
            rel = item.relative_to(staging_dir)
            dest = binaries_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(dest))

    shutil.rmtree(staging_dir, ignore_errors=True)
    logger.info("Done. Dataset updated at %s", sqlite_path)
    logger.info("Today's binaries stored at %s", binaries_dir)

    # Also harvest assembly files into assembly.sqlite
    run_assembly_pipeline(
        since_date_str=since_date_str,
        dataset_dir=str(dataset_dir),
        db_url=db_url,
        s3_endpoint=s3_endpoint,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        bucket=bucket,
        s3_https=s3_https,
        download_workers=download_workers,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily MinIO -> dataset pipeline")
    parser.add_argument(
        "--since", required=True, help="Fetch binaries built after this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--dataset-dir", default="assemblage_dataset", help="Root dataset directory"
    )
    parser.add_argument("--db-url", required=True, help="PostgreSQL connection URL")
    parser.add_argument("--s3-endpoint", required=True, help="MinIO/S3 host:port")
    parser.add_argument("--s3-access-key", required=True)
    parser.add_argument("--s3-secret-key", required=True)
    parser.add_argument("--bucket", default="artifacts")
    parser.add_argument("--s3-https", action="store_true")
    args = parser.parse_args()

    run_pipeline(
        since_date_str=args.since,
        dataset_dir=args.dataset_dir,
        db_url=args.db_url,
        s3_endpoint=args.s3_endpoint,
        s3_access_key=args.s3_access_key,
        s3_secret_key=args.s3_secret_key,
        bucket=args.bucket,
        s3_https=args.s3_https,
    )
