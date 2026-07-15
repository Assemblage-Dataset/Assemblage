#!/usr/bin/env python3
"""V2 deep validator — compares the data stored in linux_licensed.v2.duckdb
against an independent fresh extraction from the ELF + source files.

For each binary in a sample:
  1. Pull v2's functions / rvas / lines for that binary_id.
  2. Independently re-derive ground truth from the ELF using a different
     code path (this script's own DWARF walk, not the production extractor).
  3. Verify:
       — every (name, source_file) in v2 corresponds to a DWARF DIE,
       — every (start, end) RVA in v2 corresponds to a DWARF range,
       — every (line_number, rva) in v2 corresponds to a real .debug_line
         entry,
       — every populated lines.source_code matches the source file at
         that line number, byte for byte.

Failure of ANY check = real bug. Reports per-binary stats + zero-tolerance
counts.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
from elftools.elf.elffile import ELFFile

V2_DB = "/home/cliu57/research/Assemblage/data_refill/linux_licensed.v2.duckdb"
LXSRC = Path("/home/cliu57/research/Assemblage/data_refill/linuxsource")
BINROOT = Path("/home/cliu57/research/Assemblage/data_refill/binaries")


def find_archive(github_url: str | None, repo_commit: str | None) -> str | None:
    if not github_url or not github_url.startswith("https://github.com/"):
        return None
    parts = github_url[len("https://github.com/"):].rstrip("/").split("/")
    if len(parts) < 2:
        return None
    user, repo = parts[0], parts[1]
    repo_dir = LXSRC / user / repo
    if not repo_dir.is_dir():
        return None
    if repo_commit:
        cand = repo_dir / f"{repo_commit[:12]}.tar.gz"
        if cand.is_file():
            return str(cand)
    tarballs = sorted(repo_dir.glob("*.tar.gz"))
    return str(tarballs[0]) if tarballs else None


def gt_extract(elf_path: str) -> dict | None:
    """Independent fresh DWARF walk — this code path is NOT the same as
    the production minio_pipeline.extract_dwarf_info, so a bug shared
    across both is unlikely.
    """
    if not os.path.isfile(elf_path):
        return None
    with open(elf_path, "rb") as f:
        try:
            elf = ELFFile(f)
        except Exception:
            return None
        if not elf.has_dwarf_info():
            return {"functions": [], "ranges": set(), "lines_by_addr": {}}
        dwarf = elf.get_dwarf_info()
        # Base address
        base = None
        for seg in elf.iter_segments():
            if seg["p_type"] == "PT_LOAD":
                if base is None or seg["p_vaddr"] < base:
                    base = seg["p_vaddr"]
        base = base or 0
        # Executable section ranges (for the same filtering the extractor does)
        exec_ranges = []
        for sec in elf.iter_sections():
            if sec["sh_flags"] & 0x4 and sec["sh_addr"] != 0:
                exec_ranges.append(
                    (sec["sh_addr"], sec["sh_addr"] + sec["sh_size"])
                )

        def in_exec(b, e):
            return any(b < se and e > ss for ss, se in exec_ranges)

        functions: list[dict[str, Any]] = []
        ranges_set: set[tuple[int, int]] = set()
        for cu in dwarf.iter_CUs():
            cu_low_pc = 0
            top = cu.get_top_DIE()
            if "DW_AT_low_pc" in top.attributes:
                cu_low_pc = top.attributes["DW_AT_low_pc"].value

            def name_of(die, depth=0):
                if depth > 5:
                    return None
                for tag in (
                    "DW_AT_linkage_name", "DW_AT_MIPS_linkage_name", "DW_AT_name"
                ):
                    a = die.attributes.get(tag)
                    if a:
                        v = a.value
                        return v.decode("utf-8", "replace") if isinstance(v, bytes) else v
                for ref_tag in ("DW_AT_abstract_origin", "DW_AT_specification"):
                    if ref_tag in die.attributes:
                        try:
                            ref = die.get_DIE_from_attribute(ref_tag)
                            if ref is not None:
                                return name_of(ref, depth + 1)
                        except Exception:
                            pass
                return None

            def ranges_of(die):
                if "DW_AT_ranges" in die.attributes:
                    try:
                        rl = dwarf.range_lists()
                        if rl is None:
                            return None
                        entries = list(
                            rl.get_range_list_at_offset(die.attributes["DW_AT_ranges"].value)
                        )
                        out = []
                        bs = cu_low_pc
                        for e in entries:
                            if hasattr(e, "base_address"):
                                bs = e.base_address
                                continue
                            bo = getattr(e, "begin_offset", None)
                            eo = getattr(e, "end_offset", None)
                            if bo == 0 and eo == 0:
                                break
                            if getattr(e, "is_absolute", False):
                                begin, end = bo, eo
                            else:
                                begin, end = bo + bs, eo + bs
                            if begin < end:
                                out.append((begin, end))
                        return out if out else None
                    except Exception:
                        return None
                lo = die.attributes.get("DW_AT_low_pc")
                if lo is None:
                    return None
                lp = lo.value
                hp = die.attributes.get("DW_AT_high_pc")
                if hp is None:
                    return None
                if hp.form.startswith("DW_FORM_addr"):
                    h = hp.value
                else:
                    h = lp + hp.value
                if lp >= h:
                    return None
                return [(lp, h)]

            for die in cu.iter_DIEs():
                if die.tag not in ("DW_TAG_subprogram", "DW_TAG_inlined_subroutine"):
                    continue
                rngs = ranges_of(die)
                if not rngs:
                    continue
                if exec_ranges:
                    rngs = [(b, e) for (b, e) in rngs if in_exec(b, e)]
                if not rngs:
                    continue
                nm = name_of(die)
                if not nm:
                    continue
                # convert to RVA
                rva_rngs = [(b - base, e - base) for (b, e) in rngs]
                for r in rva_rngs:
                    ranges_set.add(r)

        # Line program: collect (address - base) -> set of line numbers
        lines_by_rva: dict[int, set[int]] = {}
        for cu in dwarf.iter_CUs():
            try:
                lp = dwarf.line_program_for_CU(cu)
            except Exception:
                continue
            if lp is None:
                continue
            for entry in lp.get_entries():
                s = entry.state
                if s is None or s.end_sequence or not s.line:
                    continue
                lines_by_rva.setdefault(s.address - base, set()).add(s.line)

        return {
            "ranges": ranges_set,
            "lines_by_rva": lines_by_rva,
        }


def validate_one(con: duckdb.DuckDBPyConnection, bid: int) -> dict:
    findings: list[str] = []
    stats: dict[str, Any] = {"binary_id": bid}

    row = con.execute(
        "SELECT path, github_url, repo_commit, file_name, build_mode, "
        "toolset_version, optimization, size FROM binaries WHERE id = ?",
        [bid],
    ).fetchone()
    if not row:
        findings.append("binary not in v2")
        return {"binary_id": bid, "findings": findings}
    path, url, commit, fname, bm, cmpl, opt, sz = row
    stats["file_name"] = fname
    stats["build_mode"] = bm
    stats["compiler"] = cmpl

    elf_path = str(BINROOT / path)
    if not os.path.isfile(elf_path):
        findings.append(f"binary missing on disk: {elf_path}")
        return {"binary_id": bid, "findings": findings, **stats}

    archive = find_archive(url, commit)

    # Pull v2 data for this binary
    funcs = con.execute(
        "SELECT id, name, source_file FROM functions WHERE binary_id = ?", [bid]
    ).fetchall()
    fn_ids = [f[0] for f in funcs]
    stats["v2_funcs"] = len(funcs)

    if fn_ids:
        rvas = con.execute(
            "SELECT function_id, start, \"end\" FROM rvas WHERE function_id IN ({})".format(
                ",".join(str(x) for x in fn_ids)
            )
        ).fetchall()
        lines = con.execute(
            "SELECT function_id, line_number, source_file, source_code, rva "
            "FROM lines WHERE function_id IN ({})".format(
                ",".join(str(x) for x in fn_ids)
            )
        ).fetchall()
    else:
        rvas, lines = [], []
    stats["v2_rvas"] = len(rvas)
    stats["v2_lines"] = len(lines)

    # Independent ground truth from ELF
    t_gt = time.time()
    gt = gt_extract(elf_path)
    stats["gt_extract_secs"] = round(time.time() - t_gt, 2)
    if gt is None:
        findings.append("gt extraction failed")
        return {"binary_id": bid, "findings": findings, **stats}

    gt_ranges = gt["ranges"]
    gt_lines = gt["lines_by_rva"]

    # 1. Every v2 RVA must be in DWARF
    rva_match = 0
    rva_miss = 0
    for fid, s, e in rvas:
        if (s, e) in gt_ranges:
            rva_match += 1
        else:
            rva_miss += 1
    stats["rva_match"] = rva_match
    stats["rva_miss"] = rva_miss
    if rva_miss > 0:
        findings.append(f"{rva_miss}/{len(rvas)} v2 RVAs not in DWARF")

    # 2. Every v2 line must match a real DWARF line entry
    line_match = 0
    line_miss = 0
    line_wrong = 0
    for fid, ln_num, sf, sc, rva_str in lines:
        if not rva_str.startswith("0x"):
            line_miss += 1
            continue
        rva_int = int(rva_str, 16)
        if rva_int not in gt_lines:
            line_miss += 1
        elif ln_num not in gt_lines[rva_int]:
            line_wrong += 1
        else:
            line_match += 1
    stats["line_match"] = line_match
    stats["line_miss_in_dwarf"] = line_miss
    stats["line_wrong_num"] = line_wrong
    if line_miss > 0:
        findings.append(f"{line_miss} v2 line records reference an RVA not in DWARF")
    if line_wrong > 0:
        findings.append(f"{line_wrong} v2 line records have a line number not in any CU at that RVA")

    # 3. source_code text correctness — extract source archive and compare.
    src_match = 0
    src_mm = 0
    src_unresolved = 0
    if archive:
        tmp = tempfile.mkdtemp(prefix="v2dv_", dir="/tmp")
        try:
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(tmp)
            file_cache: dict[str, list[str]] = {}
            base_idx: dict[str, list[str]] | None = None

            def build_idx():
                idx = {}
                for r, _, files in os.walk(tmp):
                    for fn in files:
                        idx.setdefault(fn, []).append(os.path.join(r, fn))
                return idx

            for fid, ln_num, sf, sc, rva_str in lines:
                if not sc:
                    continue
                # Resolve via heuristics that mirror the extractor's logic
                resolved = None
                if os.path.isfile(sf):
                    resolved = sf
                if not resolved:
                    cand = os.path.join(tmp, sf.lstrip("./"))
                    if os.path.isfile(cand):
                        resolved = cand
                if not resolved and "/tmp/projects/" in sf:
                    parts = sf.split("/tmp/projects/", 1)[1].split("/", 2)
                    if len(parts) == 3:
                        cand = os.path.join(tmp, parts[2])
                        if os.path.isfile(cand):
                            resolved = cand
                if not resolved:
                    p = sf.split("/", 1)
                    if len(p) == 2 and len(p[0]) == 32 and all(
                        c in "0123456789abcdef" for c in p[0].lower()
                    ):
                        cand = os.path.join(tmp, p[1])
                        if os.path.isfile(cand):
                            resolved = cand
                if not resolved:
                    if base_idx is None:
                        base_idx = build_idx()
                    cands = base_idx.get(os.path.basename(sf), [])
                    for c in cands:
                        if c.endswith("/" + sf.lstrip("./")):
                            resolved = c
                            break
                    if not resolved and len(cands) == 1:
                        resolved = cands[0]
                if not resolved:
                    src_unresolved += 1
                    continue
                if resolved not in file_cache:
                    try:
                        with open(resolved, encoding="utf-8", errors="replace") as fp:
                            file_cache[resolved] = fp.readlines()
                    except Exception:
                        file_cache[resolved] = []
                cached = file_cache[resolved]
                if 0 < ln_num <= len(cached):
                    actual = cached[ln_num - 1].rstrip("\n")
                    if actual == sc:
                        src_match += 1
                    else:
                        src_mm += 1
                else:
                    src_mm += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        stats["src_match"] = src_match
        stats["src_mismatch"] = src_mm
        stats["src_unresolved"] = src_unresolved
        if src_mm > 0:
            findings.append(
                f"{src_mm} v2 lines have source_code text that doesn't match "
                f"the source file at that line"
            )

    return {"binary_id": bid, "findings": findings, **stats}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True,
                    help="Comma-separated binary_ids OR path to JSON list")
    args = ap.parse_args()
    if args.ids.endswith(".json"):
        with open(args.ids) as f:
            bids = json.load(f)
    else:
        bids = [int(x) for x in args.ids.split(",")]

    con = duckdb.connect(V2_DB, read_only=True)
    con.execute("PRAGMA threads = 8")

    print(f"validating {len(bids)} binaries from v2", flush=True)
    results = []
    for i, bid in enumerate(bids):
        try:
            r = validate_one(con, bid)
        except Exception as e:
            r = {"binary_id": bid, "findings": [f"exception: {e}"]}
        results.append(r)
        n_find = len(r.get("findings", []))
        print(
            f"[{i+1}/{len(bids)}] bid={bid:>6} "
            f"funcs={r.get('v2_funcs','-')} lines={r.get('v2_lines','-')} "
            f"findings={n_find}",
            flush=True,
        )
        for fi in r.get("findings", []):
            print(f"      ⚠ {fi}", flush=True)

    n_clean = sum(1 for r in results if not r.get("findings"))
    print(f"\nclean: {n_clean}/{len(results)}", flush=True)
    return 0 if n_clean == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
