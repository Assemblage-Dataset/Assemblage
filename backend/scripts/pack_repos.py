"""Pack the exported corpus into one tar per upstream repository.

HuggingFace rate-limits free accounts to 1,000 API requests per 5 minutes, and
the flat export is 87,578 files — well over 100,000 requests, i.e. 15+ hours of
sustained max-rate uploading. Bundling each repo's builds into a single tar cuts
that to ~3,034 files and makes the upload bandwidth-bound instead.

The tars are **uncompressed**: every payload inside is already zstd (binaries,
metadata) or gzip (IR), so a second pass would cost hours and save almost nothing.

Grouping is keyed on ``repo_url`` from each ``export.json``, not on the directory
name — directory names flatten owner/project into one underscore-joined string and
are ambiguous (``junjitree/api.dracker`` becomes ``junjitree_dracker``).

**Incremental.** A repo's tar is rebuilt when the set of build directories it
should contain no longer matches what it does contain. The previous
``if dst.exists(): skip`` resume check silently dropped every new build for any
repo that already had a tar — the exact case an incremental push consists of.
Comparing member sets costs one tar index read per repo and is what makes
re-running this after a few thousand new builds correct rather than a no-op.
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_lock = threading.Lock()
_done = {"n": 0, "bytes": 0, "unchanged": 0, "rebuilt": 0}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def tar_is_current(path: Path, want: set[str]) -> bool:
    """True when ``path`` already contains exactly the build dirs in ``want``.

    Only top-level member names are compared — that is the granularity a build
    is added or removed at, and reading the index is far cheaper than the
    unconditional repack it saves. An unreadable or truncated tar reports False
    so it gets rebuilt rather than trusted.
    """
    try:
        with tarfile.open(path, "r") as tf:
            have = {name.split("/", 1)[0] for name in tf.getnames()}
    except (tarfile.TarError, OSError) as exc:
        log(f"cannot read {path.name} ({exc}); rebuilding")
        return False
    return have == want


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="/home/cliu57/research/Assemblage/assemblage-rust")
    ap.add_argument("--out", default="/home/cliu57/research/assemblage-rust-hf")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    (out / "repos").mkdir(parents=True, exist_ok=True)

    log("grouping build directories by upstream repo ...")
    groups: dict[str, list[Path]] = collections.defaultdict(list)
    index: list[dict] = []
    for d in sorted(src.iterdir()):
        # skip non-builds: dotfiles and the uploader's .cache/ bookkeeping
        if not d.is_dir() or d.name.startswith(".") or not (d / "export.json").is_file():
            continue
        man = json.loads((d / "export.json").read_text())
        slug = man["repo_url"].removeprefix("https://github.com/")
        owner, _, name = slug.partition("/")
        key = f"{owner}__{name}"
        groups[key].append(d)
        index.append(
            {
                "build_dir": d.name,
                "tar": f"repos/{key}.tar",
                "repo_url": man["repo_url"],
                "commit": man["commit"],
                "license": man["license"],
                "binaries": len(man["binaries"]),
                "has_metadata": man["has_metadata"],
                "has_ir": man["has_ir"],
                "stored_bytes": man["stored_bytes"],
            }
        )
    log(f"{len(index):,} builds -> {len(groups):,} repo tars")

    with (out / "index.jsonl").open("w") as fh:
        for row in sorted(index, key=lambda r: r["build_dir"]):
            fh.write(json.dumps(row) + "\n")

    total = len(groups)

    def pack(item: tuple[str, list[Path]]) -> None:
        key, dirs = item
        dst = out / "repos" / f"{key}.tar"
        want = {d.name for d in dirs}
        if dst.exists() and tar_is_current(dst, want):
            with _lock:
                _done["n"] += 1
                _done["unchanged"] += 1
            return
        if dst.exists():
            log(f"rebuilding {key}.tar: build set changed ({len(want)} dirs)")
            with _lock:
                _done["rebuilt"] += 1
        part = dst.with_suffix(".tar.part")
        listing = "\n".join(d.name for d in dirs) + "\n"
        proc = subprocess.run(
            ["tar", "-C", str(src), "-cf", str(part), "-T", "-"],
            input=listing,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"tar failed for {key}: {proc.stderr[:200]}")
        part.rename(dst)
        with _lock:
            _done["n"] += 1
            _done["bytes"] += dst.stat().st_size
            n = _done["n"]
        if n % 100 == 0:
            log(f"{n:,}/{total:,}  {_done['bytes'] / 1e9:.1f} GB packed")

    t0 = time.time()
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in [ex.submit(pack, it) for it in groups.items()]:
            try:
                fut.result()
            except Exception as exc:
                failed += 1
                log(f"FAILED: {exc!r}")

    log(
        f"DONE in {(time.time() - t0) / 60:.1f}m  tars={_done['n']:,} "
        f"unchanged={_done['unchanged']:,} rebuilt={_done['rebuilt']:,} "
        f"failed={failed}  {_done['bytes'] / 1e9:.1f} GB written"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
