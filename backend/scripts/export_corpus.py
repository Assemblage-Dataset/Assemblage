"""Export the Rust corpus out of MinIO into a sorted, compressed tree on disk.

One directory per build::

    assemblage-rust/{owner}_{project}_{sha12}-{flag}-{backend}-{mode}/
        binaries/<name>.zst          zstd -12
        metadata/assemblage_meta.json.zst   zstd -12
        ir/<stage>.tar.gz            copied verbatim (already gzipped)
        export.json                  provenance: source prefix + per-object sizes

Two source layouts are handled:

- **v2** (what the builder writes now) is already stored in exactly this shape
  and at the same zstd level, so it is copied **verbatim** — no decompress /
  recompress cycle, which is what made a full export a multi-hour job.
- **v1** (raw objects, until ``backfill_compress.py`` has run) is streamed
  S3 -> zstd -> disk, so the uncompressed bytes never touch the filesystem.

``export.json`` is enriched with ``repo_url``, ``commit`` and ``license`` from
PostgreSQL. ``pack_repos.py`` requires all three: it groups by ``repo_url`` and
the release excludes copyleft and unidentified licenses. Builders cannot supply
``license`` (they have no database access), so it is joined in here.

Resumable: a directory carrying a complete ``export.json`` is skipped, so
re-running after an interrupt picks up where it stopped. Partial files are written
as ``*.part`` and renamed on success, so a kill never leaves a truncated artifact
that looks finished.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import sqlalchemy
from botocore.config import Config

ARTIFACTS_BUCKET = "artifacts"
METADATA_FILENAME = "assemblage_meta.json"
IR_DIRNAME = "ir/"
EXPORT_MANIFEST = "export.json"
RUST_MARKER = "_rustc-"

ZSTD = shutil.which("zstd") or "/home/cliu57/anaconda3/bin/zstd"

_print_lock = threading.Lock()
_stats_lock = threading.Lock()
_stats = {
    "done": 0,
    "skipped": 0,
    "failed": 0,
    "raw": 0,
    "stored": 0,
    "no_meta": 0,
    "no_license": 0,
    "passthrough": 0,
    "recompressed": 0,
}


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def split_prefix(prefix: str) -> tuple[str, str, str, str] | None:
    """``owner_project_sha12_rustc-{backend}_{mode}_{flag}`` -> its four parts.

    Owner and project names contain underscores, so the head is kept verbatim and
    only the tail (after the ``_rustc-`` marker) is split.
    """
    head, _, tail = prefix.partition(RUST_MARKER)
    if not tail:
        return None
    parts = tail.split("_")
    if len(parts) != 3:
        return None
    backend, mode, flag = parts
    return head, backend, mode, flag


def dest_name(prefix: str, *, passthrough: bool = False) -> str | None:
    """The on-disk folder name for a build prefix.

    A v2 prefix already *is* the destination name — that is the point of the
    layout — so it passes through untouched. A v1 rust prefix is rewritten.
    """
    if passthrough:
        return prefix
    split = split_prefix(prefix)
    if split is None:
        return None
    head, backend, mode, flag = split
    return f"{head}-{flag.lstrip('-')}-{backend}-{mode}"


def stream_compress(s3, key: str, dst: Path, level: int) -> int:
    """Stream one object through ``zstd`` into ``dst``; return stored bytes."""
    part = dst.with_suffix(dst.suffix + ".part")
    proc = subprocess.Popen(
        [ZSTD, f"-{level}", "-T1", "-q", "-f", "-o", str(part)],
        stdin=subprocess.PIPE,
    )
    try:
        body = s3.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)["Body"]
        assert proc.stdin is not None
        shutil.copyfileobj(body, proc.stdin, length=4 << 20)
        proc.stdin.close()
        body.close()
    except BaseException:
        proc.kill()
        proc.wait()
        part.unlink(missing_ok=True)
        raise
    if proc.wait() != 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"zstd exit {proc.returncode} for {key}")
    part.rename(dst)
    return dst.stat().st_size


def stream_copy(s3, key: str, dst: Path) -> int:
    """Stream one object verbatim into ``dst``; return bytes written."""
    part = dst.with_suffix(dst.suffix + ".part")
    try:
        body = s3.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)["Body"]
        with part.open("wb") as fh:
            shutil.copyfileobj(body, fh, length=4 << 20)
        body.close()
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.rename(dst)
    return dst.stat().st_size


#: The codegen backends the Rust builders use; a v2 dir names one of them in the
#: second-to-last dash field, which is what distinguishes a rust build dir from
#: a C/C++ one without consulting the database.
RUST_BACKENDS = ("llvm", "cranelift", "gcc")
BUILD_MODES = ("Release", "Debug", "RelWithDebInfo")


def _is_rust_v2_dir(prefix: str) -> bool:
    """True for ``{owner}_{project}_{sha12}-{flag}-{backend}-{mode}`` (rust only)."""
    parts = prefix.rsplit("-", 3)
    if len(parts) != 4:
        return False
    _, _, backend, mode = parts
    # cg_gcc is a rust backend AND a C compiler name; the C v1 prefix has no
    # mode field, so requiring a known mode keeps the two apart.
    return backend in RUST_BACKENDS and mode in BUILD_MODES


def is_v2(prefix: str, objs: list[dict]) -> bool:
    """True when this prefix is already in the compressed, HF-aligned layout."""
    return any(obj["Key"] == f"{prefix}/{EXPORT_MANIFEST}" for obj in objs)


def export_v2(s3, dest: Path, prefix: str, objs: list[dict]) -> dict:
    """Copy an already-compressed build verbatim; return its export manifest.

    Nothing is decompressed: the stored bytes are the bytes we publish.
    """
    manifest: dict = {}
    for obj in objs:
        rel = obj["Key"][len(prefix) + 1 :]
        if not rel:
            continue
        if rel == EXPORT_MANIFEST:
            manifest = json.loads(
                s3.get_object(Bucket=ARTIFACTS_BUCKET, Key=obj["Key"])["Body"].read()
            )
            continue
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        stream_copy(s3, obj["Key"], dst)
    return manifest


def export_v1(s3, dest: Path, prefix: str, objs: list[dict], level: int) -> dict:
    """Stream a raw build through zstd onto disk; return its export manifest."""
    (dest / "binaries").mkdir(parents=True, exist_ok=True)
    (dest / "metadata").mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "source_prefix": prefix,
        "compression": f"zstd -{level}",
        "binaries": [],
        "metadata": None,
        "ir": [],
    }
    raw = stored = 0
    has_ir = False

    for obj in objs:
        key = obj["Key"]
        rel = key[len(prefix) + 1 :]
        if not rel:
            continue
        size = obj["Size"]
        raw += size

        if rel == METADATA_FILENAME:
            dst = dest / "metadata" / f"{METADATA_FILENAME}.zst"
            got = stream_compress(s3, key, dst, level)
            manifest["metadata"] = {"file": dst.name, "raw_bytes": size, "stored_bytes": got}
        elif rel.startswith(IR_DIRNAME):
            has_ir = True
            dst = dest / "ir" / rel[len(IR_DIRNAME) :]
            dst.parent.mkdir(parents=True, exist_ok=True)
            got = stream_copy(s3, key, dst)
            manifest["ir"].append({"file": dst.name, "bytes": got})
        else:
            dst = dest / "binaries" / f"{Path(rel).name}.zst"
            got = stream_compress(s3, key, dst, level)
            manifest["binaries"].append({"file": dst.name, "raw_bytes": size, "stored_bytes": got})
        stored += got

    manifest["has_ir"] = has_ir
    manifest["raw_bytes"] = raw
    manifest["stored_bytes"] = stored
    return manifest


def export_build(
    s3, out_root: Path, prefix: str, objs: list[dict], level: int, licenses: dict[str, dict]
) -> None:
    """Export one build prefix into its sorted directory."""
    passthrough = is_v2(prefix, objs)
    name = dest_name(prefix, passthrough=passthrough)
    if name is None:
        with _stats_lock:
            _stats["failed"] += 1
        log(f"unparseable prefix, skipped: {prefix}")
        return

    dest = out_root / name
    manifest_path = dest / EXPORT_MANIFEST
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text())
        except ValueError:
            existing = {}
        # Only a manifest carrying the pack-required fields counts as complete;
        # trees written before the enrichment existed are re-finished, not redone.
        if all(existing.get(field) for field in ("repo_url", "commit", "license")):
            with _stats_lock:
                _stats["skipped"] += 1
            return

    dest.mkdir(parents=True, exist_ok=True)
    if passthrough:
        manifest = export_v2(s3, dest, prefix, objs)
    else:
        manifest = export_v1(s3, dest, prefix, objs, level)

    # A build whose binaries produced no DWARF still ships the binaries; record it
    # so downstream consumers can filter instead of rediscovering the gap.
    manifest.setdefault("source_prefix", prefix)
    manifest["has_metadata"] = manifest.get("metadata") is not None
    manifest["passthrough"] = passthrough

    # pack_repos.py groups on repo_url (directory names flatten owner/project
    # ambiguously) and the release filters on license — both come from the DB.
    joined = licenses.get(name)
    if joined is None:
        with _stats_lock:
            _stats["no_license"] += 1
        log(f"no database row for {name}; export.json will lack repo_url/license")
    else:
        manifest.update(joined)

    manifest_path.write_text(json.dumps(manifest, indent=2))

    with _stats_lock:
        _stats["done"] += 1
        _stats["raw"] += manifest.get("raw_bytes", 0)
        _stats["stored"] += manifest.get("stored_bytes", 0)
        _stats["passthrough" if passthrough else "recompressed"] += 1
        if manifest.get("metadata") is None:
            _stats["no_meta"] += 1


def load_licenses(db_url: str) -> dict[str, dict]:
    """Map each build directory name -> ``{repo_url, commit, license}``.

    Built from one pass over projects x b_status x buildopt, so the exporter does
    a single query rather than 23k of them.
    """
    engine = sqlalchemy.create_engine(db_url)
    rows: dict[str, dict] = {}
    with engine.connect() as conn:
        for url, commit, lic, compiler, flag, backend, mode in conn.execute(
            sqlalchemy.text(
                """SELECT p.url, s.commit_hexsha, p.license, LOWER(o.compiler_name),
                          o.compiler_flag, o.codegen_backend, o.build_type
                   FROM b_status s
                   JOIN projects p ON p.id = s.repo_id
                   JOIN buildopt o ON o.id = s.build_opt_id
                   WHERE s.commit_hexsha IS NOT NULL AND s.commit_hexsha != ''"""
            )
        ):
            clean = (url or "").replace("api.", "").replace("repos/", "")
            owner, _, project = clean.rstrip("/").rpartition("/")
            owner = owner.rsplit("/", 1)[-1]
            sha12 = (commit or "")[:12]
            engine_name = backend or compiler
            name = f"{owner}_{project}_{sha12}-{(flag or '').lstrip('-')}-{engine_name}-{mode}"
            rows[name] = {"repo_url": clean, "commit": sha12, "license": lic or ""}
    engine.dispose()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="./assemblage-rust", help="destination root")
    ap.add_argument("--endpoint", default="http://localhost:9010")
    ap.add_argument("--level", type=int, default=12, help="zstd level")
    ap.add_argument("--threads", type=int, default=64, help="concurrent builds")
    ap.add_argument("--limit", type=int, default=0, help="stop after N builds (0 = all)")
    ap.add_argument(
        "--db-url",
        default="postgresql+psycopg2://assemblage:assemblage@localhost:5432/assemblage",
        help="source of repo_url/commit/license for export.json",
    )
    args = ap.parse_args()

    if not Path(ZSTD).exists():
        log(f"zstd not found at {ZSTD}")
        return 2

    out_root = Path(args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    cfg = Config(
        max_pool_connections=args.threads + 16,
        retries={"max_attempts": 5, "mode": "standard"},
    )
    session = boto3.session.Session(
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
    )
    s3 = session.client("s3", endpoint_url=args.endpoint, config=cfg)

    log(f"enumerating {ARTIFACTS_BUCKET} ...")
    builds: dict[str, list[dict]] = {}
    n_obj = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=ARTIFACTS_BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            prefix, _, rest = key.partition("/")
            if not rest:
                continue
            # v1 rust prefixes carry the _rustc- marker; v2 build dirs carry no
            # marker at all, so they are recognised by their -{backend}-{mode}
            # tail. C/C++ v1 prefixes match neither and stay out of this export.
            if RUST_MARKER not in prefix and not _is_rust_v2_dir(prefix):
                continue
            builds.setdefault(prefix, []).append(obj)
            n_obj += 1
    log(f"{len(builds):,} rust builds / {n_obj:,} objects")

    log("loading repo_url/commit/license from the database ...")
    licenses = load_licenses(args.db_url)
    log(f"{len(licenses):,} build rows joined")

    todo = sorted(builds)
    if args.limit:
        todo = todo[: args.limit]

    t0 = time.time()
    total = len(todo)

    def work(prefix: str) -> None:
        try:
            export_build(s3, out_root, prefix, builds[prefix], args.level, licenses)
        except Exception as exc:  # keep one bad build from killing the run
            with _stats_lock:
                _stats["failed"] += 1
            log(f"FAILED {prefix}: {exc!r}")
        n = _stats["done"] + _stats["skipped"] + _stats["failed"]
        if n % 250 == 0:
            el = time.time() - t0
            rate = n / el if el else 0
            eta = (total - n) / rate / 3600 if rate else 0
            log(
                f"{n:,}/{total:,}  raw={_stats['raw'] / 1e9:.1f}GB "
                f"stored={_stats['stored'] / 1e9:.1f}GB  "
                f"{rate:.1f} builds/s  eta {eta:.1f}h"
            )

    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        list(ex.map(work, todo))

    el = time.time() - t0
    ratio = _stats["raw"] / _stats["stored"] if _stats["stored"] else 0
    log(
        f"DONE in {el / 3600:.2f}h  exported={_stats['done']:,} "
        f"skipped={_stats['skipped']:,} failed={_stats['failed']:,} "
        f"no-metadata={_stats['no_meta']:,} no-license={_stats['no_license']:,} "
        f"passthrough={_stats['passthrough']:,} recompressed={_stats['recompressed']:,}"
    )
    log(f"raw={_stats['raw'] / 1e9:.1f}GB stored={_stats['stored'] / 1e9:.1f}GB ({ratio:.1f}x)")
    return 1 if _stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
