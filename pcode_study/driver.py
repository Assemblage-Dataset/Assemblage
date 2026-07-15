"""Run Ghidra headless across a list of binaries, in parallel, collecting per-binary
P-code op stats. Each binary produces one JSON in OUTPUT_DIR."""

import argparse
import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent
GHIDRA = os.environ.get(
    "GHIDRA_HOME", "/home/cliu57/research/compcert-ascent/ghidra/ghidra_12.0.3_PUBLIC"
)
HEADLESS = Path(GHIDRA) / "support" / "analyzeHeadless"
SCRIPT_DIR = ROOT / "scripts"
OUTPUT_DIR = ROOT / "output"
PROJECT_BASE = ROOT / "projects"
LOG_DIR = ROOT / "logs"

for d in (OUTPUT_DIR, PROJECT_BASE, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


def binary_slug(path: Path) -> str:
    """Slug from path relative to .../binaries/. Append md5(path)[:8] for uniqueness."""
    parts = path.parts
    try:
        idx = parts.index("binaries")
        rel = parts[idx + 1 :]
    except ValueError:
        rel = parts[-4:]
    base = "_".join(rel).replace(" ", "_").replace("/", "_")
    if len(base) > 120:
        base = base[:120]
    h = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]
    return "{}__{}".format(base, h)


def process_binary(binary_path_str: str, timeout: int = 600) -> dict:
    binary_path = Path(binary_path_str)
    slug = binary_slug(binary_path)
    proj_dir = PROJECT_BASE / ("p_" + uuid.uuid4().hex[:12])
    proj_dir.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / (slug + ".json")
    log_path = LOG_DIR / (slug + ".log")

    if output_path.exists():
        shutil.rmtree(proj_dir, ignore_errors=True)
        return {"binary": str(binary_path), "slug": slug, "ok": True, "skipped_existing": True}

    cmd = [
        str(HEADLESS),
        str(proj_dir),
        "tmp",
        "-import", str(binary_path),
        "-readOnly",
        "-scriptPath", str(SCRIPT_DIR),
        "-postScript", "PcodeStats.py", str(output_path),
        "-deleteProject",
    ]

    t0 = time.time()
    rc = None
    err = None
    try:
        proc = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
        )
        rc = proc.returncode
        with open(log_path, "wb") as f:
            f.write(b"CMD: " + " ".join(cmd).encode("utf-8") + b"\n\n")
            f.write(b"STDOUT:\n")
            f.write(proc.stdout)
            f.write(b"\n\nSTDERR:\n")
            f.write(proc.stderr)
    except subprocess.TimeoutExpired:
        err = "timeout"
    except Exception as e:
        err = "exception: " + repr(e)
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)

    elapsed = time.time() - t0
    ok = (rc == 0) and output_path.exists() and err is None
    return {
        "binary": str(binary_path),
        "slug": slug,
        "ok": ok,
        "rc": rc,
        "err": err,
        "elapsed_s": elapsed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", required=True, help="text file with one binary path per line")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--timeout", type=int, default=600,
                    help="per-binary timeout (s)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only run first N entries (0=all)")
    args = ap.parse_args()

    with open(args.sample) as f:
        paths = [line.strip() for line in f if line.strip()]
    if args.limit:
        paths = paths[: args.limit]

    print("Running %d binaries with %d workers (timeout %ds)" %
          (len(paths), args.workers, args.timeout), flush=True)

    n_ok = 0
    n_fail = 0
    n_done = 0
    fail_list = []
    t_start = time.time()

    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_binary, p, args.timeout): p for p in paths}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            n_done += 1
            if res["ok"]:
                n_ok += 1
            else:
                n_fail += 1
                fail_list.append(res)
            if n_done % 25 == 0 or n_done == len(paths):
                elapsed = time.time() - t_start
                rate = n_done / elapsed if elapsed else 0
                eta = (len(paths) - n_done) / rate if rate else 0
                print(
                    "[%d/%d] ok=%d fail=%d elapsed=%.0fs rate=%.2f/s eta=%.0fs"
                    % (n_done, len(paths), n_ok, n_fail, elapsed, rate, eta),
                    flush=True,
                )

    print("Done. ok=%d fail=%d total=%d in %.0fs" %
          (n_ok, n_fail, len(paths), time.time() - t_start), flush=True)
    if fail_list[:10]:
        print("First 10 failures:")
        for r in fail_list[:10]:
            print("  ", r.get("slug"), r.get("err") or "rc=%s" % r.get("rc"))


if __name__ == "__main__":
    main()
