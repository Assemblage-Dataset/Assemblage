#!/usr/bin/env python3
"""Deduplicate binary files under deephistory/binaries/.

Policy (assemblage_meta.json is never modified):
  1. Within a prefix: for a SHA-256 dup group, keep the longest filename
     (the fully-versioned soname); mark shorter aliases for deletion.
  2. Across prefixes: for a group still spanning multiple prefixes, keep the
     copy in the lexicographically smallest prefix; mark the others for deletion.

Symlinks and assemblage_meta.json are skipped.

Defaults to a dry run. Pass --apply to actually delete.

Usage:
    # dry run
    python3 dedup_binaries.py

    # delete
    python3 dedup_binaries.py --apply

    # reuse hash cache between runs
    python3 dedup_binaries.py --hash-cache hashes.pkl
"""
import argparse
import hashlib
import os
import pickle
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
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                continue
            out.append(p)
    return out


def human(n: float) -> str:
    for u in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024:
            return f'{n:.2f} {u}'
        n /= 1024
    return f'{n:.2f} PiB'


def build_hash_map(files: list[str], workers: int
                   ) -> dict[str, list[tuple[str, int]]]:
    by_hash: dict[str, list[tuple[str, int]]] = defaultdict(list)
    total = len(files)
    done = 0
    report_every = max(1, total // 50)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(sha256_file, p) for p in files]
        for fut in as_completed(futs):
            try:
                path, digest, size = fut.result()
            except Exception as e:
                print(f'  skip (error): {e}', flush=True)
                done += 1
                continue
            by_hash[digest].append((path, size))
            done += 1
            if done % report_every == 0 or done == total:
                print(f'  hashed {done}/{total} ({100 * done / total:.1f}%)',
                      flush=True)
    return dict(by_hash)


def pick_deletions(by_hash: dict[str, list[tuple[str, int]]]
                   ) -> list[tuple[str, int]]:
    """Return list of (path, size) to delete. Applies the two-pass policy."""
    deletions: list[tuple[str, int]] = []
    for digest, entries in by_hash.items():
        if len(entries) < 2:
            continue
        # group by prefix (parent dir name)
        by_prefix: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for path, size in entries:
            prefix = os.path.basename(os.path.dirname(path))
            by_prefix[prefix].append((path, size))

        # Pass 1: within each prefix, keep longest basename
        prefix_survivors: dict[str, tuple[str, int]] = {}
        for prefix, plist in by_prefix.items():
            plist.sort(key=lambda ps: (-len(os.path.basename(ps[0])), ps[0]))
            prefix_survivors[prefix] = plist[0]
            for loser in plist[1:]:
                deletions.append(loser)

        # Pass 2: across prefixes, keep the lex-smallest prefix's survivor
        if len(prefix_survivors) > 1:
            best = min(prefix_survivors)
            for prefix, survivor in prefix_survivors.items():
                if prefix != best:
                    deletions.append(survivor)
    return deletions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?',
                    default='/home/cliu57/research/Assemblage/deephistory/binaries')
    ap.add_argument('--workers', type=int,
                    default=min(32, (os.cpu_count() or 4) * 2))
    ap.add_argument('--hash-cache',
                    help='Path to a pickle file to load/save {path: (sha256, size)}')
    ap.add_argument('--apply', action='store_true',
                    help='Actually delete. Omit for dry-run.')
    ap.add_argument('--show', type=int, default=15,
                    help='Sample N deletions to show in dry-run (default 15)')
    ap.add_argument('--log', default='dedup_deletions.log',
                    help='Write list of deleted paths to this file')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f'error: {root} is not a directory', file=sys.stderr)
        return 2

    # Try cache first
    by_hash: dict[str, list[tuple[str, int]]] | None = None
    if args.hash_cache and os.path.isfile(args.hash_cache):
        try:
            with open(args.hash_cache, 'rb') as f:
                cached = pickle.load(f)
            by_hash = defaultdict(list)
            for path, (digest, size) in cached.items():
                if os.path.isfile(path) and not os.path.islink(path):
                    by_hash[digest].append((path, size))
            by_hash = dict(by_hash)
            print(f'Loaded {sum(len(v) for v in by_hash.values())} entries from '
                  f'cache {args.hash_cache}', flush=True)
        except Exception as e:
            print(f'cache load failed ({e}); rehashing', flush=True)
            by_hash = None

    if by_hash is None:
        print(f'Scanning {root} ...', flush=True)
        files = collect_files(root)
        print(f'Hashing {len(files)} files (workers={args.workers}) ...',
              flush=True)
        by_hash = build_hash_map(files, args.workers)

        if args.hash_cache:
            flat = {path: (digest, size)
                    for digest, entries in by_hash.items()
                    for path, size in entries}
            with open(args.hash_cache, 'wb') as f:
                pickle.dump(flat, f)
            print(f'Saved {len(flat)} hashes to {args.hash_cache}', flush=True)

    deletions = pick_deletions(by_hash)
    total_files = sum(len(v) for v in by_hash.values())
    bytes_reclaim = sum(s for _, s in deletions)

    print()
    print('=' * 60)
    print(f'Mode:                     {"APPLY" if args.apply else "DRY-RUN"}')
    print(f'Total files considered:   {total_files}')
    print(f'Unique content hashes:    {len(by_hash)}')
    print(f'Files to delete:          {len(deletions)}')
    print(f'Space to reclaim:         {human(bytes_reclaim)}')
    print('=' * 60)

    if args.show > 0 and deletions:
        print(f'\nSample of {min(args.show, len(deletions))} deletions:')
        for p, sz in deletions[:args.show]:
            print(f'  {human(sz):>12}  {os.path.relpath(p, root)}')

    if not args.apply:
        print('\nDry run — no files deleted. Pass --apply to actually delete.')
        return 0

    print(f'\nDeleting {len(deletions)} files ...', flush=True)
    removed = 0
    errors = 0
    with open(args.log, 'w') as logf:
        for p, _ in deletions:
            try:
                os.remove(p)
                logf.write(p + '\n')
                removed += 1
                if removed % 1000 == 0:
                    print(f'  removed {removed}/{len(deletions)}', flush=True)
            except FileNotFoundError:
                removed += 1  # already gone; count as done
            except Exception as e:
                errors += 1
                print(f'  error: {p}: {e}', flush=True)
    print(f'Done. removed={removed} errors={errors} '
          f'reclaimed={human(bytes_reclaim)}  log={args.log}')
    return 0 if errors == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
