#!/usr/bin/env python3
"""Validate one Assemblage-extracted binary record against the actual ELF
file's DWARF info.

Usage:  validate_binary.py <binary_id>
        binary_<id>.json must be in the same directory.

Prints a JSON report to stdout describing any mismatches discovered.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

VAL_DIR = Path(__file__).resolve().parent
BIN_ROOT = Path("/home/cliu57/research/Assemblage/data_refill/binaries")
SRC_ROOT = Path("/home/cliu57/research/Assemblage/data_refill/linuxsource")


def _readelf_symbols(elf: Path) -> dict[str, list[dict[str, Any]]]:
    """Return mapping of symbol name → list of dicts with addr/size/type."""
    try:
        out = subprocess.check_output(
            ["readelf", "-Ws", str(elf)], stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="replace")
    except subprocess.CalledProcessError:
        return {}
    syms: dict[str, list[dict[str, Any]]] = {}
    # Format: Num: Value Size Type Bind Vis Ndx Name
    pat = re.compile(r"^\s*\d+:\s+([0-9a-f]+)\s+(\d+)\s+(\w+)\s+\w+\s+\w+\s+\S+\s+(.*)$")
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        addr_hex, size, ty, name = m.groups()
        if not name:
            continue
        if ty != "FUNC":
            continue
        syms.setdefault(name, []).append(
            {"addr": int(addr_hex, 16), "size": int(size), "type": ty}
        )
    return syms


def _dwarf_funcs(elf: Path) -> list[dict[str, Any]]:
    """Extract function-like DIEs from DWARF: subprograms + inlined."""
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return []
    funcs: list[dict[str, Any]] = []
    with open(elf, "rb") as f:
        ef = ELFFile(f)
        if not ef.has_dwarf_info():
            return []
        dwarf = ef.get_dwarf_info()
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_subprogram":
                    continue
                name = None
                if "DW_AT_name" in die.attributes:
                    raw = die.attributes["DW_AT_name"].value
                    name = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                low_pc = (
                    die.attributes["DW_AT_low_pc"].value
                    if "DW_AT_low_pc" in die.attributes
                    else None
                )
                high_pc_attr = die.attributes.get("DW_AT_high_pc")
                high_pc = None
                if high_pc_attr is not None and low_pc is not None:
                    if high_pc_attr.form.startswith("DW_FORM_data"):
                        high_pc = low_pc + high_pc_attr.value
                    else:
                        high_pc = high_pc_attr.value
                funcs.append({"name": name, "low_pc": low_pc, "high_pc": high_pc})
    return funcs


def _dwarf_line_count(elf: Path) -> int:
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return -1
    n = 0
    with open(elf, "rb") as f:
        ef = ELFFile(f)
        if not ef.has_dwarf_info():
            return 0
        dw = ef.get_dwarf_info()
        for cu in dw.iter_CUs():
            try:
                lp = dw.line_program_for_CU(cu)
                if lp is None:
                    continue
                for entry in lp.get_entries():
                    if entry.state and not entry.state.end_sequence:
                        n += 1
            except Exception:
                continue
    return n


def _text_section_bounds(elf: Path) -> tuple[int, int] | None:
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError:
        return None
    with open(elf, "rb") as f:
        ef = ELFFile(f)
        sec = ef.get_section_by_name(".text")
        if sec is None:
            return None
        return sec["sh_addr"], sec["sh_addr"] + sec["sh_size"]


def validate(bid: int) -> dict[str, Any]:
    data = json.loads((VAL_DIR / f"binary_{bid}.json").read_text())
    findings: list[str] = []
    stats: dict[str, Any] = {}

    elf_path = Path(data["binary_path_on_disk"])
    if not elf_path.exists():
        findings.append(f"binary missing on disk: {elf_path}")
        return {"binary_id": bid, "findings": findings, "stats": stats}

    funcs = data["functions"]
    rvas = data["rvas"]
    lines = data["lines"]
    fn_ids = {f["id"] for f in funcs}

    stats["n_functions"] = len(funcs)
    stats["n_rvas"] = len(rvas)
    stats["n_lines"] = len(lines)

    # 1. FK integrity: rvas.function_id and lines.function_id must reference funcs.
    bad_rva_fk = [r for r in rvas if r["function_id"] not in fn_ids]
    bad_line_fk = [ln for ln in lines if ln["function_id"] not in fn_ids]
    if bad_rva_fk:
        findings.append(f"{len(bad_rva_fk)} rvas reference unknown function_id (sample: {bad_rva_fk[:1]})")
    if bad_line_fk:
        findings.append(
            f"{len(bad_line_fk)} lines reference unknown function_id (sample fid={bad_line_fk[0]['function_id']})"
        )

    # 2. binary_id consistency
    bad_bid = [f for f in funcs if f["binary_id"] != bid]
    if bad_bid:
        findings.append(f"{len(bad_bid)} functions have binary_id != {bid}")

    # 3. RVA range sanity
    bad_range = [r for r in rvas if r["start"] is None or r["end"] is None or r["start"] >= r["end"]]
    if bad_range:
        findings.append(f"{len(bad_range)} rvas have invalid start/end (sample: {bad_range[:1]})")

    # 4. Cross-check vs ELF DWARF
    dwarf_funcs = _dwarf_funcs(elf_path)
    stats["dwarf_subprograms"] = len(dwarf_funcs)
    dwarf_named = {f["name"] for f in dwarf_funcs if f["name"]}

    sym_funcs = _readelf_symbols(elf_path)
    stats["elf_func_symbols"] = sum(len(v) for v in sym_funcs.values())

    db_names = {f["name"] for f in funcs if f["name"]}
    stats["db_function_names"] = len(db_names)
    stats["dwarf_named_funcs"] = len(dwarf_named)

    only_in_db = db_names - dwarf_named - set(sym_funcs)
    only_in_dwarf = dwarf_named - db_names
    stats["names_only_in_db"] = len(only_in_db)
    stats["names_only_in_dwarf"] = len(only_in_dwarf)
    if only_in_db:
        findings.append(
            f"{len(only_in_db)} db function names not found in ELF DWARF or symtab "
            f"(sample: {sorted(only_in_db)[:5]})"
        )

    # 5. Truncation check: any function name exactly 128 chars or 127 chars
    truncated = [f["name"] for f in funcs if f["name"] and len(f["name"]) >= 127]
    if truncated:
        findings.append(
            f"{len(truncated)} function names look truncated (>=127 chars) "
            f"(sample: {truncated[:1]})"
        )

    # 6. Source file plausibility — should NOT be absolute path inside /tmp/projects
    src_in_tmp = [ln for ln in lines if ln["source_file"] and ln["source_file"].startswith("/tmp/")]
    if src_in_tmp:
        findings.append(
            f"{len(src_in_tmp)} line records have source_file under /tmp/ "
            f"(sample: {src_in_tmp[0]['source_file']!r}) — paths point to build-time tmpdir"
        )

    # 7. RVA bounds vs .text
    text_bounds = _text_section_bounds(elf_path)
    if text_bounds:
        lo, hi = text_bounds
        out_of_text = [r for r in rvas if not (lo <= r["start"] < hi)]
        stats["text_section"] = {"lo": hex(lo), "hi": hex(hi)}
        if out_of_text:
            findings.append(
                f"{len(out_of_text)}/{len(rvas)} rvas fall outside .text [{hex(lo)}-{hex(hi)}] "
                f"(sample: start={out_of_text[0]['start']:#x})"
            )

    # 8. Source-code column non-empty when expected
    fn_with_src = [f for f in funcs if f["source_codes_len"] and f["source_codes_len"] > 0]
    fn_with_src_file = [f for f in funcs if f["source_file"]]
    stats["functions_with_source_codes"] = len(fn_with_src)
    stats["functions_with_source_file"] = len(fn_with_src_file)

    # 9. Lines should reference functions that have source_file populated
    fn_id_to_src = {f["id"]: f["source_file"] for f in funcs}
    line_no_src_fn = [
        ln for ln in lines if not fn_id_to_src.get(ln["function_id"])
    ]
    if line_no_src_fn and lines:
        findings.append(
            f"{len(line_no_src_fn)}/{len(lines)} lines reference a function with empty source_file"
        )

    # 10. Lines should have rva consistent with their function's rva range
    fn_id_to_rvas = {}
    for r in rvas:
        fn_id_to_rvas.setdefault(r["function_id"], []).append(r)
    line_rva_outside = 0
    for ln in lines:
        if not ln.get("rva"):
            continue
        try:
            line_rva_int = int(ln["rva"], 16) if isinstance(ln["rva"], str) and ln["rva"].startswith("0x") else int(ln["rva"])
        except (ValueError, TypeError):
            continue
        ranges = fn_id_to_rvas.get(ln["function_id"], [])
        if ranges and not any(r["start"] <= line_rva_int < r["end"] for r in ranges):
            line_rva_outside += 1
    if line_rva_outside:
        findings.append(
            f"{line_rva_outside}/{len(lines)} line records have rva outside their function's [start,end] range"
        )

    return {"binary_id": bid, "findings": findings, "stats": stats}


def main() -> int:
    bids = []
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            bids.append(int(arg))
    else:
        # Default: validate a single binary id from stdin
        bids = [int(sys.stdin.read().strip())]

    reports = []
    for bid in bids:
        try:
            reports.append(validate(bid))
        except Exception as e:
            reports.append({"binary_id": bid, "error": str(e)})
    print(json.dumps(reports, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
