#!/usr/bin/env python3
"""Measure how much space symlink-flattened duplicates use.

Walks deephistory/binaries, hashes every file, and reports:
  - total on-disk bytes
  - bytes stored redundantly (sum of dup copies beyond the first)
  - redundancy % of total
  - breakdown by scope:
      * intra-prefix: duplicates within one prefix (symlink triplets)
      * cross-prefix: same file across different prefixes (shared deps)

Usage:  python3 dedup_analysis.py [root]
"""
import os
import sys
import hashlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = sys.argv[1] if len(sys.argv) > 1 else "deephistory/binaries"
WORKERS = int(os.environ.get("WORKERS", "32"))

def hash_file(path):
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_prefix(prefix_dir):
    """Return list of (hash, size, filename, prefix) for each file."""
    out = []
    prefix = os.path.basename(prefix_dir)
    for entry in os.scandir(prefix_dir):
        if entry.is_file():
            try:
                size = entry.stat().st_size
                h = hash_file(entry.path)
                out.append((h, size, entry.name, prefix))
            except OSError:
                pass
    return out


def main():
    prefixes = [os.path.join(ROOT, p) for p in os.listdir(ROOT)
                if os.path.isdir(os.path.join(ROOT, p))]
    print(f"Scanning {len(prefixes)} prefixes in {ROOT}...", flush=True)

    total_bytes = 0
    file_count = 0
    # hash -> list of (size, filename, prefix)
    by_hash = defaultdict(list)
    # for intra-prefix dups: (prefix, hash) -> count
    intra = defaultdict(int)

    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(scan_prefix, p): p for p in prefixes}
        done = 0
        for fut in as_completed(futs):
            rows = fut.result()
            for h, size, fname, pfx in rows:
                total_bytes += size
                file_count += 1
                by_hash[h].append((size, fname, pfx))
                intra[(pfx, h)] += 1
            done += 1
            if done % 500 == 0:
                print(f"\r  {done}/{len(prefixes)} prefixes scanned, {file_count} files, "
                      f"{total_bytes/(1024**3):.1f} GiB",
                      end="", flush=True)
    print()

    # Redundancy analysis
    redundant_bytes = 0            # total dup bytes (all copies beyond 1st)
    intra_redundant = 0            # within same prefix (symlink flattening)
    cross_redundant = 0            # across prefixes (shared deps)

    for h, rows in by_hash.items():
        if len(rows) == 1:
            continue
        size = rows[0][0]
        redundant_bytes += size * (len(rows) - 1)

        # Count intra-prefix dups (appear >1 time in same prefix)
        prefix_counts = defaultdict(int)
        for _, _, pfx in rows:
            prefix_counts[pfx] += 1
        for pfx, n in prefix_counts.items():
            if n > 1:
                intra_redundant += size * (n - 1)

    cross_redundant = redundant_bytes - intra_redundant

    print()
    print("=" * 50)
    print(f"Total files:          {file_count:,}")
    print(f"Unique content:       {len(by_hash):,}")
    print(f"Total on-disk:        {total_bytes / (1024**3):.2f} GiB")
    print(f"Redundant (dup)      {redundant_bytes / (1024**3):.2f} GiB "
          f"({100*redundant_bytes/total_bytes:.1f}%)")
    print(f"  intra-prefix dups: {intra_redundant / (1024**3):.2f} GiB "
          f"({100*intra_redundant/total_bytes:.1f}%)  [symlink flattening]")
    print(f"  cross-prefix dups: {cross_redundant / (1024**3):.2f} GiB "
          f"({100*cross_redundant/total_bytes:.1f}%)  [shared across configs]")
    print(f"Dedup-able savings:  {redundant_bytes / (1024**3):.2f} GiB")
    print("=" * 50)

    # Top 10 most-duplicated file contents by redundant bytes
    print("\nTop 10 files by wasted-duplicate bytes:")
    dup_waste = [(h, rows[0][0] * (len(rows) - 1), len(rows), rows[0][1])
                 for h, rows in by_hash.items() if len(rows) > 1]
    dup_waste.sort(key=lambda x: -x[1])
    for h, waste, copies, name in dup_waste[:10]:
        print(f"  {waste/(1024**2):8.1f} MiB  x{copies:4d} copies  {name[:60]}")


if __name__ == "__main__":
    main()
