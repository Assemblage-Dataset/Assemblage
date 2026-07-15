#!/usr/bin/env python3
"""Pick 100 random binaries from linux_licensed.duckdb and dump
per-binary {functions, rvas, lines} data to JSON files for validator agents.

Output:
  data_refill/.validation/manifest.json     — list of all 100 binary records
  data_refill/.validation/binary_<id>.json  — full extracted data per binary
  data_refill/.validation/batch_<i>.json    — list of binary_ids for agent i (0..9)
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import duckdb

DATA_DIR = Path("/home/cliu57/research/Assemblage/data_refill")
DUCKDB_PATH = DATA_DIR / "linux_licensed.duckdb"
OUT_DIR = DATA_DIR / ".validation"
BINARIES_ROOT = DATA_DIR / "binaries"
SOURCE_ROOT = DATA_DIR / "linuxsource"

N_BINARIES = 100
N_AGENTS = 10
SEED = 42


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)

    con.execute("SET threads = 16")

    random.seed(SEED)
    con.execute(f"SELECT setseed({SEED / 100})")

    # Pick non-empty binaries that have at least one function (otherwise
    # there's nothing for the validator to compare).
    rows = con.execute(
        """
        SELECT b.id, b.file_name, b.path, b.platform, b.build_mode,
               b.toolset_version, b.optimization, b.size, b.github_url,
               b.repo_commit, b.binary_format, b.license, b.hash,
               b.repo_last_update
        FROM binaries b
        WHERE b.size > 0
          AND EXISTS (SELECT 1 FROM functions f WHERE f.binary_id = b.id)
        USING SAMPLE 100 ROWS (RESERVOIR, 42)
        """
    ).fetchall()
    cols = [d[0] for d in con.description]
    sample = [dict(zip(cols, r)) for r in rows]
    print(f"sampled {len(sample)} binaries")

    manifest = []
    for rec in sample:
        bid = rec["id"]

        funcs = con.execute(
            """SELECT id, name, hash, binary_id, prototype, source_file,
                      length(top_comments)  AS top_comments_len,
                      length(source_codes)  AS source_codes_len,
                      top_comments, source_codes
               FROM functions WHERE binary_id = ?""",
            [bid],
        ).fetchall()
        fn_cols = [d[0] for d in con.description]
        funcs_dicts = [dict(zip(fn_cols, f)) for f in funcs]

        fn_ids = [f["id"] for f in funcs_dicts]
        rvas = []
        lines = []
        if fn_ids:
            placeholders = ",".join(["?"] * len(fn_ids))
            rvas_rows = con.execute(
                f'SELECT id, start, "end", function_id FROM rvas WHERE function_id IN ({placeholders})',
                fn_ids,
            ).fetchall()
            rvas_cols = [d[0] for d in con.description]
            rvas = [dict(zip(rvas_cols, r)) for r in rvas_rows]

            lines_rows = con.execute(
                f"""SELECT id, line_number, source_file, source_code,
                           function_id, rva, length
                    FROM lines WHERE function_id IN ({placeholders})""",
                fn_ids,
            ).fetchall()
            lines_cols = [d[0] for d in con.description]
            lines = [dict(zip(lines_cols, r)) for r in lines_rows]

        # Locate the actual binary file on disk. The path column is relative
        # to data_refill/binaries/.
        on_disk = BINARIES_ROOT / rec["path"]
        on_disk_exists = on_disk.exists()
        on_disk_size = on_disk.stat().st_size if on_disk_exists else None

        out = {
            "binary": rec,
            "binary_path_on_disk": str(on_disk),
            "binary_exists_on_disk": on_disk_exists,
            "binary_size_on_disk": on_disk_size,
            "n_functions": len(funcs_dicts),
            "n_rvas": len(rvas),
            "n_lines": len(lines),
            "functions": funcs_dicts,
            "rvas": rvas,
            "lines": lines,
        }

        path = OUT_DIR / f"binary_{bid}.json"
        with open(path, "w") as f:
            json.dump(out, f, default=str)
        manifest.append(
            {
                "id": bid,
                "file_name": rec["file_name"],
                "path": rec["path"],
                "binary_path_on_disk": str(on_disk),
                "binary_exists_on_disk": on_disk_exists,
                "binary_size_on_disk": on_disk_size,
                "compiler": rec["toolset_version"],
                "optimization": rec["optimization"],
                "build_mode": rec["build_mode"],
                "github_url": rec["github_url"],
                "n_functions": len(funcs_dicts),
                "n_rvas": len(rvas),
                "n_lines": len(lines),
                "data_file": f"binary_{bid}.json",
            }
        )

    with open(OUT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # Split into N_AGENTS batches, deterministic.
    manifest.sort(key=lambda x: x["id"])
    for i in range(N_AGENTS):
        batch = manifest[i::N_AGENTS]
        with open(OUT_DIR / f"batch_{i}.json", "w") as f:
            json.dump(batch, f, indent=2, default=str)

    on_disk = sum(1 for m in manifest if m["binary_exists_on_disk"])
    print(f"binaries present on disk: {on_disk}/{len(manifest)}")
    print(f"manifest at {OUT_DIR / 'manifest.json'}")
    print(f"per-binary data files at {OUT_DIR}/binary_<id>.json")
    print(f"agent batches at {OUT_DIR}/batch_<0..{N_AGENTS - 1}>.json")


if __name__ == "__main__":
    main()
