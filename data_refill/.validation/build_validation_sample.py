#!/usr/bin/env python3
"""Build a diverse 50-binary sample for deep validation.

Stratifies across compiler/build_mode/opt level so we catch bugs that
might only surface on specific build configurations.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

import duckdb

DUCKDB = "/home/cliu57/research/Assemblage/data_refill/linux_licensed.duckdb"
LXSRC = "/home/cliu57/research/Assemblage/data_refill/linuxsource"
BINROOT = "/home/cliu57/research/Assemblage/data_refill/binaries"
OUT = "/home/cliu57/research/Assemblage/data_refill/.validation/validation_sample.json"


def find_archive(github_url: str, repo_commit: str | None) -> str | None:
    if not github_url.startswith("https://github.com/"):
        return None
    parts = github_url[len("https://github.com/"):].rstrip("/").split("/")
    if len(parts) < 2:
        return None
    user, repo = parts[0], parts[1]
    repo_dir = os.path.join(LXSRC, user, repo)
    if not os.path.isdir(repo_dir):
        return None
    if repo_commit:
        short = repo_commit[:12]
        candidate = os.path.join(repo_dir, f"{short}.tar.gz")
        if os.path.isfile(candidate):
            return candidate
    # Fall back to any .tar.gz in the repo dir
    tarballs = sorted(glob.glob(os.path.join(repo_dir, "*.tar.gz")))
    return tarballs[0] if tarballs else None


def main() -> None:
    con = duckdb.connect(DUCKDB, read_only=True)
    con.execute("SELECT setseed(0.42)")

    # Stratified sample: 5 from each (build_mode, compiler) bucket, plus
    # special cases.
    strata = [
        ("RelWithDebInfo", "clang", 30),
        ("RelWithDebInfo", "gcc",   30),
        ("RelWithDebInfo", "",      8),
        ("",               "clang", 40),
        ("",               "gcc",   40),
    ]

    sample = []
    for build_mode, compiler, n in strata:
        rows = con.execute(f"""
            SELECT id, file_name, path, github_url, repo_commit,
                   build_mode, toolset_version, optimization, size
            FROM binaries
            WHERE build_mode = ? AND toolset_version = ?
              AND size > 0
              AND github_url IS NOT NULL AND github_url != ''
            ORDER BY hash(id || ?)
            LIMIT {n}
        """, [build_mode, compiler, str(n)]).fetchall()
        cols = [d[0] for d in con.description]
        for r in rows:
            sample.append(dict(zip(cols, r)))

    print(f"sampled {len(sample)} binaries from strata")

    # Add a few specific ones we already smoke-tested
    extras = [165223, 428376, 545109]
    rows = con.execute(
        f"""SELECT id, file_name, path, github_url, repo_commit,
                   build_mode, toolset_version, optimization, size
            FROM binaries WHERE id IN ({','.join(str(x) for x in extras)})"""
    ).fetchall()
    cols = [d[0] for d in con.description]
    for r in rows:
        d = dict(zip(cols, r))
        if d["id"] not in {s["id"] for s in sample}:
            sample.append(d)

    work_items = []
    n_with_archive = 0
    for s in sample:
        bp = os.path.join(BINROOT, s["path"])
        archive = find_archive(s["github_url"], s["repo_commit"])
        if archive:
            n_with_archive += 1
        work_items.append({
            "binary_id": s["id"],
            "binary_path": bp,
            "source_archive": archive,
            "compiler": s["toolset_version"],
            "build_mode": s["build_mode"],
            "optimization": s["optimization"],
            "github_url": s["github_url"],
            "size_kb": s["size"],
        })

    Path(OUT).write_text(json.dumps(work_items, indent=2, default=str))
    print(f"wrote {OUT}")
    print(f"  {n_with_archive}/{len(work_items)} have a source archive")
    print(f"  binaries on disk: {sum(1 for w in work_items if os.path.isfile(w['binary_path']))}/{len(work_items)}")


if __name__ == "__main__":
    main()
