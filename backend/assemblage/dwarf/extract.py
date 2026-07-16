"""The one DWARF extractor, shared by the builder and the dataset pipeline.

Lifted verbatim from the builder's ``LinuxBuildStrategy._extract_dwarf_info`` and
its helpers (the pre-re-architecture DWARF-v5 range-list fix is preserved), then
extended with the three features the two forks needed between them:

- **byte-identical duplicate-function dedup** (an approved behavioural delta; the
  dataset side already did this, the builder did not);
- **optional ``source_root`` remapping** so a later consumer can read source
  lines from a local checkout even when the DWARF paths are absolute build
  paths;
- **an optional SIGALRM wall-clock timeout** (installed only on the main thread,
  where Python permits ``signal.alarm``).

``extract_dwarf_info`` returns exactly one ``Binary_info_list`` item — the shape
frozen by ``tests/fixtures/golden/hello-make.metadata.norm.json`` — or ``None``
when the binary carries no usable debug info. With ``source_root=None`` and
``timeout_secs=None`` (the builder's call) the output is byte-for-byte what the
old builder produced, save for the dedup of exact-duplicate function entries.
"""

import bisect
import logging
import os
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

logger = logging.getLogger(__name__)

_DEFAULT_SIZE_LIMIT = 150 * 1024 * 1024


class _DwarfTimeout(Exception):
    """Raised by the SIGALRM handler when extraction exceeds its budget."""


@contextmanager
def _alarm(timeout_secs: int | None) -> Iterator[None]:
    """Arm a SIGALRM wall-clock timeout, but only on the main thread."""
    armed = (
        timeout_secs is not None
        and timeout_secs > 0
        and threading.current_thread() is threading.main_thread()
    )
    if not armed:
        yield
        return

    def _handler(_signum: int, _frame: Any) -> None:
        raise _DwarfTimeout

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(int(timeout_secs))  # type: ignore[arg-type]
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def extract_dwarf_info(
    binfile: str,
    *,
    source_root: str | None = None,
    size_limit: int | None = None,
    timeout_secs: int | None = None,
) -> dict[str, Any] | None:
    """Extract one ``Binary_info_list`` item from ``binfile``.

    ``size_limit`` defaults to the ``DWARF_SIZE_LIMIT`` env var (150 MB): larger
    binaries are skipped because loading every CU's functions and source lines
    costs roughly 10-20x the file size in Python memory. ``source_root`` remaps
    source-line lookups onto a local checkout; ``timeout_secs`` bounds the whole
    extraction with SIGALRM (main-thread only).
    """
    if size_limit is None:
        size_limit = int(os.environ.get("DWARF_SIZE_LIMIT", _DEFAULT_SIZE_LIMIT))
    try:
        size = os.path.getsize(binfile)
    except OSError:
        return None
    if size > size_limit:
        # Never skip silently: a RelWithDebInfo binary losing its whole
        # Binary_info_list must be attributable from the logs (statically
        # linked Rust binaries trip this routinely; raise DWARF_SIZE_LIMIT
        # per-service when the memory budget allows).
        logger.warning(
            "skipping DWARF extraction for %s: %d bytes > DWARF_SIZE_LIMIT=%d",
            binfile,
            size,
            size_limit,
        )
        return None

    try:
        with _alarm(timeout_secs):
            return _extract(binfile, source_root)
    except _DwarfTimeout:
        logger.warning("DWARF extraction timed out for %s", binfile)
        return None


def _extract(binfile: str, source_root: str | None) -> dict[str, Any] | None:
    with open(binfile, "rb") as f:
        try:
            elf = ELFFile(f)
        except ELFError:
            return None

        if not elf.has_dwarf_info():
            return None

        dwarf_info = elf.get_dwarf_info()
        base_addr = _get_elf_base_address(elf)
        exec_ranges = _executable_ranges(elf)

        all_functions: list[dict[str, Any]] = []
        file_cache: dict[str, list[str]] = {}

        for cu in dwarf_info.iter_CUs():
            top_die = cu.get_top_DIE()
            comp_dir = ""
            comp_dir_attr = top_die.attributes.get("DW_AT_comp_dir")
            if comp_dir_attr:
                comp_dir = _as_text(comp_dir_attr.value)

            cu_base_addr = 0
            cu_low_pc = top_die.attributes.get("DW_AT_low_pc")
            if cu_low_pc:
                cu_base_addr = cu_low_pc.value

            line_program = dwarf_info.line_program_for_CU(cu)
            file_table = _build_dwarf_file_table(line_program, comp_dir)

            func_start_idx = len(all_functions)
            _collect_functions_from_die(
                top_die, all_functions, base_addr, file_table, dwarf_info, cu_base_addr, exec_ranges
            )

            if line_program:
                _assign_line_entries(
                    line_program,
                    all_functions,
                    func_start_idx,
                    base_addr,
                    file_table,
                    file_cache,
                    source_root,
                )

        return _build_item(binfile, all_functions)


def _executable_ranges(elf: Any) -> list[tuple[int, int]]:
    """Union of SHF_EXECINSTR section ranges.

    Some compilers emit DW_TAG_subprogram DIEs at low_pc=0 for template
    instantiations that never made it into the binary; those addresses fall
    outside any executable section and must be filtered out.
    """
    exec_ranges: list[tuple[int, int]] = []
    for sec in elf.iter_sections():
        if sec["sh_flags"] & 0x4:  # SHF_EXECINSTR
            start = sec["sh_addr"]
            if start == 0:
                continue
            exec_ranges.append((start, start + sec["sh_size"]))
    exec_ranges.sort()
    return exec_ranges


def _get_elf_base_address(elf: Any) -> int:
    """The lowest PT_LOAD virtual address — the base for RVA computation."""
    base: int | None = None
    for seg in elf.iter_segments():
        if seg["p_type"] == "PT_LOAD" and (base is None or seg["p_vaddr"] < base):
            base = seg["p_vaddr"]
    return base if base is not None else 0


def _as_text(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _build_dwarf_file_table(line_program: Any, comp_dir: str) -> dict[int, str]:
    """Map each DWARF file index to a full file path."""
    file_table: dict[int, str] = {}
    if line_program is None:
        return file_table

    file_entries = line_program.header.get("file_entry", [])
    include_dirs = line_program.header.get("include_directory", [])
    version = line_program.header.get("version", 4)

    for i, entry in enumerate(file_entries):
        name = _as_text(entry.name)
        dir_index = entry.dir_index
        # DWARF v5: include_directory is 0-indexed (dir[0] = comp_dir).
        # DWARF v4: include_directory is 1-indexed (dir_index 0 means comp_dir).
        if version >= 5:
            if 0 <= dir_index < len(include_dirs):
                dir_name = _as_text(include_dirs[dir_index])
                full_path = name if os.path.isabs(name) else os.path.join(dir_name, name)
            elif comp_dir:
                full_path = name if os.path.isabs(name) else os.path.join(comp_dir, name)
            else:
                full_path = name
            file_table[i] = full_path
        else:
            if 0 < dir_index <= len(include_dirs):
                dir_name = _as_text(include_dirs[dir_index - 1])
                full_path = name if os.path.isabs(name) else os.path.join(dir_name, name)
            elif comp_dir:
                full_path = name if os.path.isabs(name) else os.path.join(comp_dir, name)
            else:
                full_path = name
            file_table[i + 1] = full_path

    return file_table


def _resolve_die_name(die: Any, _depth: int = 0) -> str | None:
    """Function name: prefer DW_AT_linkage_name (disambiguates C++ overloads),
    fall back to DW_AT_name, then follow abstract_origin/specification.
    """
    if _depth > 5:
        return None
    for direct_tag in ("DW_AT_linkage_name", "DW_AT_MIPS_linkage_name", "DW_AT_name"):
        attr = die.attributes.get(direct_tag)
        if attr:
            return _as_text(attr.value)
    for ref_tag in ("DW_AT_abstract_origin", "DW_AT_specification"):
        ref_attr = die.attributes.get(ref_tag)
        if ref_attr:
            try:
                ref_die = die.get_DIE_from_attribute(ref_tag)
                if ref_die:
                    return _resolve_die_name(ref_die, _depth + 1)
            except Exception:
                pass
    return None


def _resolve_die_source(die: Any, file_table: dict[int, str], _depth: int = 0) -> str:
    """Source file for a DIE, following abstract_origin/specification."""
    if _depth > 5:
        return ""
    decl_file = die.attributes.get("DW_AT_decl_file")
    if decl_file:
        return file_table.get(decl_file.value, "")
    for ref_tag in ("DW_AT_abstract_origin", "DW_AT_specification"):
        ref_attr = die.attributes.get(ref_tag)
        if ref_attr:
            try:
                ref_die = die.get_DIE_from_attribute(ref_tag)
                if ref_die:
                    return _resolve_die_source(ref_die, file_table, _depth + 1)
            except Exception:
                pass
    return ""


def _resolve_address_ranges(
    die: Any, dwarf_info: Any, cu_base_addr: int
) -> list[tuple[int, int]] | None:
    """Absolute address ranges from DW_AT_ranges or low_pc/high_pc.

    Handles pyelftools' three entry shapes: BaseAddressEntry (sets the current
    base PC), absolute RangeEntry (begin/end already absolute), and relative
    RangeEntry (begin/end are offsets from the current base). Critical for
    DWARF-v5 binaries, which use BaseAddressEntry + offset_pair extensively.
    """
    ranges_attr = die.attributes.get("DW_AT_ranges")
    if ranges_attr is not None:
        try:
            range_lists = dwarf_info.range_lists()
            if range_lists is not None:
                rl = range_lists.get_range_list_at_offset(ranges_attr.value)
                result: list[tuple[int, int]] = []
                base = cu_base_addr
                for entry in rl:
                    if hasattr(entry, "base_address"):
                        base = entry.base_address
                        continue
                    if (
                        getattr(entry, "begin_offset", None) == 0
                        and getattr(entry, "end_offset", None) == 0
                    ):
                        break
                    if getattr(entry, "is_absolute", False):
                        begin = entry.begin_offset
                        end = entry.end_offset
                    else:
                        begin = entry.begin_offset + base
                        end = entry.end_offset + base
                    if begin < end:
                        result.append((begin, end))
                return result if result else None
        except Exception:
            pass
    low_pc_attr = die.attributes.get("DW_AT_low_pc")
    if not low_pc_attr:
        return None
    low_pc = low_pc_attr.value
    high_pc_attr = die.attributes.get("DW_AT_high_pc")
    if not high_pc_attr:
        return None
    if high_pc_attr.form.startswith("DW_FORM_addr"):
        high_pc = high_pc_attr.value
    else:
        high_pc = low_pc + high_pc_attr.value
    if low_pc >= high_pc:
        return None
    return [(low_pc, high_pc)]


def _collect_functions_from_die(
    die: Any,
    functions_list: list[dict[str, Any]],
    base_addr: int,
    file_table: dict[int, str],
    dwarf_info: Any,
    cu_base_addr: int,
    exec_ranges: list[tuple[int, int]],
) -> None:
    """Recursively collect DW_TAG_subprogram / DW_TAG_inlined_subroutine DIEs."""
    if die.tag in ("DW_TAG_subprogram", "DW_TAG_inlined_subroutine"):
        func_name = _resolve_die_name(die)
        if func_name:
            addr_ranges = _resolve_address_ranges(die, dwarf_info, cu_base_addr)
            # Drop ranges that don't overlap any executable section.
            if addr_ranges and exec_ranges:
                addr_ranges = [
                    (b, e)
                    for (b, e) in addr_ranges
                    if any(b < se and e > ss for ss, se in exec_ranges)
                ]
            if addr_ranges:
                source_file = _resolve_die_source(die, file_table)
                ranges_info = []
                for begin, end in addr_ranges:
                    rva_start = begin - base_addr
                    rva_end = end - base_addr
                    if rva_start < 0 or rva_end <= rva_start:
                        continue
                    ranges_info.append(
                        {
                            "rva_start": format(rva_start, "x").rjust(16, "0"),
                            "rva_end": format(rva_end, "x").rjust(16, "0"),
                            "start_int": rva_start,
                            "end_int": rva_end,
                        }
                    )
                if ranges_info:
                    functions_list.append(
                        {
                            "name": func_name,
                            "source_file": source_file,
                            "ranges": ranges_info,
                            "lines": [],
                        }
                    )

    for child in die.iter_children():
        _collect_functions_from_die(
            child, functions_list, base_addr, file_table, dwarf_info, cu_base_addr, exec_ranges
        )


def _read_source_line(
    source_file: str,
    source_root: str | None,
    line_num: int,
    cache: dict[str, list[str]],
) -> str:
    """Return the text of ``line_num`` in ``source_file`` (empty if unreadable).

    With ``source_root``, a checkout root is tried before the raw DWARF path so a
    consumer can read sources even when the DWARF paths are absolute build paths.
    With ``source_root=None`` this is the builder's exact behaviour: read the raw
    path only.
    """
    if not source_file:
        return ""
    candidates = []
    if source_root:
        relative = source_file.lstrip(os.sep)
        candidates.append(os.path.join(source_root, relative))
    candidates.append(source_file)

    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if candidate not in cache:
            try:
                with open(candidate, encoding="utf-8", errors="replace") as sf:
                    cache[candidate] = sf.readlines()
            except Exception:
                cache[candidate] = []
        lines = cache[candidate]
        if 0 < line_num <= len(lines):
            return lines[line_num - 1].rstrip("\n")
        return ""
    return ""


def _assign_line_entries(
    line_program: Any,
    all_functions: list[dict[str, Any]],
    func_start_idx: int,
    base_addr: int,
    file_table: dict[int, str],
    file_cache: dict[str, list[str]],
    source_root: str | None,
) -> None:
    """Assign this CU's line entries to the functions collected from it."""
    intervals: list[tuple[int, int, int]] = []
    for fi in range(func_start_idx, len(all_functions)):
        for r in all_functions[fi]["ranges"]:
            intervals.append((r["start_int"], r["end_int"], fi))
    intervals.sort()
    interval_starts = [iv[0] for iv in intervals]

    raw_entries: list[dict[str, Any]] = []
    for entry in line_program.get_entries():
        state = entry.state
        if state is None or state.end_sequence:
            continue
        if not state.line:  # skip compiler-synthetic entries
            continue

        rva = state.address - base_addr
        source_file = file_table.get(state.file, "")
        source_code = _read_source_line(source_file, source_root, state.line, file_cache)

        raw_entries.append(
            {
                "line_number": state.line,
                "rva": format(rva, "x").rjust(16, "0"),
                "rva_int": rva,
                "length": 0,
                "source_code": source_code,
                "source_file": source_file,
            }
        )

    raw_entries.sort(key=lambda x: x["rva_int"])
    for i in range(len(raw_entries) - 1):
        raw_entries[i]["length"] = raw_entries[i + 1]["rva_int"] - raw_entries[i]["rva_int"]

    # Assign each line entry to the innermost interval that contains it (walking
    # back handles an outer subprogram overlapping its inlined subroutine).
    for entry_dict in raw_entries:
        entry_rva = entry_dict["rva_int"]
        idx = bisect.bisect_right(interval_starts, entry_rva) - 1
        while idx >= 0:
            s, e, fi = intervals[idx]
            if s <= entry_rva < e:
                all_functions[fi]["lines"].append(entry_dict)
                break
            idx -= 1


def _intersect_ratio(ranges: list[dict[str, Any]]) -> str:
    if len(ranges) <= 1:
        return "0%"
    ranges_sorted = sorted(ranges, key=lambda x: x["start_int"])
    total_len = ranges_sorted[-1]["end_int"] - ranges_sorted[0]["start_int"]
    gap = sum(
        ranges_sorted[i + 1]["start_int"] - ranges_sorted[i]["end_int"]
        for i in range(len(ranges_sorted) - 1)
    )
    return f"{(gap / total_len * 100):.2f}%" if total_len > 0 else "0%"


def _build_item(binfile: str, all_functions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Assemble the Binary_info_list item, deduplicating identical functions."""
    functions: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []

    for func_data in all_functions:
        if not func_data["ranges"]:
            continue

        clean_lines = [
            {
                "line_number": ln["line_number"],
                "rva": ln["rva"],
                "length": ln["length"],
                "source_code": ln["source_code"],
                "source_file": ln["source_file"],
            }
            for ln in func_data["lines"]
        ]
        function_info = [
            {"rva_start": r["rva_start"], "rva_end": r["rva_end"]} for r in func_data["ranges"]
        ]
        function_entry = {
            "function_name": func_data["name"],
            "source_file": func_data["source_file"],
            "intersect_ratio": _intersect_ratio(func_data["ranges"]),
            "function_info": function_info,
            "lines": clean_lines,
        }
        # Byte-identical duplicate-function dedup (approved delta).
        if function_entry in seen:
            continue
        seen.append(function_entry)
        functions.append(function_entry)

    if not functions:
        return None
    return {"file": os.path.basename(binfile), "functions": functions}
