#!/usr/bin/env python3
"""Smoke-test the fixed extract_dwarf_info on a few binaries from our 92-binary
sample, comparing against the buggy data currently in DuckDB.

Usage: smoke_test_extractor.py [<binary_id> ...]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Make the fixed extractor importable
sys.path.insert(0, "/home/cliu57/research/Assemblage/Assemblage_dataset_cli")

# Stub modules minio_pipeline imports but doesn't need for extract_dwarf_info.
# The repo has a moved dataset_orm.py, so we mock the missing names.
import types  # noqa: E402

_stub_orm = types.ModuleType("dataset_orm")
_stub_orm.migrate_existing_db = lambda *_a, **_kw: None
_stub_orm.init_clean_database = lambda *_a, **_kw: None
sys.modules.setdefault("dataset_orm_stub", _stub_orm)

# Patch the actual dataset_orm to add the missing names if absent.
import dataset_orm  # noqa: E402
if not hasattr(dataset_orm, "migrate_existing_db"):
    dataset_orm.migrate_existing_db = lambda *_a, **_kw: None

_stub_db = types.ModuleType("db")
class _StubDB:
    def __init__(self, *_a, **_kw): pass
    def bulk_add_repos(self, *_a, **_kw): pass
    def bulk_add_assembly_files(self, *_a, **_kw): pass
    def shutdown(self, *_a, **_kw): pass
_stub_db.Dataset_DB = _StubDB
sys.modules["db"] = _stub_db

# Fix the module attribute that minio_pipeline.py also pulls in.
os.environ.setdefault("DWARF_TIMEOUT_SECS", "120")  # generous for big binaries
import minio_pipeline  # noqa: E402

import duckdb  # noqa: E402

VAL_DIR = Path(__file__).resolve().parent
DUCKDB_PATH = "/home/cliu57/research/Assemblage/data_refill/linux_licensed.duckdb"


def report_one(bid: int) -> dict:
    data_path = VAL_DIR / f"binary_{bid}.json"
    if not data_path.exists():
        return {"bid": bid, "error": "no data file"}
    data = json.loads(data_path.read_text())
    elf_path = data["binary_path_on_disk"]
    if not os.path.isfile(elf_path):
        return {"bid": bid, "error": f"missing ELF at {elf_path}"}

    print(f"=== binary {bid} ({data['binary']['file_name']}) ===", flush=True)
    print(f"  ELF: {elf_path}")
    print(f"  build_mode={data['binary']['build_mode']!r} compiler={data['binary']['toolset_version']!r} opt={data['binary']['optimization']!r}")
    print(f"  DB has: {data['n_functions']} functions, {data['n_rvas']} rvas, {data['n_lines']} lines")

    t0 = time.time()
    new = minio_pipeline.extract_dwarf_info(elf_path)
    elapsed = time.time() - t0
    print(f"  fixed extractor: {elapsed:.2f}s", flush=True)

    if new is None:
        print("  ⚠ fixed extractor returned None (timeout or no DWARF)")
        return {"bid": bid, "error": "extractor returned None"}

    funcs_new = new["functions"]
    n_new_funcs = len(funcs_new)
    n_new_lines = sum(len(f["lines"]) for f in funcs_new)
    n_new_ranges = sum(len(f["function_info"]) for f in funcs_new)
    print(f"  fixed: {n_new_funcs} functions, {n_new_ranges} rvas, {n_new_lines} lines")

    # Compare names
    db_names = {f["name"] for f in data["functions"] if f["name"]}
    new_names = {f["function_name"] for f in funcs_new}
    only_db = db_names - new_names
    only_new = new_names - db_names

    db_len16 = sum(1 for n in db_names if len(n) == 16)
    new_len16 = sum(1 for n in new_names if len(n) == 16)
    print(f"  names exactly 16 chars: DB={db_len16}/{len(db_names)} → fixed={new_len16}/{len(new_names)}")

    # Spot-check RVA end accuracy: pick a function that exists in both
    common = list(db_names & new_names)[:5]
    if common:
        print("  RVA end comparison (sample):")
        new_by_name = {f["function_name"]: f for f in funcs_new}
        db_funcs_by_name = {f["name"]: f for f in data["functions"]}
        rvas_by_fid = {}
        for r in data["rvas"]:
            rvas_by_fid.setdefault(r["function_id"], []).append(r)
        for nm in common:
            db_func = db_funcs_by_name.get(nm)
            if not db_func or db_func["id"] not in rvas_by_fid:
                continue
            db_rva = rvas_by_fid[db_func["id"]][0]
            new_func = new_by_name[nm]
            new_rng = new_func["function_info"][0]
            db_size = db_rva["end"] - db_rva["start"]
            new_size = int(new_rng["rva_end"], 16) - int(new_rng["rva_start"], 16)
            agree = "✓" if db_size == new_size else f"DB={db_size} fixed={new_size}"
            print(f"    {nm[:40]:<40} start=0x{db_rva['start']:x} {agree}")

    return {
        "bid": bid,
        "old_funcs": data["n_functions"],
        "new_funcs": n_new_funcs,
        "old_lines": data["n_lines"],
        "new_lines": n_new_lines,
        "old_len16": db_len16,
        "new_len16": new_len16,
        "name_overlap": len(db_names & new_names),
        "names_only_db": len(only_db),
        "names_only_new": len(only_new),
    }


def main() -> int:
    if len(sys.argv) > 1:
        bids = [int(x) for x in sys.argv[1:]]
    else:
        # Default: try a broken binary (build_mode='') and a working one (RelWithDebInfo)
        bids = [165223, 428376, 549596 if False else 545109]

    summaries = []
    for bid in bids:
        try:
            summaries.append(report_one(bid))
        except Exception as e:
            print(f"  ERROR on {bid}: {e}", flush=True)
            summaries.append({"bid": bid, "error": str(e)})
        print()

    print("=== summary ===")
    for s in summaries:
        print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
