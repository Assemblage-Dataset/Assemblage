"""
minio_pipeline.py

Daily dataset pipeline: fetches new licensed Linux binaries from MinIO + PostgreSQL,
re-extracts DWARF debug info, constructs metadata in the format expected by db_construct(),
then appends to the cumulative linux_licensed.sqlite and stores each day's raw binaries
under assemblage_dataset/{date}/binaries/.

Usage:
    python minio_pipeline.py --since 2026-03-08 --dataset-dir /path/to/assemblage_dataset \
        --db-url postgresql://... --s3-endpoint http://localhost:9000 \
        --s3-access-key ... --s3-secret-key ... [--bucket artifacts]
"""

import argparse
import bisect
import datetime
import hashlib
import json
import signal
import logging
import os
import shutil
import tempfile
from pathlib import Path

import boto3
import botocore
from botocore.client import Config
from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError
from sqlalchemy import create_engine, text

# dataset_utils lives in the same directory
from dataset_utils import db_construct, METAFILE
from dataset_orm import migrate_existing_db, init_clean_database
from db import Dataset_DB

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DWARF extraction (mirrors LinuxBuildStrategy._extract_dwarf_info)
# ---------------------------------------------------------------------------

def _get_elf_base_address(elf):
    base = None
    for seg in elf.iter_segments():
        if seg["p_type"] == "PT_LOAD":
            if base is None or seg["p_vaddr"] < base:
                base = seg["p_vaddr"]
    return base if base is not None else 0


def _build_dwarf_file_table(line_program, comp_dir):
    file_table = {}
    if line_program is None:
        return file_table
    file_entries = line_program.header.get("file_entry", [])
    include_dirs = line_program.header.get("include_directory", [])
    version = line_program.header.get("version", 4)
    for i, entry in enumerate(file_entries):
        name = entry.name
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        dir_index = entry.dir_index
        # DWARF v5: include_directory is 0-indexed (dir[0] = comp_dir)
        # DWARF v4: include_directory is 1-indexed (dir_index 0 means comp_dir)
        if version >= 5:
            if 0 <= dir_index < len(include_dirs):
                dir_name = include_dirs[dir_index]
                if isinstance(dir_name, bytes):
                    dir_name = dir_name.decode("utf-8", errors="replace")
                full_path = name if os.path.isabs(name) else os.path.join(dir_name, name)
            elif comp_dir:
                full_path = name if os.path.isabs(name) else os.path.join(comp_dir, name)
            else:
                full_path = name
            file_table[i] = full_path
        else:
            if dir_index > 0 and dir_index <= len(include_dirs):
                dir_name = include_dirs[dir_index - 1]
                if isinstance(dir_name, bytes):
                    dir_name = dir_name.decode("utf-8", errors="replace")
                full_path = name if os.path.isabs(name) else os.path.join(dir_name, name)
            elif comp_dir:
                full_path = name if os.path.isabs(name) else os.path.join(comp_dir, name)
            else:
                full_path = name
            file_table[i + 1] = full_path
    return file_table


def _resolve_die_name(die, _depth=0):
    """Get function name, preferring DW_AT_linkage_name (full mangled
    symbol — disambiguates C++ overloads), falling back to DW_AT_name,
    then following DW_AT_abstract_origin/DW_AT_specification.
    """
    if _depth > 5:
        return None
    for direct_tag in ("DW_AT_linkage_name", "DW_AT_MIPS_linkage_name", "DW_AT_name"):
        attr = die.attributes.get(direct_tag)
        if attr:
            val = attr.value
            return val.decode("utf-8", errors="replace") if isinstance(val, bytes) else val
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


def _resolve_address_ranges(die, dwarf_info, cu_base_addr):
    """Get absolute address ranges from DW_AT_ranges or low_pc/high_pc.

    Handles three pyelftools entry shapes correctly:
      * BaseAddressEntry — has `base_address`; sets the current base PC.
      * RangeEntry with `is_absolute=True` — begin_offset/end_offset are
        already absolute addresses (they aren't a base selector).
      * RangeEntry with `is_absolute=False` — begin/end are relative
        offsets to be added to the current base.
    Critical for DWARF v5 binaries which use BaseAddressEntry + offset_pair
    extensively; the previous logic conflated absolute-RangeEntry with the
    base-selector and produced fictional ranges of the form
    (begin_offset, begin_offset + offset_pair_end).
    """
    ranges_attr = die.attributes.get("DW_AT_ranges")
    if ranges_attr is not None:
        try:
            range_lists = dwarf_info.range_lists()
            if range_lists is not None:
                rl = range_lists.get_range_list_at_offset(ranges_attr.value)
                result = []
                base = cu_base_addr
                for entry in rl:
                    # BaseAddressEntry sets the current base PC.
                    if hasattr(entry, "base_address"):
                        base = entry.base_address
                        continue
                    # End-of-list sentinel for v4 .debug_ranges.
                    if (getattr(entry, "begin_offset", None) == 0 and
                            getattr(entry, "end_offset", None) == 0):
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


def _resolve_die_source(die, file_table, _depth=0):
    """Get source file from DIE, following abstract_origin/specification."""
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


# Per-binary DWARF extraction timeout in seconds (0 = skip DWARF entirely).
# Default raised from 1s -> 30s: the previous default caused most non-trivial
# binaries to time out and persist with empty DWARF, which is why ~171K of the
# legacy rows had n_lines=0 despite real .debug_line data in the ELF.
DWARF_TIMEOUT_SECS = int(os.environ.get("DWARF_TIMEOUT_SECS", "30"))


class _DWARFTimeout(BaseException):
    """BaseException so inner 'except Exception' blocks cannot suppress it."""


def _resolve_source_path(source_file, source_root_for_binary, comp_dir):
    """Try to map a DWARF source_file path (which may reference a
    build-time tmpdir like `/tmp/projects/<user>/<repo>/...`) to an actual
    file on disk. Returns the resolved path or None.
    """
    if not source_file:
        return None
    if os.path.isfile(source_file):
        return source_file
    if not source_root_for_binary:
        return None
    # Strip a `/tmp/projects/<user>/<repo>/` prefix if present.
    m = source_file.split("/tmp/projects/", 1)
    if len(m) == 2 and "/" in m[1]:
        # m[1] = "<user>/<repo>/<rest>"
        parts = m[1].split("/", 2)
        if len(parts) == 3:
            candidate = os.path.join(source_root_for_binary, parts[2])
            if os.path.isfile(candidate):
                return candidate
    # Strip a 32-hex MD5/UUID prefix that some build paths use.
    parts = source_file.split("/", 1)
    if len(parts) == 2 and len(parts[0]) == 32 and all(
        c in "0123456789abcdef" for c in parts[0].lower()
    ):
        candidate = os.path.join(source_root_for_binary, parts[1])
        if os.path.isfile(candidate):
            return candidate
    # Plain relative-path resolution against the source root. We try this
    # whether or not comp_dir is present — for many binaries DWARF emits
    # paths like `src/algorithms.cpp` directly, with no comp_dir.
    if not os.path.isabs(source_file):
        candidate = os.path.join(source_root_for_binary, source_file)
        if os.path.isfile(candidate):
            return candidate
    # If comp_dir is an absolute path matching the build tmpdir, try
    # rewriting `comp_dir/<rest>` -> `source_root/<rest>` on top.
    if comp_dir and source_file.startswith(comp_dir + "/"):
        rel = source_file[len(comp_dir) + 1:]
        candidate = os.path.join(source_root_for_binary, rel)
        if os.path.isfile(candidate):
            return candidate
    return None


def _read_source_line(resolved_path, line_num, _cache):
    """Cached line reader. Returns the source line text or '' on failure."""
    if not resolved_path or not line_num:
        return ""
    cached = _cache.get(resolved_path)
    if cached is None:
        try:
            with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                cached = f.readlines()
        except Exception:
            cached = []
        _cache[resolved_path] = cached
    if 0 < line_num <= len(cached):
        return cached[line_num - 1].rstrip("\n")
    return ""


def extract_dwarf_info(binfile, source_root=None):
    """
    Extract function/RVA/line info from an ELF binary using DWARF.
    Returns a dict in the Binary_info_list item format, or None on failure.
    Aborts and returns None if extraction exceeds DWARF_TIMEOUT_SECS.

    `source_root`, if given, is a directory containing the project source
    tree for this binary. It is used to resolve DWARF source_file paths
    (which often look like `/tmp/projects/<user>/<repo>/...`) to actual
    on-disk files so `lines.source_code` can be populated.
    """
    def _timeout_handler(signum, frame):
        raise _DWARFTimeout()

    if DWARF_TIMEOUT_SECS == 0:
        return None  # DWARF extraction disabled

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(DWARF_TIMEOUT_SECS)
    try:
        with open(binfile, "rb") as f:
            elf = ELFFile(f)
            if not elf.has_dwarf_info():
                return None
            dwarf = elf.get_dwarf_info()
            base_addr = _get_elf_base_address(elf)

            # Build the union of executable section ranges. Some compilers
            # emit DW_TAG_subprogram DIEs at low_pc=0 for STL template
            # instantiations etc. that never made it into the binary; those
            # addresses fall outside any SHF_EXECINSTR section and must be
            # filtered to avoid storing bogus zero-RVA functions.
            exec_ranges = []  # list of (start, end) in absolute addresses
            for sec in elf.iter_sections():
                if sec["sh_flags"] & 0x4:  # SHF_EXECINSTR
                    start = sec["sh_addr"]
                    if start == 0:
                        continue  # not allocated to a real address
                    exec_ranges.append((start, start + sec["sh_size"]))
            exec_ranges.sort()

            def _in_exec(begin, end):
                """True if [begin, end) overlaps any executable section."""
                for s, e in exec_ranges:
                    if begin < e and end > s:
                        return True
                return False

            all_functions = []  # list of dicts (avoids name-key collision)
            seen_functions = set()  # dedup by (name, source_file, rva_ranges)
            source_cache = {}  # filepath -> list of lines, for source_code

            for cu in dwarf.iter_CUs():
                comp_dir = ""
                cu_base_addr = 0
                try:
                    top_die = cu.get_top_DIE()
                    comp_dir_attr = top_die.attributes.get("DW_AT_comp_dir")
                    if comp_dir_attr:
                        comp_dir = comp_dir_attr.value
                        if isinstance(comp_dir, bytes):
                            comp_dir = comp_dir.decode("utf-8", errors="replace")
                    cu_low_pc = top_die.attributes.get("DW_AT_low_pc")
                    if cu_low_pc:
                        cu_base_addr = cu_low_pc.value
                except Exception as e:
                    logger.debug("Failed to read CU top DIE: %s", e)

                lp = None
                try:
                    lp = dwarf.line_program_for_CU(cu)
                except Exception as e:
                    logger.debug("Failed to get line program for CU: %s", e)
                file_table = _build_dwarf_file_table(lp, comp_dir)

                # Collect line entries per address, sorted for O(log N) range queries.
                line_addrs = []   # sorted list of addresses
                line_data  = {}   # addr -> {line, file_idx}
                if lp:
                    try:
                        for entry in lp.get_entries():
                            if entry.state is None:
                                continue
                            s = entry.state
                            if s.end_sequence:
                                continue
                            # Skip compiler-synthetic entries (no real source line).
                            if not s.line:
                                continue
                            line_data[s.address] = {
                                "line": s.line,
                                "file_idx": s.file,
                            }
                    except Exception as e:
                        logger.debug("Error iterating line program entries: %s", e)
                    line_addrs = sorted(line_data.keys())

                for die in cu.iter_DIEs():
                    if die.tag not in ("DW_TAG_subprogram",
                                       "DW_TAG_inlined_subroutine"):
                        continue

                    func_name = _resolve_die_name(die)
                    if not func_name:
                        continue

                    addr_ranges = _resolve_address_ranges(
                        die, dwarf, cu_base_addr)
                    if not addr_ranges:
                        continue
                    # Drop ranges that don't overlap any executable section.
                    # This filters out STL/template DIEs the compiler emits
                    # at low_pc=0 even though no code was generated.
                    if exec_ranges:
                        addr_ranges = [
                            (b, e) for (b, e) in addr_ranges if _in_exec(b, e)
                        ]
                        if not addr_ranges:
                            continue

                    source_file = _resolve_die_source(die, file_table)

                    # Collect lines across all ranges using bisect
                    lines = []
                    rva_ranges = []
                    for begin, end in addr_ranges:
                        rva_s = begin - base_addr
                        rva_e = end - base_addr
                        if rva_s < 0 or rva_e <= rva_s:
                            continue
                        rva_ranges.append({
                            "rva_start": format(rva_s, "x").rjust(16, "0"),
                            "rva_end": format(rva_e, "x").rjust(16, "0"),
                        })
                        lo = bisect.bisect_left(line_addrs, begin)
                        hi = bisect.bisect_left(line_addrs, end)
                        for addr in line_addrs[lo:hi]:
                            li = line_data[addr]
                            rva = addr - base_addr
                            sf = file_table.get(li["file_idx"], source_file)
                            src_text = ""
                            if source_root:
                                resolved = _resolve_source_path(
                                    sf, source_root, comp_dir)
                                src_text = _read_source_line(
                                    resolved, li["line"], source_cache)
                            lines.append({
                                "line_number": li["line"],
                                "rva": format(rva, "x").rjust(16, "0"),
                                "rva_int": rva,
                                "length": 0,
                                "source_code": src_text,
                                "source_file": sf,
                            })

                    # Compute line lengths from consecutive addresses
                    if lines:
                        lines.sort(key=lambda x: x["rva_int"])
                        for i in range(len(lines) - 1):
                            lines[i]["length"] = (
                                lines[i + 1]["rva_int"] - lines[i]["rva_int"])
                        for ln in lines:
                            del ln["rva_int"]

                    if rva_ranges:
                        # Dedup: skip if we already have this function
                        rva_key = tuple(
                            (r["rva_start"], r["rva_end"]) for r in rva_ranges)
                        dedup_key = (func_name, source_file, rva_key)
                        if dedup_key in seen_functions:
                            continue
                        seen_functions.add(dedup_key)
                        all_functions.append({
                            "function_name": func_name,
                            "source_file": source_file,
                            "function_info": rva_ranges,
                            "lines": lines,
                        })

        if not all_functions:
            return None

        functions_list = []
        for fdata in all_functions:
            functions_list.append({
                "function_name": fdata["function_name"],
                "source_file": fdata["source_file"],
                "intersect_ratio": "0.00%",
                "function_info": fdata["function_info"],
                "lines": fdata["lines"],
            })

        return {
            "file": os.path.basename(binfile),
            "functions": functions_list,
        }
    except _DWARFTimeout:
        logger.info("DWARF extraction timed out (>%ds) for %s", DWARF_TIMEOUT_SECS, binfile)
        return None
    except (ELFError, Exception) as e:
        logger.debug("DWARF extraction failed for %s: %s", binfile, e)
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ---------------------------------------------------------------------------
# PostgreSQL query
# ---------------------------------------------------------------------------

NEW_ASSEMBLY_QUERY = text("""
    SELECT
        b.id                        AS binary_id,
        b.file_name,
        r.url                       AS repo_url,
        r.name                      AS repo_name,
        r.description               AS repo_description,
        r.language                  AS repo_language,
        r.build_system,
        r.created_at                AS repo_created_at,
        r.size                      AS repo_size_kb,
        s.commit_hexsha,
        LOWER(opt.compiler_name)    AS compiler,
        opt.compiler_flag
    FROM binaries b
    JOIN b_status s   ON b.status_id    = s.id
    JOIN projects r   ON s.repo_id      = r.id
    JOIN buildopt opt ON s.build_opt_id = opt.id
    WHERE b.build_date > :since
      AND opt.platform = 'linux'
      AND (b.file_name LIKE '%%.s'
           OR b.file_name LIKE '%%.S'
           OR b.file_name LIKE '%%.asm')
      AND b.file_name NOT LIKE '%%CompilerId%%'
      AND b.file_name NOT LIKE '%%CMakeDetermineCompiler%%'
      AND s.commit_hexsha != ''
    ORDER BY b.build_date ASC
""")


NEW_BINARIES_QUERY = text("""
    SELECT
        b.id                        AS binary_id,
        b.file_name,
        b.build_date,
        r.url                       AS repo_url,
        s.commit_hexsha,
        r.updated_at,
        LOWER(opt.compiler_name)    AS compiler,
        opt.library                 AS arch,
        opt.platform,
        r.name                      AS repo_name,
        r.description               AS repo_description,
        r.language                  AS repo_language,
        r.build_system,
        r.created_at                AS repo_created_at,
        r.size                      AS repo_size_kb,
        s.build_time,
        opt.compiler_flag
    FROM binaries b
    JOIN b_status s   ON b.status_id    = s.id
    JOIN projects r   ON s.repo_id      = r.id
    JOIN buildopt opt ON s.build_opt_id = opt.id
    WHERE b.build_date > :since
      AND opt.platform = 'linux'
      AND b.file_name NOT LIKE '%%.bc'
      AND b.file_name NOT LIKE '%%.ii'
      AND b.file_name NOT LIKE '%%.o'
      AND b.file_name NOT LIKE '%%.a'
      AND b.file_name NOT LIKE '%%.json'
      AND b.file_name NOT LIKE '%%CMakeDetermineCompiler%%'
      AND b.file_name NOT LIKE '%%CompilerId%%'
      AND s.commit_hexsha != ''
    ORDER BY b.build_date ASC
""")


def query_new_binaries(db_url, since_dt):
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(NEW_BINARIES_QUERY, {"since": since_dt}).fetchall()
    return [dict(row._mapping) for row in rows]


def query_new_assembly(db_url, since_dt, limit=0):
    engine = create_engine(db_url)
    q = NEW_ASSEMBLY_QUERY
    if limit > 0:
        q = text(str(NEW_ASSEMBLY_QUERY.text) + f" LIMIT {int(limit)}")
    with engine.connect() as conn:
        rows = conn.execute(q, {"since": since_dt}).fetchall()
    return [dict(row._mapping) for row in rows]


# ---------------------------------------------------------------------------
# MinIO download
# ---------------------------------------------------------------------------

def make_s3_client(endpoint, access_key, secret_key, https=False):
    scheme = "https" if https else "http"
    endpoint_url = f"{scheme}://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def parse_github_owner_project(url):
    """Extract owner and project name from a GitHub URL (cloneable or API form)."""
    parts = url.rstrip("/").split("/")
    return parts[-2], parts[-1]


_FLAG_TO_OLD_ENUM = {"-O0": "NONE", "-O1": "LOW", "-O2": "MEDIUM", "-O3": "HIGH"}

def download_binary(s3, bucket, repo_url, commit_hexsha, compiler, opt_enum, filename, dest_path):
    owner, project = parse_github_owner_project(repo_url)
    basename = os.path.basename(filename)
    # Try new path format (-O0), old enum format (opt_NONE), and legacy name (opt_LOW)
    old_name = _FLAG_TO_OLD_ENUM.get(opt_enum, "")
    candidates = [
        f"{owner}/{project}/{commit_hexsha}/{compiler}/{opt_enum}/{basename}",
    ]
    if old_name:
        candidates.append(f"{owner}/{project}/{commit_hexsha}/{compiler}/opt_{old_name}/{basename}")
    candidates.append(f"{owner}/{project}/{commit_hexsha}/{compiler}/opt_{opt_enum}/{basename}")
    for s3_key in candidates:
        try:
            s3.download_file(bucket, s3_key, dest_path)
            return True
        except botocore.exceptions.ClientError:
            continue
    logger.warning("Failed to download s3://%s/%s (tried %d paths)", bucket, candidates[0], len(candidates))
    return False


def download_source_archive(s3, owner, project, commit_hexsha, dest_path):
    """
    Download the source archive for a repo+commit from the project-archive bucket.
    Key format: {owner}/{project}/{commit_hexsha}.tar.gz
    Returns True on success, False on failure.
    """
    s3_key = f"{owner}/{project}/{commit_hexsha}.tar.gz"
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        s3.download_file("project-archive", s3_key, dest_path)
        logger.info("Downloaded source archive s3://project-archive/%s -> %s", s3_key, dest_path)
        return True
    except botocore.exceptions.ClientError as e:
        logger.info("Source archive not available s3://project-archive/%s : %s", s3_key, e)
        return False


# ---------------------------------------------------------------------------
# Staging directory construction
# ---------------------------------------------------------------------------

def get_md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def build_staging_entry(row, binary_path, staging_dir, source_root=None):
    """
    Place the binary and a generated assemblage_meta.json into a staging
    sub-directory in the format db_construct() expects:
        staging/{identifier}/
            assemblage_meta.json
            {identifier}_{filename}

    `source_root`, if provided, is the path to the extracted source tree for
    this binary's repo (used so DWARF source_file paths can be resolved and
    `lines.source_code` populated).
    """
    repo_url = row["repo_url"]
    compiler = row["compiler"]
    compiler_flag = row.get("compiler_flag", "") or ""
    arch = row["arch"] or "x64"
    commit_hexsha = row["commit_hexsha"] or ""
    filename = os.path.basename(row["file_name"])

    updated_at = row["updated_at"]
    if hasattr(updated_at, "strftime"):
        pushed_at_str = updated_at.strftime("%m/%d/%Y, %H:%M:%S")
    else:
        pushed_at_str = str(updated_at)

    identifier = f"{get_md5(repo_url)}_{arch}_{compiler}_{compiler_flag}"
    ident_dir = os.path.join(staging_dir, identifier)
    os.makedirs(ident_dir, exist_ok=True)

    # Extract DWARF info from the downloaded binary (only meaningful for ELF files).
    dwarf_item = extract_dwarf_info(binary_path, source_root=source_root)

    meta_path = os.path.join(ident_dir, METAFILE)

    # Determine artifact_type based on file extension
    ext = os.path.splitext(filename)[1].lower()
    artifact_type_map = {'.s': 'assembly', '.S': 'assembly', '.bc': 'llvm_ir', '.ii': 'preprocessed', '.i': 'preprocessed'}
    artifact_type = artifact_type_map.get(ext, 'binary')

    # Read existing meta if present (we accumulate Binary_info_list across
    # multiple binaries that share the same identifier dir), or build a new
    # one. db_construct() reads `Binary_info_list` directly from this metafile
    # — the previous design wrote DWARF to a sidecar `.dwarf.json` that
    # db_construct silently ignored, so all daily-pipeline DWARF data was
    # being discarded.
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    else:
        meta = {}

    if not meta:
        meta = {
            "Platform": arch,
            "Compiler": compiler,
            "URL": repo_url,
            "Compiler_flag": compiler_flag,
            "Pushed_at": pushed_at_str,
            "commit_sha": commit_hexsha,
            "Repo_name": row.get("repo_name", "") or "",
            "Repo_description": row.get("repo_description", "") or "",
            "Repo_language": row.get("repo_language", "") or "",
            "Build_system": row.get("build_system", "") or "",
            "Repo_created_at": str(row.get("repo_created_at", "")) or "",
            "Repo_size_kb": row.get("repo_size_kb", 0) or 0,
            "Build_time": row.get("build_time", 0) or 0,
            "Artifact_type": artifact_type,
        }

    # Append this binary's DWARF entry into Binary_info_list.
    if dwarf_item is not None:
        existing = meta.setdefault("Binary_info_list", [])
        # de-dup by file name in case the same binary is staged twice
        seen_files = {entry.get("file") for entry in existing}
        if dwarf_item.get("file") not in seen_files:
            existing.append(dwarf_item)

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Binary name must match the identifier prefix pattern used by db_construct
    dest_bin = os.path.join(ident_dir, f"{identifier}_{filename}")
    shutil.copy2(binary_path, dest_bin)

    return ident_dir


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _download_asm_one(args):
    """Download a single assembly file. Thread worker."""
    row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir = args
    s3 = make_s3_client(s3_endpoint, s3_access_key, s3_secret_key, s3_https)

    filename = os.path.basename(row["file_name"])
    raw_path = os.path.join(download_dir, f"{row['binary_id']}_{filename}")

    if os.path.exists(raw_path):
        return ("cached", row, raw_path)

    ok = download_binary(
        s3, bucket,
        repo_url=row["repo_url"],
        commit_hexsha=row["commit_hexsha"],
        compiler=row["compiler"],
        opt_enum=row.get("compiler_flag", ""),
        filename=filename,
        dest_path=raw_path,
    )
    if not ok:
        return ("fail", row, None)

    return ("ok", row, raw_path)


def run_assembly_pipeline(since_date_str, dataset_dir, db_url,
                          s3_endpoint, s3_access_key, s3_secret_key,
                          bucket="artifacts", s3_https=False,
                          download_workers=32, limit=0):
    """Download assembly files from MinIO and record them in assembly.sqlite."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import datetime as _datetime

    since_dt = datetime.datetime.strptime(since_date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    )
    today_str = datetime.date.today().isoformat()

    dataset_dir = Path(dataset_dir)
    asm_dir = dataset_dir / today_str / "assembly"
    asm_dir.mkdir(parents=True, exist_ok=True)

    sqlite_path = str(dataset_dir / "assembly.sqlite")

    logger.info("[asm] Querying assembly files since %s ...", since_date_str)
    rows = query_new_assembly(db_url, since_dt, limit=limit)
    logger.info("[asm] Found %d assembly files", len(rows))

    if not rows:
        logger.info("[asm] Nothing to do.")
        return

    # Init or migrate the assembly SQLite DB
    if not os.path.exists(sqlite_path):
        init_clean_database(f"sqlite:///{sqlite_path}")
    migrate_existing_db(sqlite_path)

    db = Dataset_DB(f"sqlite:///{sqlite_path}")

    # Ensure repos exist first, build url->id map
    repo_map = {}  # github_url -> repo_id
    repo_ds = {}
    for row in rows:
        url = row["repo_url"]
        if url not in repo_ds:
            repo_ds[url] = {
                "github_url": url,
                "name": row.get("repo_name", "") or "",
                "description": row.get("repo_description", "") or "",
                "language": row.get("repo_language", "") or "",
                "build_system": row.get("build_system", "") or "",
                "license": "",
                "created_at": str(row.get("repo_created_at", "")) or "",
                "size_kb": row.get("repo_size_kb", 0) or 0,
                "first_seen": today_str,
            }
    db.bulk_add_repos(list(repo_ds.values()))

    # Build url -> repo_id lookup
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(sqlite_path)
    cursor = conn.cursor()
    for url in repo_ds:
        rid = cursor.execute("SELECT id FROM repos WHERE github_url = ?", (url,)).fetchone()
        if rid:
            repo_map[url] = rid[0]
    conn.close()

    # Download assembly files
    download_dir = str(dataset_dir / today_str / "raw_asm")
    os.makedirs(download_dir, exist_ok=True)

    work_args = [
        (row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir)
        for row in rows
    ]

    downloaded = 0
    failed = 0
    total = len(rows)
    downloaded_rows = []

    logger.info("[asm] Downloading %d files with %d threads...", total, download_workers)
    with ThreadPoolExecutor(max_workers=download_workers) as pool:
        futures = {pool.submit(_download_asm_one, a): a[0] for a in work_args}
        for future in as_completed(futures):
            try:
                status, row, raw_path = future.result()
            except Exception as e:
                failed += 1
                if failed <= 5:
                    logger.error("[asm] Worker exception: %s", e)
                continue
            if status in ("ok", "cached"):
                downloaded += 1
                downloaded_rows.append((row, raw_path))
            else:
                failed += 1

    logger.info("[asm] Downloads done: %d ok, %d failed", downloaded, failed)

    # Move to hash-based layout and record in DB
    asm_records = []
    for row, raw_path in downloaded_rows:
        repo_id = repo_map.get(row["repo_url"])
        if repo_id is None:
            continue
        filename = os.path.basename(raw_path)
        # hash-based subdirectory
        fhash = hashlib.md5(filename.encode()).hexdigest()
        sub = os.path.join(fhash[0:2], fhash[2:4])
        dest_dir = asm_dir / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        shutil.copy2(raw_path, str(dest_path))

        rel_path = str(dest_path.relative_to(dataset_dir))
        asm_records.append({"repo_id": repo_id, "path": rel_path})

        if len(asm_records) >= 1000:
            db.bulk_add_assembly_files(asm_records)
            asm_records = []

    db.bulk_add_assembly_files(asm_records)
    db.shutdown()

    logger.info("[asm] Done. %d assembly files recorded in %s", downloaded, sqlite_path)


def _download_one(args):
    """Download a single binary. Thread worker function (no DWARF — signal unsafe in threads)."""
    row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir = args
    s3 = make_s3_client(s3_endpoint, s3_access_key, s3_secret_key, s3_https)

    filename = os.path.basename(row["file_name"])
    raw_path = os.path.join(download_dir, f"{row['binary_id']}_{filename}")

    if os.path.exists(raw_path):
        return ("cached", row, raw_path)

    ok = download_binary(
        s3, bucket,
        repo_url=row["repo_url"],
        commit_hexsha=row["commit_hexsha"],
        compiler=row["compiler"],
        opt_enum=row.get("compiler_flag", ""),
        filename=filename,
        dest_path=raw_path,
    )
    if not ok:
        return ("fail", row, None)

    return ("ok", row, raw_path)


def run_pipeline(since_date_str, dataset_dir, db_url,
                 s3_endpoint, s3_access_key, s3_secret_key,
                 bucket="artifacts", s3_https=False,
                 download_workers=32):

    from concurrent.futures import ThreadPoolExecutor, as_completed

    since_dt = datetime.datetime.strptime(since_date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    )
    today_str = datetime.date.today().isoformat()

    dataset_dir = Path(dataset_dir)
    date_dir = dataset_dir / today_str
    binaries_dir = date_dir / "binaries"
    binaries_dir.mkdir(parents=True, exist_ok=True)

    sqlite_path = str(dataset_dir / "linux_licensed.sqlite")

    logger.info("Querying new binaries since %s ...", since_date_str)
    rows = query_new_binaries(db_url, since_dt)
    logger.info("Found %d new binaries", len(rows))

    if not rows:
        logger.info("Nothing to do.")
        return

    staging_dir = str(date_dir / "staging")
    os.makedirs(staging_dir, exist_ok=True)

    download_dir = str(date_dir / "raw")
    os.makedirs(download_dir, exist_ok=True)

    archives_dir = str(date_dir / "archives")
    os.makedirs(archives_dir, exist_ok=True)

    downloaded = 0
    failed = 0
    total = len(rows)

    logger.info("Downloading with %d threads...", download_workers)

    work_args = [
        (row, bucket, s3_endpoint, s3_access_key, s3_secret_key, s3_https, download_dir)
        for row in rows
    ]

    # Phase 1: Parallel downloads (no DWARF — signals not safe in threads)
    downloaded_rows = []  # (row, raw_path) pairs for staging
    with ThreadPoolExecutor(max_workers=download_workers) as pool:
        futures = {pool.submit(_download_one, a): a[0] for a in work_args}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 2000 == 0:
                logger.info("Downloaded %d/%d files (%.1f%%) ok=%d failed=%d",
                            done_count, total, 100.0 * done_count / total, downloaded, failed)
            try:
                status, row, raw_path = future.result()
            except Exception as e:
                failed += 1
                if failed <= 5:
                    logger.error("Worker exception: %s", e)
                continue

            if status in ("ok", "cached"):
                downloaded += 1
                downloaded_rows.append((row, raw_path))
            else:
                failed += 1

    logger.info("Downloads done: %d ok, %d failed. Now staging...", downloaded, failed)

    # Phase 2: Sequential staging with DWARF extraction (main thread, signal-safe)
    staged = 0
    downloaded_archives = set()
    s3_arc = make_s3_client(s3_endpoint, s3_access_key, s3_secret_key, s3_https)
    for idx, (row, raw_path) in enumerate(downloaded_rows, 1):
        if idx % 2000 == 0:
            logger.info("Staging %d/%d (%.1f%%)", idx, len(downloaded_rows), 100.0 * idx / len(downloaded_rows))

        try:
            build_staging_entry(row, raw_path, staging_dir)
            staged += 1
        except Exception as e:
            if staged == 0:
                logger.error("Staging error: %s", e)
            continue

        # Download source archive once per (repo_url, commit_hexsha)
        archive_key = (row["repo_url"], row["commit_hexsha"])
        if archive_key not in downloaded_archives:
            downloaded_archives.add(archive_key)
            owner, project = parse_github_owner_project(row["repo_url"])
            commit_hexsha = row["commit_hexsha"] or ""
            if commit_hexsha:
                archive_dest = os.path.join(
                    archives_dir, owner, project, f"{commit_hexsha}.tar.gz"
                )
                download_source_archive(s3_arc, owner, project, commit_hexsha, archive_dest)

    logger.info("Staged %d/%d binaries for db_construct() (failed_dl=%d)", staged, total, failed)

    if staged == 0:
        logger.info("No binaries staged; skipping db_construct.")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return

    logger.info("Running db_construct() -> %s", sqlite_path)
    db_construct(
        dbfile=sqlite_path,
        target_dir=staging_dir,
        include_lines=True,
        include_functions=True,
        include_rvas=True,
        include_pdbs=False,
    )

    # db_construct moves binaries out of staging into its own hash-based layout;
    # copy final processed tree into the date binaries dir
    for item in Path(staging_dir).rglob("*"):
        if item.is_file():
            rel = item.relative_to(staging_dir)
            dest = binaries_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(dest))

    shutil.rmtree(staging_dir, ignore_errors=True)
    logger.info("Done. Dataset updated at %s", sqlite_path)
    logger.info("Today's binaries stored at %s", binaries_dir)

    # Also harvest assembly files into assembly.sqlite
    run_assembly_pipeline(
        since_date_str=since_date_str,
        dataset_dir=str(dataset_dir),
        db_url=db_url,
        s3_endpoint=s3_endpoint,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        bucket=bucket,
        s3_https=s3_https,
        download_workers=download_workers,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily MinIO -> dataset pipeline")
    parser.add_argument("--since", required=True, help="Fetch binaries built after this date (YYYY-MM-DD)")
    parser.add_argument("--dataset-dir", default="assemblage_dataset", help="Root dataset directory")
    parser.add_argument("--db-url", required=True, help="PostgreSQL connection URL")
    parser.add_argument("--s3-endpoint", required=True, help="MinIO/S3 host:port")
    parser.add_argument("--s3-access-key", required=True)
    parser.add_argument("--s3-secret-key", required=True)
    parser.add_argument("--bucket", default="artifacts")
    parser.add_argument("--s3-https", action="store_true")
    args = parser.parse_args()

    run_pipeline(
        since_date_str=args.since,
        dataset_dir=args.dataset_dir,
        db_url=args.db_url,
        s3_endpoint=args.s3_endpoint,
        s3_access_key=args.s3_access_key,
        s3_secret_key=args.s3_secret_key,
        bucket=args.bucket,
        s3_https=args.s3_https,
    )
