"""Spawn N PyGhidra workers in parallel; each gets a chunk of the sample.

Each worker is a separate Python process running worker.py — one JVM per worker,
not per binary. Workers stream progress to stdout, mixed into one log file.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "logs"
CHUNK_DIR = ROOT / "chunks"
LOG_DIR.mkdir(parents=True, exist_ok=True)
CHUNK_DIR.mkdir(parents=True, exist_ok=True)

GHIDRA_HOME = os.environ.get(
    "GHIDRA_HOME", "/home/cliu57/research/compcert-ascent/ghidra/ghidra_12.0.3_PUBLIC"
)
PYTHON = os.environ.get("PCODE_PYTHON", "/home/cliu57/anaconda3/bin/python3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--per-fn-timeout", type=int, default=60)
    ap.add_argument("--per-bin-timeout", type=int, default=300)
    args = ap.parse_args()

    with open(args.sample) as f:
        binaries = [line.strip() for line in f if line.strip()]
    n = len(binaries)
    nw = args.workers
    chunks = [[] for _ in range(nw)]
    for i, b in enumerate(binaries):
        chunks[i % nw].append(b)

    chunk_files = []
    for i, chunk in enumerate(chunks):
        cf = CHUNK_DIR / ("chunk_%02d.txt" % i)
        cf.write_text("\n".join(chunk) + "\n")
        chunk_files.append(cf)

    print("Total binaries: %d across %d workers (avg %.1f / worker)"
          % (n, nw, n / nw), flush=True)

    env = os.environ.copy()
    env["GHIDRA_INSTALL_DIR"] = GHIDRA_HOME
    env["PCODE_OUT_DIR"] = args.out
    env["PCODE_PER_FN_TIMEOUT"] = str(args.per_fn_timeout)
    env["PCODE_PER_BIN_TIMEOUT"] = str(args.per_bin_timeout)

    procs = []
    log_paths = []
    t0 = time.time()
    for i, cf in enumerate(chunk_files):
        log_path = LOG_DIR / ("worker_%02d.log" % i)
        log_paths.append(log_path)
        f = open(log_path, "wb")
        p = subprocess.Popen(
            [PYTHON, str(ROOT / "worker.py"), str(cf)],
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
        )
        procs.append((p, f, i))
        print("spawned worker %d pid=%d (chunk %d, %d binaries)"
              % (i, p.pid, i, len(chunks[i])), flush=True)

    while any(p.poll() is None for p, _, _ in procs):
        time.sleep(15)
        # Count outputs to give progress
        out_dir = Path(args.out)
        n_done = len(list(out_dir.glob("*.json")))
        elapsed = time.time() - t0
        rate = n_done / elapsed if elapsed > 0 else 0
        alive = sum(1 for p, _, _ in procs if p.poll() is None)
        eta = (n - n_done) / rate if rate > 0 else float("inf")
        print("[main] elapsed=%.0fs workers_alive=%d outputs=%d/%d rate=%.2f/s eta=%.0fs"
              % (elapsed, alive, n_done, n, rate, eta), flush=True)

    for p, f, i in procs:
        f.close()
    rcs = [p.returncode for p, _, _ in procs]
    print("[main] all workers exited. rcs=%s elapsed=%.0fs"
          % (rcs, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
