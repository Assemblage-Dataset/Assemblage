# Ghidra Jython postScript - extract P-code op stats per function.
# @category Analysis
# Arg 1: absolute output JSON path.

import json
import time
import traceback

from ghidra.app.decompiler import DecompInterface, DecompileOptions
from ghidra.util.task import ConsoleTaskMonitor

DECOMP_TIMEOUT_S = 60

args = getScriptArgs()
if len(args) < 1:
    raise RuntimeError("PcodeStats: missing output path arg")
output_path = args[0]

prog = currentProgram
decomp = DecompInterface()
opts = DecompileOptions()
decomp.setOptions(opts)
decomp.setSimplificationStyle("decompile")
if not decomp.openProgram(prog):
    raise RuntimeError("decomp.openProgram failed: " + str(decomp.getLastMessage()))

monitor = ConsoleTaskMonitor()
fn_mgr = prog.getFunctionManager()

result = {
    "binary": prog.getName(),
    "executable_path": prog.getExecutablePath(),
    "executable_md5": prog.getExecutableMD5(),
    "language": str(prog.getLanguage().getLanguageID()),
    "n_functions_total": 0,
    "n_decompiled": 0,
    "n_failed": 0,
    "n_skipped_external_or_thunk": 0,
    "functions": [],
    "op_totals": {},
    "started_at": time.time(),
}

functions = list(fn_mgr.getFunctions(True))
result["n_functions_total"] = len(functions)

for fn in functions:
    if fn.isExternal() or fn.isThunk():
        result["n_skipped_external_or_thunk"] += 1
        continue
    body = fn.getBody()
    size_bytes = body.getNumAddresses() if body is not None else 0
    try:
        decomp_res = decomp.decompileFunction(fn, DECOMP_TIMEOUT_S, monitor)
    except Exception:
        result["n_failed"] += 1
        continue
    if decomp_res is None or not decomp_res.decompileCompleted():
        result["n_failed"] += 1
        continue
    high_fn = decomp_res.getHighFunction()
    if high_fn is None:
        result["n_failed"] += 1
        continue

    op_counts = {}
    n_ops = 0
    ops_iter = high_fn.getPcodeOps()
    while ops_iter.hasNext():
        op = ops_iter.next()
        m = op.getMnemonic()
        op_counts[m] = op_counts.get(m, 0) + 1
        n_ops += 1
        result["op_totals"][m] = result["op_totals"].get(m, 0) + 1

    result["functions"].append({
        "addr": str(fn.getEntryPoint()),
        "name": fn.getName(),
        "size_bytes": size_bytes,
        "n_ops": n_ops,
        "ops": op_counts,
    })
    result["n_decompiled"] += 1

result["finished_at"] = time.time()
result["duration_s"] = result["finished_at"] - result["started_at"]

f = open(output_path, "w")
try:
    json.dump(result, f, separators=(",", ":"))
finally:
    f.close()

print("PcodeStats: wrote %d functions (%d failed) -> %s" %
      (result["n_decompiled"], result["n_failed"], output_path))
