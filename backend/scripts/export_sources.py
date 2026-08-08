"""Stage the corpus's source archives for publication, next to the packed builds.

The release ships binaries, metadata and IR; until now it shipped no source at
all, only a ``repo_url`` + ``commit`` pointer in each ``export.json``. That makes
source correspondence contingent on GitHub: a deleted, privated or force-pushed
repo leaves its binaries permanently unaccompanied, with DWARF naming
``source_file`` paths nothing can resolve. This stage closes that gap.

Output is a **sibling** of the packed repo tars, not a member of them::

    {out}/repos/{owner}__{name}.tar        <- pack_repos.py, UNTOUCHED
    {out}/sources/{owner}__{name}.tar.gz   <- here

Deliberately a separate tree. ``pack_repos.py`` decides whether to rebuild a tar
by comparing its top-level member set, so adding a ``source/`` member inside the
tars would invalidate *every* one of them -- a full re-pack and re-upload of the
whole corpus (~463 GiB) to ship ~31 GiB of source. Keyed on the same
``{owner}__{name}`` slug as the tar it belongs to, so the join is exact.

Archives are copied **verbatim**: they are already gzip, already the whole
``git clone --recursive`` working tree, and were uploaded before the build ran
(no ``target/`` -- verified against the bucket). ``.git`` is included, which is
~20% of bytes on average, so the shipped tree carries full history and commit
metadata rather than a bare snapshot.

Only repos reachable from ``--src`` are staged, so the license filter that
governs the export governs source too -- copyleft and unidentified licenses never
reach this stage because they were never exported.

Resumable: a destination whose size already matches the object is skipped, and
downloads land as ``*.part`` then rename, so a kill never leaves a truncated
archive that looks complete.

Usage::

    set -a; . ./secrets.env; set +a
    python backend/scripts/export_sources.py --dry-run      # report, write nothing
    python backend/scripts/export_sources.py --threads 16
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config

PROJECT_ARCHIVE_BUCKET = "project-archive"

_lock = threading.Lock()
_done = {"copied": 0, "skipped": 0, "failed": 0, "bytes": 0}
_t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:7.1f}s] {msg}", flush=True)


def slug_of(repo_url: str) -> str | None:
    """The ``{owner}__{name}`` key pack_repos.py files this repo's builds under.

    Must stay byte-identical to that script's key, or the shipped source will not
    line up with the tar it describes.
    """
    tail = repo_url.removeprefix("https://github.com/").strip("/")
    owner, _, name = tail.partition("/")
    if not owner or not name:
        return None
    return f"{owner}__{name}"


def index_bucket(s3, bucket: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Map every archive key -> size, plus sha12 -> keys for fallback lookup.

    The fallback exists because ``export_corpus.py`` rewrites repo URLs
    (``.replace("api.", "").replace("repos/", "")``) while the builder derived the
    archive key from the *raw* URL, so ``junjitree/api.dracker`` is stored under a
    name the cleaned URL can no longer reproduce. sha12 is unique enough to
    recover those few by commit alone.
    """
    sizes: dict[str, int] = {}
    by_sha: dict[str, list[str]] = collections.defaultdict(list)
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("latest.txt"):
                continue
            sizes[key] = obj["Size"]
            sha = key.rsplit("/", 1)[-1].removesuffix(".tar.gz")
            by_sha[sha].append(key)
    return sizes, by_sha


def collect(src: Path) -> dict[str, list[dict]]:
    """Group the export's build dirs by repo slug, carrying provenance along."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for d in sorted(src.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        manifest = d / "export.json"
        if not manifest.is_file():
            continue
        try:
            man = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        url, commit = man.get("repo_url"), man.get("commit")
        if not url or not commit:
            continue
        slug = slug_of(url)
        if slug is None:
            continue
        groups[slug].append(
            {"repo_url": url, "commit": commit, "license": man.get("license") or ""}
        )
    return groups


def plan(groups: dict[str, list[dict]], sizes, by_sha) -> tuple[list[dict], list[dict]]:
    """Resolve each repo to one archive object. Returns (resolved, unresolved)."""
    resolved: list[dict] = []
    unresolved: list[dict] = []
    for slug, builds in sorted(groups.items()):
        commits = {b["commit"] for b in builds}
        for commit in sorted(commits):
            first = next(b for b in builds if b["commit"] == commit)
            owner, _, name = first["repo_url"].removeprefix(
                "https://github.com/"
            ).strip("/").partition("/")
            key = f"{owner}/{name}/{commit}.tar.gz"
            if key not in sizes:
                # cleaned-URL miss: recover by commit, but only when unambiguous
                candidates = by_sha.get(commit, [])
                key = candidates[0] if len(candidates) == 1 else ""
            entry = {
                "slug": slug,
                # sha suffix only when a repo really has >1 commit in the export,
                # so the common 1:1 case stays a plain mirror of repos/{slug}.tar
                "file": f"{slug}.tar.gz" if len(commits) == 1 else f"{slug}__{commit}.tar.gz",
                "key": key,
                "bytes": sizes.get(key, 0),
                "repo_url": first["repo_url"],
                "commit": commit,
                "license": first["license"],
                "builds": sum(1 for b in builds if b["commit"] == commit),
            }
            (resolved if key else unresolved).append(entry)
    return resolved, unresolved


def fetch(s3, bucket: str, entry: dict, out_dir: Path) -> None:
    dst = out_dir / entry["file"]
    if dst.is_file() and dst.stat().st_size == entry["bytes"] and entry["bytes"] > 0:
        with _lock:
            _done["skipped"] += 1
        return
    part = dst.with_suffix(".gz.part")
    try:
        with part.open("wb") as fh:
            s3.download_fileobj(bucket, entry["key"], fh)
        size = part.stat().st_size
        if entry["bytes"] and size != entry["bytes"]:
            raise OSError(f"size mismatch: got {size}, want {entry['bytes']}")
        part.rename(dst)
    except Exception as exc:  # noqa: BLE001 - one bad object must not stop the run
        part.unlink(missing_ok=True)
        with _lock:
            _done["failed"] += 1
        log(f"FAILED {entry['file']}: {exc}")
        return
    with _lock:
        _done["copied"] += 1
        _done["bytes"] += size
        n = _done["copied"] + _done["skipped"]
        if n % 200 == 0:
            log(f"{n} done ({_done['bytes'] / 2**30:.1f} GiB copied)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="/home/cliu57/research/Assemblage/assemblage-rust",
                    help="exported corpus root (source of truth for what ships)")
    ap.add_argument("--out", default="/home/cliu57/research/assemblage-rust-hf",
                    help="HF upload folder; sources land in {out}/sources")
    ap.add_argument("--endpoint", default="http://localhost:9010")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="stage only N repos (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not src.is_dir():
        log(f"export root not found: {src}")
        return 2
    out_dir = Path(args.out).resolve() / "sources"

    cfg = Config(max_pool_connections=args.threads + 16,
                 retries={"max_attempts": 5, "mode": "standard"})
    session = boto3.session.Session(
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
    )
    s3 = session.client("s3", endpoint_url=args.endpoint, config=cfg)

    log(f"indexing {PROJECT_ARCHIVE_BUCKET} ...")
    sizes, by_sha = index_bucket(s3, PROJECT_ARCHIVE_BUCKET)
    log(f"{len(sizes)} archives in bucket")

    log(f"scanning {src} ...")
    groups = collect(src)
    resolved, unresolved = plan(groups, sizes, by_sha)
    total = sum(e["bytes"] for e in resolved)
    log(f"{len(groups)} repos in export -> {len(resolved)} archives, "
        f"{total / 2**30:.1f} GiB; {len(unresolved)} unresolved")
    for e in unresolved:
        log(f"  UNRESOLVED {e['repo_url']} @ {e['commit']} ({e['builds']} builds)")

    if args.limit:
        resolved = resolved[: args.limit]
        log(f"--limit: staging {len(resolved)}")
    if args.dry_run:
        log("--dry-run: nothing written")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        list(pool.map(lambda e: fetch(s3, PROJECT_ARCHIVE_BUCKET, e, out_dir), resolved))

    manifest = {
        "count": len(resolved),
        "unresolved": [
            {"repo_url": e["repo_url"], "commit": e["commit"]} for e in unresolved
        ],
        "note": "verbatim git clone --recursive tarballs, .git included, pre-build",
        "repos": [
            {
                "file": e["file"],
                "repo_url": e["repo_url"],
                "commit": e["commit"],
                "license": e["license"],
                "bytes": e["bytes"],
                "builds": e["builds"],
                "tar": f"repos/{e['slug']}.tar",
            }
            for e in resolved
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")

    log(f"copied {_done['copied']}, skipped {_done['skipped']}, failed {_done['failed']}, "
        f"{_done['bytes'] / 2**30:.1f} GiB -> {out_dir}")
    return 1 if _done["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
