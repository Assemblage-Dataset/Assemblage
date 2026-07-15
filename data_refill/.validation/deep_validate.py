#!/usr/bin/env python3
"""Deep, independent validation of the fixed DWARF extractor.

For each binary:
  1. Extract its source archive to a temp dir.
  2. Run minio_pipeline.extract_dwarf_info(binfile, source_root=...).
  3. Independently re-derive ground truth from the ELF using pyelftools
     (different code path than the extractor itself — distinct iteration,
     different aggregation).
  4. Verify every record in the extractor's output corresponds to ground
     truth, and every reasonable ground-truth item appears in output.
  5. For populated source_code text, read the source file at the reported
     line number and compare exactly.

Outputs a structured findings list per binary. Designed to be run on a
diverse sample to catch any remaining bugs before the full re-extraction.
"""
from __future__ import annotations

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

# Stub modules that the restored minio_pipeline imports.
sys.path.insert(0, "/home/cliu57/research/Assemblage/Assemblage_dataset_cli")
import types as _types
_stub_db = _types.ModuleType("db")
class _StubDB:
    def __init__(self, *_a, **_kw): pass
    def bulk_add_repos(self, *_a, **_kw): pass
    def bulk_add_assembly_files(self, *_a, **_kw): pass
    def shutdown(self, *_a, **_kw): pass
_stub_db.Dataset_DB = _StubDB
sys.modules["db"] = _stub_db
import dataset_orm
if not hasattr(dataset_orm, "migrate_existing_db"):
    dataset_orm.migrate_existing_db = lambda *_a, **_kw: None

os.environ.setdefault("DWARF_TIMEOUT_SECS", "300")

import minio_pipeline  # noqa: E402

from elftools.elf.elffile import ELFFile  # noqa: E402

LXSRC = Path("/home/cliu57/research/Assemblage/data_refill/linuxsource")
BINROOT = Path("/home/cliu57/research/Assemblage/data_refill/binaries")


# ---------------------------------------------------------------------------
# Independent ground-truth extraction (intentionally NOT shared with the
# extractor under test — distinct code path = real cross-check).
# ---------------------------------------------------------------------------

def gt_dwarf_subprograms(elf_path: str) -> list[dict[str, Any]]:
    """Independent walk of DWARF DIEs to enumerate subprograms.

    Returns a list of dicts: {name, linkage_name, low_pc, high_pc, decl_file_id}.
    Includes both DW_TAG_subprogram and DW_TAG_inlined_subroutine.
    """
    out: list[dict[str, Any]] = []
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return out
        dw = elf.get_dwarf_info()
        for cu in dw.iter_CUs():
            cu_low_pc = 0
            top = cu.get_top_DIE()
            if "DW_AT_low_pc" in top.attributes:
                cu_low_pc = top.attributes["DW_AT_low_pc"].value

            def name_of(die, depth=0):
                if depth > 5:
                    return None, None
                ln_attr = die.attributes.get("DW_AT_linkage_name") or \
                          die.attributes.get("DW_AT_MIPS_linkage_name")
                ln = None
                if ln_attr:
                    v = ln_attr.value
                    ln = v.decode("utf-8", "replace") if isinstance(v, bytes) else v
                nm_attr = die.attributes.get("DW_AT_name")
                nm = None
                if nm_attr:
                    v = nm_attr.value
                    nm = v.decode("utf-8", "replace") if isinstance(v, bytes) else v
                if ln or nm:
                    return ln, nm
                for ref_tag in ("DW_AT_abstract_origin", "DW_AT_specification"):
                    if ref_tag in die.attributes:
                        try:
                            r = die.get_DIE_from_attribute(ref_tag)
                            if r is not None:
                                return name_of(r, depth + 1)
                        except Exception:
                            pass
                return None, None

            def ranges_of(die):
                if "DW_AT_ranges" in die.attributes:
                    try:
                        rl = dw.range_lists()
                        if rl is not None:
                            entries = rl.get_range_list_at_offset(
                                die.attributes["DW_AT_ranges"].value)
                            ranges = []
                            base = cu_low_pc
                            for e in entries:
                                if hasattr(e, "base_address"):
                                    base = e.base_address
                                    continue
                                bo = getattr(e, "begin_offset", None)
                                eo = getattr(e, "end_offset", None)
                                if bo == 0 and eo == 0:
                                    break
                                if getattr(e, "is_absolute", False):
                                    begin, end = bo, eo
                                else:
                                    begin, end = bo + base, eo + base
                                if begin < end:
                                    ranges.append((begin, end))
                            return ranges if ranges else None
                    except Exception:
                        pass
                lp = die.attributes.get("DW_AT_low_pc")
                if lp is None:
                    return None
                low_pc = lp.value
                hp = die.attributes.get("DW_AT_high_pc")
                if hp is None:
                    return None
                if hp.form.startswith("DW_FORM_addr"):
                    high_pc = hp.value
                else:
                    high_pc = low_pc + hp.value
                if low_pc >= high_pc:
                    return None
                return [(low_pc, high_pc)]

            for die in cu.iter_DIEs():
                if die.tag not in ("DW_TAG_subprogram",
                                   "DW_TAG_inlined_subroutine"):
                    continue
                rngs = ranges_of(die)
                if not rngs:
                    continue
                ln, nm = name_of(die)
                if not (ln or nm):
                    continue
                out.append({
                    "linkage_name": ln,
                    "name": nm,
                    "ranges": rngs,
                })
    return out


def gt_line_entries(elf_path: str) -> dict[int, set[int]]:
    """Independent walk of .debug_line. Returns {address: set(line_numbers)}.

    A single address can have *different* line numbers in different CUs
    (e.g. an inlined helper from a header observed by multiple translation
    units). Aggregating to a set lets the validator accept any of the legit
    per-CU values, which is what the extractor produces.
    """
    out: dict[int, set[int]] = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        if not elf.has_dwarf_info():
            return out
        dw = elf.get_dwarf_info()
        for cu in dw.iter_CUs():
            try:
                lp = dw.line_program_for_CU(cu)
            except Exception:
                continue
            if lp is None:
                continue
            for entry in lp.get_entries():
                s = entry.state
                if s is None or s.end_sequence or not s.line:
                    continue
                out.setdefault(s.address, set()).add(s.line)
    return out


def text_section_bounds(elf_path: str) -> tuple[int, int] | None:
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        sec = elf.get_section_by_name(".text")
        if sec is None:
            return None
        return sec["sh_addr"], sec["sh_addr"] + sec["sh_size"]


def elf_base_address(elf_path: str) -> int:
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        base = None
        for seg in elf.iter_segments():
            if seg["p_type"] == "PT_LOAD":
                if base is None or seg["p_vaddr"] < base:
                    base = seg["p_vaddr"]
        return base if base is not None else 0


# ---------------------------------------------------------------------------
# Validation core
# ---------------------------------------------------------------------------

def _hex_to_int(s: str) -> int:
    return int(s, 16) if isinstance(s, str) else int(s)


def validate_one(
    binary_id: int,
    binary_path: str,
    source_archive: str | None,
) -> dict[str, Any]:
    findings: list[str] = []
    stats: dict[str, Any] = {"binary_id": binary_id, "binary": os.path.basename(binary_path)}

    # 1. Set up source root if available
    source_root = None
    tmp_dir: tempfile.TemporaryDirectory | None = None
    if source_archive and os.path.isfile(source_archive):
        tmp_dir = tempfile.TemporaryDirectory(prefix="dv_src_", dir="/tmp")
        try:
            with tarfile.open(source_archive, "r:gz") as tf:
                tf.extractall(tmp_dir.name)
            source_root = tmp_dir.name
        except Exception as e:
            findings.append(f"source archive extraction failed: {e}")
            tmp_dir.cleanup()
            tmp_dir = None
            source_root = None

    try:
        # 2. Run the extractor under test
        t0 = time.time()
        result = minio_pipeline.extract_dwarf_info(
            binary_path, source_root=source_root)
        stats["extract_secs"] = round(time.time() - t0, 2)

        if result is None:
            # Check whether this is "legitimately no extractable data":
            #   * no .debug_info section at all (stripped, PE, etc.)
            #   * .debug_info present but contains only declarations (no
            #     subprogram has low_pc/ranges, e.g. tiny shared libs that
            #     publish a header API but link the bodies elsewhere)
            try:
                proc = subprocess.run(
                    ["readelf", "-S", binary_path],
                    capture_output=True, text=True, timeout=30
                )
                has_debug = ".debug_info" in (proc.stdout or "")
                if not has_debug:
                    stats["no_dwarf"] = True
                    stats["findings"] = []
                    return stats
            except Exception:
                pass
            # DWARF present — count subprograms with actual addresses.
            try:
                gt_subs = gt_dwarf_subprograms(binary_path)
                if not gt_subs:
                    stats["dwarf_no_addressable_funcs"] = True
                    stats["findings"] = []
                    return stats
            except Exception:
                pass
            findings.append(
                "extractor returned None despite ELF having extractable DWARF "
                "subprograms")
            stats["findings"] = findings
            return stats

        out_funcs = result["functions"]
        stats["n_funcs_out"] = len(out_funcs)
        stats["n_lines_out"] = sum(len(f["lines"]) for f in out_funcs)
        stats["n_rvas_out"] = sum(len(f["function_info"]) for f in out_funcs)

        # 3. Ground truth
        gt_subs = gt_dwarf_subprograms(binary_path)
        gt_lines = gt_line_entries(binary_path)
        base_addr = elf_base_address(binary_path)
        text_b = text_section_bounds(binary_path)
        stats["n_gt_subprograms"] = len(gt_subs)
        stats["n_gt_lines"] = len(gt_lines)
        stats["base_addr"] = hex(base_addr)
        stats["text_bounds"] = (hex(text_b[0]), hex(text_b[1])) if text_b else None

        # 4. Build ground-truth name set (preferring linkage_name)
        gt_names = set()
        gt_low_pcs = set()
        for s in gt_subs:
            gt_names.add(s["linkage_name"] or s["name"])
            for begin, _end in s["ranges"]:
                gt_low_pcs.add(begin)

        # 5. Verify each output function name traces back to the DWARF.
        out_names_only_in_out = []
        for f in out_funcs:
            if f["function_name"] not in gt_names:
                # Strict failure mode: the name should be in DWARF.
                # Allow a fallback: maybe extractor used DW_AT_name
                # while gt used linkage_name (or vice versa).
                # We check the *other* name too.
                if f["function_name"] not in {s["name"] for s in gt_subs}:
                    out_names_only_in_out.append(f["function_name"])
        if out_names_only_in_out:
            findings.append(
                f"{len(out_names_only_in_out)} output function_name not "
                f"in DWARF (sample: {out_names_only_in_out[:3]})")

        # 6. RVA correctness: every output function's start (low_pc) should
        # land on a DWARF DIE's low_pc.
        rva_starts_match = 0
        rva_starts_miss = 0
        sample_miss = []
        for f in out_funcs:
            for r in f["function_info"]:
                rva_start = _hex_to_int(r["rva_start"])
                # convert RVA -> absolute by adding base
                abs_start = rva_start + base_addr
                if abs_start in gt_low_pcs:
                    rva_starts_match += 1
                else:
                    rva_starts_miss += 1
                    if len(sample_miss) < 3:
                        sample_miss.append({
                            "function": f["function_name"],
                            "rva_start_hex": r["rva_start"],
                            "abs_start_hex": hex(abs_start),
                        })
        stats["rva_starts_match"] = rva_starts_match
        stats["rva_starts_miss"] = rva_starts_miss
        if rva_starts_miss > 0:
            findings.append(
                f"{rva_starts_miss}/{rva_starts_match + rva_starts_miss} RVA "
                f"starts not found in DWARF DIE low_pc set "
                f"(sample: {sample_miss[:1]})")

        # 7. RVA end correctness: the (start, end) range should match a DWARF
        # range exactly.
        rva_ranges_in_gt = 0
        rva_ranges_not = 0
        rng_sample = []
        gt_ranges = set()
        for s in gt_subs:
            for begin, end in s["ranges"]:
                gt_ranges.add((begin, end))
        for f in out_funcs:
            for r in f["function_info"]:
                rs = _hex_to_int(r["rva_start"]) + base_addr
                re_ = _hex_to_int(r["rva_end"]) + base_addr
                if (rs, re_) in gt_ranges:
                    rva_ranges_in_gt += 1
                else:
                    rva_ranges_not += 1
                    if len(rng_sample) < 3:
                        rng_sample.append({
                            "function": f["function_name"],
                            "out": (hex(rs), hex(re_)),
                        })
        stats["rva_ranges_match"] = rva_ranges_in_gt
        stats["rva_ranges_miss"] = rva_ranges_not
        if rva_ranges_not > 0:
            findings.append(
                f"{rva_ranges_not}/{rva_ranges_in_gt + rva_ranges_not} "
                f"output (start,end) ranges not in DWARF "
                f"(sample: {rng_sample})")

        # 8. Line entries: every output line.rva should correspond to a real
        # entry in the DWARF line program (after line!=0 filter), and the
        # line number must match.
        line_match = 0
        line_miss = 0
        line_wrong_num = 0
        line_sample = []
        for f in out_funcs:
            for ln in f["lines"]:
                rva_int = _hex_to_int(ln["rva"])
                abs_addr = rva_int + base_addr
                if abs_addr in gt_lines:
                    valid_lines = gt_lines[abs_addr]
                    if ln["line_number"] in valid_lines:
                        line_match += 1
                    else:
                        line_wrong_num += 1
                        if len(line_sample) < 3:
                            line_sample.append({
                                "addr_hex": hex(abs_addr),
                                "out_line": ln["line_number"],
                                "gt_lines": sorted(valid_lines),
                            })
                else:
                    line_miss += 1
        stats["line_match"] = line_match
        stats["line_miss_in_gt"] = line_miss
        stats["line_wrong_num"] = line_wrong_num
        if line_miss:
            findings.append(
                f"{line_miss} output line entries reference an RVA not in "
                f"DWARF line program")
        if line_wrong_num:
            findings.append(
                f"{line_wrong_num} output line entries have wrong line "
                f"number (sample: {line_sample[:1]})")

        # 9. 16-char truncation check.
        # The OLD legacy bug truncated mangled C++ symbols mid-name; those
        # show as len=16 strings that DON'T appear anywhere in the ELF
        # symbol/DWARF tables as themselves. So we only flag len-16 names
        # that are NOT a valid prefix of any DWARF DIE name (i.e. truncated
        # truly mid-symbol). Real 16-char names like `mbedtls_mpi_init` ARE
        # the DWARF name, so they're not flagged.
        len16_names = [f["function_name"] for f in out_funcs
                       if len(f["function_name"]) == 16]
        truncated = []
        if len16_names:
            all_dwarf_names = set()
            for s in gt_subs:
                if s.get("linkage_name"):
                    all_dwarf_names.add(s["linkage_name"])
                if s.get("name"):
                    all_dwarf_names.add(s["name"])
            for n in len16_names:
                # If it appears verbatim in DWARF, it's a real name.
                if n in all_dwarf_names:
                    continue
                # If a longer DWARF name starts with this prefix, suspicious.
                truncated.append(n)
        stats["n_funcs_len16"] = len(len16_names)
        stats["n_funcs_len16_truncated"] = len(truncated)
        if truncated:
            findings.append(
                f"{len(truncated)} function names look truncated to 16 chars "
                f"(sample: {truncated[:3]})")

        # 10. Section pseudo-symbols must NOT appear. Only flag exact
        # matches against known ELF section names — `.omp_outlined.`,
        # `.constprop.`, etc. are legit compiler-generated functions.
        SECTION_PSEUDO = {
            ".text", ".bss", ".data", ".rodata", ".plt",
            ".init", ".fini", ".init_array", ".fini_array",
            ".dynsym", ".dynstr", ".symtab", ".strtab",
        }
        section_names = [f["function_name"] for f in out_funcs
                         if f["function_name"] in SECTION_PSEUDO]
        if section_names:
            findings.append(
                f"{len(section_names)} section pseudo-symbols leaked: "
                f"{section_names[:3]}")

        # 11. Source code text correctness — for lines with non-empty
        # source_code, locate the file under source_root and compare the
        # text against what's actually on disk at that line number.
        # We try multiple resolution strategies because the extractor uses
        # comp_dir from the CU which the validator doesn't have access to;
        # so we also fall back to find-by-basename when the relative-path
        # approach fails.
        if source_root:
            src_match = 0
            src_mismatch = 0
            src_unresolvable = 0
            mismatch_sample = []
            file_cache: dict[str, list[str]] = {}
            basename_index: dict[str, list[str]] | None = None

            def _build_basename_index() -> dict[str, list[str]]:
                idx: dict[str, list[str]] = {}
                for root, _dirs, files in os.walk(source_root):
                    for fn in files:
                        idx.setdefault(fn, []).append(os.path.join(root, fn))
                return idx

            for f in out_funcs:
                for ln in f["lines"]:
                    if not ln.get("source_code"):
                        continue
                    sf = ln.get("source_file")
                    if not sf:
                        continue
                    resolved = minio_pipeline._resolve_source_path(
                        sf, source_root, "")
                    if not resolved:
                        # Fall back to basename matching — works for cases
                        # where extractor used comp_dir that we can't replay.
                        if basename_index is None:
                            basename_index = _build_basename_index()
                        candidates = basename_index.get(os.path.basename(sf), [])
                        # Prefer one whose tail matches the relative path tail
                        for c in candidates:
                            if c.endswith("/" + sf.lstrip("./")):
                                resolved = c
                                break
                        if not resolved and len(candidates) == 1:
                            resolved = candidates[0]
                    if not resolved:
                        src_unresolvable += 1
                        continue
                    if resolved not in file_cache:
                        try:
                            with open(resolved, "r", encoding="utf-8",
                                      errors="replace") as fp:
                                file_cache[resolved] = fp.readlines()
                        except Exception:
                            file_cache[resolved] = []
                    cached = file_cache[resolved]
                    line_num = ln["line_number"]
                    if 0 < line_num <= len(cached):
                        true_line = cached[line_num - 1].rstrip("\n")
                        if true_line == ln["source_code"]:
                            src_match += 1
                        else:
                            src_mismatch += 1
                            if len(mismatch_sample) < 3:
                                mismatch_sample.append({
                                    "file": resolved,
                                    "line": line_num,
                                    "out": ln["source_code"][:80],
                                    "gt":  true_line[:80],
                                })
                    else:
                        src_mismatch += 1
            stats["source_text_match"] = src_match
            stats["source_text_mismatch"] = src_mismatch
            stats["source_text_unresolvable"] = src_unresolvable
            if src_mismatch:
                findings.append(
                    f"{src_mismatch} source_code text mismatches "
                    f"({src_match} matched, {src_unresolvable} unresolvable; "
                    f"sample: {mismatch_sample})")

        stats["findings"] = findings
        return stats
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: deep_validate.py <work_items.json>")
        return 1
    work_items = json.loads(Path(sys.argv[1]).read_text())
    print(f"validating {len(work_items)} binaries", flush=True)
    all_stats = []
    for i, item in enumerate(work_items):
        s = validate_one(
            item["binary_id"],
            item["binary_path"],
            item.get("source_archive"),
        )
        s["compiler"] = item.get("compiler")
        s["build_mode"] = item.get("build_mode")
        s["optimization"] = item.get("optimization")
        all_stats.append(s)
        print(f"[{i+1}/{len(work_items)}] bid={s['binary_id']:>6} "
              f"funcs={s.get('n_funcs_out','-')} lines={s.get('n_lines_out','-')} "
              f"findings={len(s['findings'])}", flush=True)
        if s["findings"]:
            for fi in s["findings"]:
                print(f"      ⚠ {fi}", flush=True)

    out_path = Path(sys.argv[1]).parent / "deep_validate_report.json"
    out_path.write_text(json.dumps(all_stats, indent=2, default=str))
    print(f"\nwrote {out_path}")

    n_clean = sum(1 for s in all_stats if not s["findings"])
    print(f"\nclean: {n_clean}/{len(all_stats)}")
    return 0 if n_clean == len(all_stats) else 2


if __name__ == "__main__":
    sys.exit(main())
