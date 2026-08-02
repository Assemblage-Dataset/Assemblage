"""Rewrite v1 (raw) artifact prefixes into the v2 (compressed) layout, in place.

The builder writes v2 from now on; this migrates the ~23k builds already in the
bucket. Measured on the published corpus, that is ~2.3 TiB down to ~370 GiB.

Per build, the transform is::

    {owner}_{project}_{sha}_{compiler}_{flag}/name          ->  {v2}/binaries/name.zst
    {owner}_{project}_{sha}_rustc-{be}_{mode}_{flag}/name    ->  {v2}/binaries/name.zst
                                     .../assemblage_meta.json ->  {v2}/metadata/…json.zst
                                     .../ir/{stage}.tar.gz    ->  {v2}/ir/{stage}.tar.gz
                                                              +   {v2}/export.json

Safety, in order of how much they matter:

- **Verify before delete.** Every object is re-downloaded from its new key and
  its sha256 compared against the source before a single v1 object is removed.
  A build that fails verification keeps its v1 objects and is reported.
- **Resumable.** A prefix whose ``export.json`` already exists is skipped, so an
  interrupted run costs only the build it was mid-way through.
- **--dry-run by default.** Deletion requires --apply.
- **--keep-raw** migrates without deleting, for a first pass you want to inspect.

Readers already resolve both layouts (dataset.pipeline tries v2, then v1), so
the corpus is queryable throughout and there is no flag day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from assemblage.storage import compress, layout

BUCKET = layout.ARTIFACTS_BUCKET
RUST_MARKER = "_rustc-"
_CHUNK = 4 << 20

_lock = threading.Lock()
_stats: dict[str, int] = defaultdict(int)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_v1_prefix(prefix: str) -> tuple[str, str, str] | None:
    """``(head, backend, mode)`` from a v1 prefix, or ``None`` if unparseable.

    ``head`` is ``{owner}_{project}_{sha12}`` kept verbatim — owner and project
    names contain underscores, so only the tail is split.
    """
    if RUST_MARKER in prefix:
        head, _, tail = prefix.partition(RUST_MARKER)
        parts = tail.split("_")
        if len(parts) != 3:
            return None
        backend, mode, flag = parts
        return f"{head}-{flag.lstrip('-')}-{backend}-{mode}", backend, mode
    # C/C++: {owner}_{project}_{sha12}_{compiler}_{flag}. v1 carried no mode, and
    # every C row in the live DB is RelWithDebInfo, so that is what v2 records.
    parts = prefix.rsplit("_", 2)
    if len(parts) != 3:
        return None
    head, compiler, flag = parts
    return f"{head}-{flag.lstrip('-')}-{compiler}-RelWithDebInfo", compiler, "RelWithDebInfo"


def migrate(s3, prefix: str, objects: list[dict], *, apply: bool, keep_raw: bool) -> None:
    parsed = parse_v1_prefix(prefix)
    if parsed is None:
        with _lock:
            _stats["unparseable"] += 1
        log(f"unparseable prefix, skipped: {prefix}")
        return
    v2, _, _ = parsed

    try:
        s3.head_object(Bucket=BUCKET, Key=layout.export_manifest_key(v2))
        with _lock:
            _stats["skipped"] += 1
        return
    except Exception:
        pass

    binaries: list[dict] = []
    metadata: dict | None = None
    ir: list[dict] = []
    raw_total = stored_total = 0
    migrated: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        for obj in objects:
            key = obj["Key"]
            rel = key[len(prefix) + 1 :]
            if not rel:
                continue
            src = os.path.join(tmp, "src")
            s3.download_file(BUCKET, key, src)
            want = sha256_file(src)
            raw_bytes = os.path.getsize(src)

            if rel == layout.METADATA_FILENAME:
                dst_key = layout.compressed_metadata_key(v2)
                packed = os.path.join(tmp, "packed")
                stored = compress.compress_file(src, packed)
                s3.upload_file(packed, BUCKET, dst_key)
                record = {
                    "file": f"{layout.METADATA_FILENAME}{layout.COMPRESSED_SUFFIX}",
                    "raw_bytes": raw_bytes,
                    "stored_bytes": stored,
                }
                metadata = record
                compressed = True
            elif rel.startswith(f"{layout.IR_DIRNAME}/"):
                # Already gzipped — copied verbatim, never recompressed.
                dst_key = f"{v2}/{rel}"
                s3.copy_object(
                    Bucket=BUCKET, CopySource={"Bucket": BUCKET, "Key": key}, Key=dst_key
                )
                stored = raw_bytes
                if not rel.endswith(layout.IR_MANIFEST_FILENAME):
                    ir.append(
                        {
                            "file": os.path.basename(rel),
                            "raw_bytes": raw_bytes,
                            "stored_bytes": stored,
                        }
                    )
                compressed = False
            else:
                base = os.path.basename(rel)
                dst_key = layout.binary_key(v2, base)
                packed = os.path.join(tmp, "packed")
                stored = compress.compress_file(src, packed)
                s3.upload_file(packed, BUCKET, dst_key)
                binaries.append(
                    {
                        "file": base + layout.COMPRESSED_SUFFIX,
                        "raw_bytes": raw_bytes,
                        "stored_bytes": stored,
                    }
                )
                compressed = True

            # --- verify the new object reproduces the old bytes exactly ------
            check = os.path.join(tmp, "check")
            s3.download_file(BUCKET, dst_key, check)
            if compressed:
                restored = os.path.join(tmp, "restored")
                compress.decompress_file(check, restored)
                got = sha256_file(restored)
                os.remove(restored)
            else:
                got = sha256_file(check)
            os.remove(check)
            os.remove(src)
            if got != want:
                with _lock:
                    _stats["verify_failed"] += 1
                log(f"VERIFY FAILED {key} -> {dst_key}; leaving v1 in place")
                return

            migrated.append(key)
            raw_total += raw_bytes
            stored_total += stored

    manifest = {
        "source_prefix": prefix,
        "compression": f"zstd -{compress.COMPRESS_LEVEL}",
        "binaries": binaries,
        "metadata": metadata,
        "ir": ir,
        "has_ir": bool(ir),
        "has_metadata": metadata is not None,
        "raw_bytes": raw_total,
        "stored_bytes": stored_total,
        "backfilled": True,
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=layout.export_manifest_key(v2),
        Body=json.dumps(manifest, indent=2).encode(),
    )

    if apply and not keep_raw and migrated:
        for start in range(0, len(migrated), 1000):
            s3.delete_objects(
                Bucket=BUCKET,
                Delete={
                    "Objects": [{"Key": k} for k in migrated[start : start + 1000]],
                    "Quiet": True,
                },
            )

    with _lock:
        _stats["migrated"] += 1
        _stats["objects"] += len(migrated)
        _stats["raw"] += raw_total
        _stats["stored"] += stored_total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default="http://localhost:9010")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="stop after N builds (0 = all)")
    ap.add_argument("--apply", action="store_true", help="delete v1 objects after verifying")
    ap.add_argument("--keep-raw", action="store_true", help="write v2 but keep v1 objects")
    args = ap.parse_args()

    s3 = boto3.session.Session(
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
    ).client(
        "s3",
        endpoint_url=args.endpoint,
        config=Config(
            max_pool_connections=args.threads + 16,
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )

    log("enumerating the artifacts bucket ...")
    builds: dict[str, list[dict]] = defaultdict(list)
    v2_dirs: set[str] = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            head, _, rest = obj["Key"].partition("/")
            if not rest:
                continue
            # v2 dirs are the ones already carrying an export.json.
            if rest == layout.EXPORT_MANIFEST_FILENAME:
                v2_dirs.add(head)
            builds[head].append(obj)

    todo = sorted(p for p in builds if p not in v2_dirs)
    if args.limit:
        todo = todo[: args.limit]
    log(f"{len(todo):,} v1 prefixes to migrate ({len(v2_dirs):,} already v2)")
    if not args.apply and not args.keep_raw:
        log("DRY RUN — pass --apply to delete v1 objects, or --keep-raw to write v2 only")
        return 0

    start = time.time()
    total = len(todo)

    def work(prefix: str) -> None:
        try:
            migrate(s3, prefix, builds[prefix], apply=args.apply, keep_raw=args.keep_raw)
        except Exception as exc:
            with _lock:
                _stats["failed"] += 1
            log(f"FAILED {prefix}: {exc!r}")
        done = sum(_stats[k] for k in ("migrated", "skipped", "failed", "verify_failed"))
        if done % 250 == 0:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            log(
                f"{done:,}/{total:,}  {_stats['raw'] / 2**30:,.0f} -> "
                f"{_stats['stored'] / 2**30:,.0f} GiB  {rate:.1f} builds/s  "
                f"eta {(total - done) / rate / 3600 if rate else 0:.1f}h"
            )

    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        list(pool.map(work, todo))

    saved = _stats["raw"] - _stats["stored"]
    log(
        f"DONE in {(time.time() - start) / 3600:.2f}h  migrated={_stats['migrated']:,} "
        f"skipped={_stats['skipped']:,} failed={_stats['failed']:,} "
        f"verify_failed={_stats['verify_failed']:,} unparseable={_stats['unparseable']:,}"
    )
    log(
        f"{_stats['objects']:,} objects  {_stats['raw'] / 2**30:,.1f} -> "
        f"{_stats['stored'] / 2**30:,.1f} GiB  (reclaimed {saved / 2**30:,.1f} GiB)"
    )
    return 1 if (_stats["failed"] or _stats["verify_failed"]) else 0


if __name__ == "__main__":
    sys.exit(main())
