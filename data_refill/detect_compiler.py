#!/usr/bin/env python3
"""Detect the compiler (gcc/clang) for binaries with empty toolset_version.

Reads (id, path) pairs from linux_licensed.duckdb where toolset_version is
NULL or empty, parses each binary's DWARF DW_AT_producer (with .comment as
fallback), and writes results to compiler_detect.jsonl in the same dir.

The .comment section is unreliable on its own because clang binaries also
embed "GCC: (Ubuntu ...)" strings from libc startup objects, so DWARF is the
authoritative source. .comment is only used when DWARF is unavailable.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import duckdb
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parent
BIN_ROOT = ROOT / "binaries"
DB_PATH = ROOT / "linux_licensed.duckdb"
OUT_PATH = ROOT / "compiler_detect.jsonl"

MAX_CUS_SCAN = 8


def classify_producer(val: str) -> str:
    low = val.lower()
    if "clang" in low:
        return "clang"
    if low.startswith("gnu") or "gcc" in low:
        return "gcc"
    return "other"


def detect_one(args):
    bid, rel_path = args
    full = BIN_ROOT / rel_path
    try:
        with open(full, "rb") as f:
            head = f.read(4)
            if head[:4] != b"\x7fELF":
                return {"id": bid, "compiler": "not_elf", "evidence": head.hex()}
            f.seek(0)
            elf = ELFFile(f)
            producer = None
            if elf.has_dwarf_info():
                dw = elf.get_dwarf_info()
                for i, cu in enumerate(dw.iter_CUs()):
                    if i >= MAX_CUS_SCAN:
                        break
                    die = cu.get_top_DIE()
                    prod = die.attributes.get("DW_AT_producer")
                    if prod is None:
                        continue
                    val = prod.value
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", errors="replace")
                    producer = val
                    break
            if producer is not None:
                return {
                    "id": bid,
                    "compiler": classify_producer(producer),
                    "source": "dwarf",
                    "evidence": producer[:200],
                }
            sec = elf.get_section_by_name(".comment")
            if sec is not None:
                data = sec.data().decode("utf-8", errors="replace")
                tokens = [t for t in data.split("\x00") if t]
                joined = " | ".join(tokens)
                low = joined.lower()
                if "clang" in low:
                    return {
                        "id": bid,
                        "compiler": "clang",
                        "source": "comment",
                        "evidence": joined[:200],
                    }
                if "gcc" in low:
                    return {
                        "id": bid,
                        "compiler": "gcc",
                        "source": "comment",
                        "evidence": joined[:200],
                    }
                return {
                    "id": bid,
                    "compiler": "unknown",
                    "source": "comment",
                    "evidence": joined[:200],
                }
            return {"id": bid, "compiler": "unknown", "source": "no_dwarf_no_comment"}
    except FileNotFoundError:
        return {"id": bid, "compiler": "missing"}
    except Exception as e:
        return {"id": bid, "compiler": "error", "evidence": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all rows")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    q = "SELECT id, path FROM binaries WHERE toolset_version IS NULL OR toolset_version = ''"
    if args.limit:
        q += f" LIMIT {args.limit}"
    rows = con.execute(q).fetchall()
    con.close()
    print(f"[detect] {len(rows)} binaries to process, {args.workers} workers", flush=True)

    counts = {}
    t0 = time.time()
    written = 0
    with open(args.out, "w") as out, mp.Pool(args.workers) as pool:
        for r in pool.imap_unordered(detect_one, rows, chunksize=64):
            out.write(json.dumps(r) + "\n")
            counts[r["compiler"]] = counts.get(r["compiler"], 0) + 1
            written += 1
            if written % 5000 == 0:
                elapsed = time.time() - t0
                rate = written / elapsed
                eta = (len(rows) - written) / rate if rate > 0 else 0
                print(
                    f"[detect] {written}/{len(rows)}  {rate:.0f}/s  eta {eta:.0f}s  {counts}",
                    flush=True,
                )

    print(f"[detect] done in {time.time()-t0:.1f}s -> {args.out}", flush=True)
    print(f"[detect] counts: {counts}", flush=True)


if __name__ == "__main__":
    main()
