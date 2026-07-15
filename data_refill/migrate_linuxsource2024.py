#!/usr/bin/env python3
"""
Migrate data_refill/2024/linuxsource/<md5>/  ->  data_refill/linuxsource/<user>/<repo>/<commit>.tar.gz

Mapping md5 -> URL is built from DISTINCT github_url in linux.sqlite.
Commit is the short=12 HEAD of the .git inside each <md5> dir.

Behavior:
  - skip if target tar.gz already exists (do not delete source)
  - on successful tar, rm -rf source
  - leave unmatched (md5 not in db) folders alone
"""

import argparse
import hashlib
import os
import shutil
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SRC_ROOT = ROOT / "2024" / "linuxsource"
DST_ROOT = ROOT / "linuxsource"
SQLITE_PATH = ROOT / "linux.sqlite"
LOG_PATH = ROOT / "migrate_linuxsource2024.log"


def md5_of(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def normalize_url(u: str) -> str:
    u = u.strip()
    if u.endswith(".git"):
        u = u[:-4]
    if u.endswith("/"):
        u = u[:-1]
    return u


def parse_user_repo(url: str):
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


def short_commit(git_dir):
    try:
        out = subprocess.run(
            ["git", "-C", str(git_dir), "rev-parse", "--short=12", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        sha = out.stdout.strip()
        if len(sha) == 12 and all(c in "0123456789abcdef" for c in sha):
            return sha
        return None
    except Exception:
        return None


def build_mapping():
    con = sqlite3.connect(str(SQLITE_PATH))
    urls = [r[0] for r in con.execute("SELECT DISTINCT github_url FROM binaries").fetchall()]
    con.close()
    mapping = {}
    for u in urls:
        if not u:
            continue
        n = normalize_url(u)
        mapping[md5_of(n)] = n
    return mapping


def process_one(md5: str, url: str):
    src = SRC_ROOT / md5
    if not src.is_dir():
        return ("missing_src", md5, url, "")
    user, repo = parse_user_repo(url)
    if not user or not repo:
        return ("bad_url", md5, url, "")
    commit = short_commit(src)
    if not commit:
        return ("no_commit", md5, url, "")
    dst_dir = DST_ROOT / user / repo
    dst = dst_dir / f"{commit}.tar.gz"
    if dst.exists():
        return ("skip_exists", md5, url, str(dst))
    dst_dir.mkdir(parents=True, exist_ok=True)
    tmp = dst_dir / f".{commit}.tar.gz.tmp.{os.getpid()}"
    try:
        rc = subprocess.run(
            ["tar", "-czf", str(tmp), "-C", str(src), "."],
            capture_output=True,
            text=True,
        )
        if rc.returncode != 0:
            if tmp.exists():
                tmp.unlink()
            return ("tar_fail", md5, url, rc.stderr.strip()[:200])
        os.replace(tmp, dst)
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return ("exception", md5, url, repr(e)[:200])
    # tar exited clean -> remove source
    try:
        shutil.rmtree(src)
    except Exception as e:
        return ("rm_fail", md5, url, repr(e)[:200])
    return ("done", md5, url, str(dst))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="process at most N entries (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mapping = build_mapping()
    existing = set(os.listdir(SRC_ROOT))
    work = [(h, u) for h, u in mapping.items() if h in existing]
    work.sort()
    if args.limit:
        work = work[: args.limit]

    print(f"distinct urls in db: {len(mapping)}")
    print(f"folders in {SRC_ROOT.name}: {len(existing)}")
    print(f"matched (to process): {len(work)}")
    if args.dry_run:
        for h, u in work[:10]:
            print(f"  would process {h} -> {u}")
        return

    counts = {
        "done": 0,
        "skip_exists": 0,
        "tar_fail": 0,
        "rm_fail": 0,
        "missing_src": 0,
        "bad_url": 0,
        "no_commit": 0,
        "exception": 0,
    }
    t0 = time.time()
    with open(LOG_PATH, "a") as logf:
        logf.write(
            f"--- run start {time.strftime('%Y-%m-%d %H:%M:%S')} workers={args.workers} total={len(work)} ---\n"
        )
        logf.flush()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(process_one, h, u): (h, u) for h, u in work}
            done_n = 0
            for fut in as_completed(futs):
                status, md5, url, info = fut.result()
                counts[status] = counts.get(status, 0) + 1
                logf.write(f"{status}\t{md5}\t{url}\t{info}\n")
                done_n += 1
                if done_n % 50 == 0:
                    logf.flush()
                    elapsed = time.time() - t0
                    rate = done_n / elapsed if elapsed else 0
                    eta = (len(work) - done_n) / rate if rate else 0
                    summary = " ".join(f"{k}={v}" for k, v in counts.items() if v)
                    print(
                        f"[{done_n}/{len(work)}] {summary}  rate={rate:.1f}/s  eta={eta / 60:.1f}m"
                    )
        logf.write(f"--- run end {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    elapsed = time.time() - t0
    print(f"\nFinished in {elapsed / 60:.1f}m")
    for k, v in counts.items():
        if v:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
