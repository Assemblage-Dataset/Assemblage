"""Long-lived PyGhidra worker. Reads binary paths from a file (one per line),
analyzes each, writes per-binary JSON to OUTPUT_DIR.

Designed to be spawned multiple times in parallel by run_2000.py — each instance
owns a JVM, so spawning N workers gives ~N-way parallelism with one JVM startup
per worker (not per binary).

Required env:
    GHIDRA_INSTALL_DIR
    PCODE_OUT_DIR

Optional env:
    PCODE_PER_FN_TIMEOUT (s, default 60)
    PCODE_PER_BIN_TIMEOUT (s, default 300)
"""

import hashlib
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

OUT_DIR = Path(os.environ["PCODE_OUT_DIR"])
OUT_DIR.mkdir(parents=True, exist_ok=True)
PER_FN_TIMEOUT = int(os.environ.get("PCODE_PER_FN_TIMEOUT", "60"))
PER_BIN_TIMEOUT = float(os.environ.get("PCODE_PER_BIN_TIMEOUT", "300"))

import pyghidra  # noqa: E402

pyghidra.start()

from ghidra.app.decompiler import DecompInterface, DecompileOptions  # noqa: E402
from ghidra.util.task import ConsoleTaskMonitor  # noqa: E402


def binary_slug(path: Path) -> str:
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


def process_binary(binary_path: Path, out_path: Path) -> dict:
    if out_path.exists():
        return {"ok": True, "skipped": True}
    t0 = time.time()
    n_dec = 0
    n_failed = 0
    n_skipped = 0
    truncated = False

    with pyghidra.open_program(str(binary_path), analyze=True) as flat:
        program = flat.getCurrentProgram()
        decomp = DecompInterface()
        opts = DecompileOptions()
        decomp.setOptions(opts)
        decomp.setSimplificationStyle("decompile")
        if not decomp.openProgram(program):
            return {"ok": False, "err": "decomp.openProgram failed"}
        try:
            monitor = ConsoleTaskMonitor()
            fn_mgr = program.getFunctionManager()

            result = {
                "binary": program.getName(),
                "binary_path": str(binary_path),
                "executable_md5": program.getExecutableMD5(),
                "language": str(program.getLanguage().getLanguageID()),
                "n_functions_total": 0,
                "n_decompiled": 0,
                "n_failed": 0,
                "n_skipped_external_or_thunk": 0,
                "truncated": False,
                "functions": [],
                "op_totals": {},
                "started_at": t0,
            }

            functions = list(fn_mgr.getFunctions(True))
            result["n_functions_total"] = len(functions)

            t_budget_end = t0 + PER_BIN_TIMEOUT
            for fn in functions:
                if time.time() > t_budget_end:
                    truncated = True
                    break
                if fn.isExternal() or fn.isThunk():
                    n_skipped += 1
                    continue
                body = fn.getBody()
                size_bytes = body.getNumAddresses() if body is not None else 0
                try:
                    dr = decomp.decompileFunction(fn, PER_FN_TIMEOUT, monitor)
                except Exception:
                    n_failed += 1
                    continue
                if dr is None or not dr.decompileCompleted():
                    n_failed += 1
                    continue
                high = dr.getHighFunction()
                if high is None:
                    n_failed += 1
                    continue
                op_counts = {}
                n_ops = 0
                it = high.getPcodeOps()
                while it.hasNext():
                    op = it.next()
                    m = op.getMnemonic()
                    op_counts[m] = op_counts.get(m, 0) + 1
                    n_ops += 1
                    result["op_totals"][m] = result["op_totals"].get(m, 0) + 1
                result["functions"].append({
                    "addr": str(fn.getEntryPoint()),
                    "name": fn.getName(),
                    "size_bytes": int(size_bytes),
                    "n_ops": n_ops,
                    "ops": op_counts,
                })
                n_dec += 1
        finally:
            decomp.dispose()

    result["n_decompiled"] = n_dec
    result["n_failed"] = n_failed
    result["n_skipped_external_or_thunk"] = n_skipped
    result["truncated"] = truncated
    result["finished_at"] = time.time()
    result["duration_s"] = result["finished_at"] - t0

    tmp_path = out_path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    tmp_path.replace(out_path)
    return {"ok": True, "n_dec": n_dec, "duration": result["duration_s"]}


def main():
    if len(sys.argv) < 2:
        print("usage: worker.py <binary_list_file>", file=sys.stderr)
        sys.exit(2)
    list_path = sys.argv[1]
    with open(list_path) as f:
        binaries = [line.strip() for line in f if line.strip()]
    pid = os.getpid()
    n = len(binaries)
    print("[worker %d] start, %d binaries" % (pid, n), flush=True)
    n_ok = n_fail = n_skip = 0
    t_start = time.time()
    for i, bp_str in enumerate(binaries):
        bp = Path(bp_str)
        slug = binary_slug(bp)
        out = OUT_DIR / (slug + ".json")
        t0 = time.time()
        try:
            r = process_binary(bp, out)
            if r.get("skipped"):
                n_skip += 1
            elif r.get("ok"):
                n_ok += 1
            else:
                n_fail += 1
                err_path = OUT_DIR / (slug + ".err")
                err_path.write_text(json.dumps(r))
        except Exception as e:
            n_fail += 1
            err_path = OUT_DIR / (slug + ".err")
            err_path.write_text("EXC: %s\n%s" % (e, traceback.format_exc()))
        if (i + 1) % 5 == 0 or (i + 1) == n:
            elapsed = time.time() - t_start
            rate = (i + 1) / elapsed if elapsed else 0
            print(
                "[worker %d] %d/%d ok=%d fail=%d skip=%d rate=%.2f/s last=%.1fs"
                % (pid, i + 1, n, n_ok, n_fail, n_skip, rate, time.time() - t0),
                flush=True,
            )
    print("[worker %d] DONE ok=%d fail=%d skip=%d total_time=%.0fs"
          % (pid, n_ok, n_fail, n_skip, time.time() - t_start), flush=True)


if __name__ == "__main__":
    main()
