#!/usr/bin/env python3
"""Scan deephistory/binaries/ for duplicate binary files.

Hashes each file (excluding assemblage_meta.json) with SHA-256 in parallel and
reports duplicate groups plus summary stats.

Usage:
    python3 check_binary_duplicates.py [root_dir] [--workers N] [--show N]
"""
import argparse
import hashlib
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed


def sha256_file(path: str, chunk: int = 1 << 20) -> tuple[str, str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            size += len(b)
    return path, h.hexdigest(), size


def collect_files(root: str) -> list[str]:
    out = []
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn == 'assemblage_meta.json':
                continue
            out.append(os.path.join(dirpath, fn))
    return out


def human(n: int) -> str:
    for u in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024:
            return f'{n:.2f} {u}'
        n /= 1024
    return f'{n:.2f} PiB'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?',
                    default='/home/cliu57/research/Assemblage/deephistory/binaries')
    ap.add_argument('--workers', type=int, default=min(32, (os.cpu_count() or 4) * 2))
    ap.add_argument('--show', type=int, default=20,
                    help='Top-N duplicate groups to print')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f'error: {root} is not a directory', file=sys.stderr)
        return 2

    print(f'Scanning {root} ...', flush=True)
    files = collect_files(root)
    total = len(files)
    print(f'Found {total} files to hash (workers={args.workers})', flush=True)
    if total == 0:
        return 0

    by_hash: dict[str, list[tuple[str, int]]] = defaultdict(list)
    total_bytes = 0
    done = 0
    report_every = max(1, total // 50)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(sha256_file, p) for p in files]
        for fut in as_completed(futs):
            try:
                path, digest, size = fut.result()
            except Exception as e:
                print(f'  skip (error): {e}', flush=True)
                done += 1
                continue
            by_hash[digest].append((path, size))
            total_bytes += size
            done += 1
            if done % report_every == 0 or done == total:
                print(f'  hashed {done}/{total} ({100 * done / total:.1f}%)',
                      flush=True)

    dup_groups = {h: entries for h, entries in by_hash.items() if len(entries) > 1}
    unique_hashes = len(by_hash)
    dup_files = sum(len(v) for v in dup_groups.values())
    redundant = sum(len(v) - 1 for v in dup_groups.values())
    redundant_bytes = sum((len(v) - 1) * v[0][1] for v in dup_groups.values())

    print()
    print('=' * 60)
    print(f'Total files scanned:     {total}')
    print(f'Total bytes:             {human(total_bytes)}')
    print(f'Unique content hashes:   {unique_hashes}')
    print(f'Duplicate groups (>=2):  {len(dup_groups)}')
    print(f'Files inside dup groups: {dup_files}')
    print(f'Redundant copies:        {redundant}  '
          f'({human(redundant_bytes)} reclaimable)')
    print('=' * 60)

    if dup_groups and args.show > 0:
        top = sorted(dup_groups.items(),
                     key=lambda kv: (-len(kv[1]), -kv[1][0][1]))[:args.show]
        print(f'\nTop {len(top)} duplicate groups:')
        for digest, entries in top:
            size = entries[0][1]
            print(f'\n  sha256={digest[:16]}...  copies={len(entries)}  '
                  f'size={human(size)}')
            for p, _ in entries[:5]:
                print(f'    {os.path.relpath(p, root)}')
            if len(entries) > 5:
                print(f'    ... and {len(entries) - 5} more')

    return 0


if __name__ == '__main__':
    sys.exit(main())
