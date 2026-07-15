#!/usr/bin/env python3
"""Migrate linux_licensed.sqlite to linux_licensed.duckdb.

Uses DuckDB's sqlite_scanner extension to ATTACH the SQLite database
read-only and stream-copy each table via CREATE TABLE AS SELECT.
Resumable: tables already migrated with matching row counts are skipped.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

DATA_DIR = Path("/home/cliu57/research/Assemblage/data_refill")
SQLITE_PATH = DATA_DIR / "linux_licensed.sqlite"
DUCKDB_PATH = DATA_DIR / "linux_licensed.duckdb"
TMP_DIR = DATA_DIR / ".duckdb_tmp"

# Pre-counted via native sqlite3 on 2026-05-05 (before migration).
# These act as the source of truth for skip-already-migrated logic; the
# script does NOT recount via sqlite_scanner because COUNT(*) on the giant
# tables takes many minutes and the actual CTAS copy reads the same rows.
EXPECTED_ROWS = {
    "binaries": 249121,
    "pdbs": 0,
    "rvas": 367953333,
    "functions": 364830193,
    "lines": 590864255,
}
TABLES = ["binaries", "pdbs", "rvas", "functions", "lines"]
INDEXES = [
    ("idx_functions_binary_id", "functions", "binary_id"),
    ("idx_rvas_function_id", "rvas", "function_id"),
    ("idx_lines_function_id", "lines", "function_id"),
]


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fmt_secs(secs: float) -> str:
    if secs < 60:
        return f"{secs:.1f}s"
    if secs < 3600:
        return f"{secs / 60:.1f}m"
    return f"{secs / 3600:.2f}h"


def main() -> int:
    if not SQLITE_PATH.exists():
        log(f"ERROR: source sqlite not found: {SQLITE_PATH}")
        return 1

    TMP_DIR.mkdir(exist_ok=True)

    log(f"opening duckdb at {DUCKDB_PATH}")
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("PRAGMA memory_limit = '64GB'")
    con.execute("PRAGMA threads = 32")
    con.execute(f"PRAGMA temp_directory = '{TMP_DIR}'")
    con.execute("PRAGMA preserve_insertion_order = false")

    con.execute("INSTALL sqlite")
    con.execute("LOAD sqlite")
    con.execute(f"ATTACH '{SQLITE_PATH}' AS src (TYPE sqlite, READ_ONLY)")

    dst_db = con.execute("SELECT current_database()").fetchone()[0]
    for tbl in TABLES:
        expected = EXPECTED_ROWS[tbl]

        existing = con.execute(
            "SELECT count(*) FROM duckdb_tables() "
            "WHERE database_name=? AND schema_name='main' AND table_name=?",
            [dst_db, tbl],
        ).fetchone()[0]

        if existing:
            dst_n = con.execute(f'SELECT count(*) FROM main."{tbl}"').fetchone()[0]
            if dst_n == expected:
                log(f"{tbl}: already migrated ({dst_n:,} rows), skipping")
                continue
            log(f"{tbl}: partial copy detected ({dst_n:,} != {expected:,}), dropping")
            con.execute(f'DROP TABLE main."{tbl}"')

        log(f"{tbl}: starting copy (~{expected:,} rows expected)")
        t0 = time.time()
        con.execute(f'CREATE TABLE main."{tbl}" AS SELECT * FROM src."{tbl}"')
        con.execute("CHECKPOINT")
        elapsed = time.time() - t0

        dst_n = con.execute(f'SELECT count(*) FROM main."{tbl}"').fetchone()[0]
        rate = dst_n / elapsed if elapsed > 0 else 0
        log(f"{tbl}: copied {dst_n:,} rows in {fmt_secs(elapsed)} ({rate:,.0f} rows/s)")
        if dst_n != expected:
            log(f"ERROR: {tbl} row mismatch: expected={expected} dst={dst_n}")
            return 2

        sz_gib = DUCKDB_PATH.stat().st_size / (1024**3)
        log(f"  duckdb file size now: {sz_gib:.2f} GiB")

    for idx_name, tbl, col in INDEXES:
        existing_idx = con.execute(
            "SELECT count(*) FROM duckdb_indexes() "
            "WHERE database_name=? AND schema_name='main' AND index_name=?",
            [dst_db, idx_name],
        ).fetchone()[0]
        if existing_idx:
            log(f"index {idx_name}: already exists, skipping")
            continue
        log(f"creating index {idx_name} on {tbl}({col})")
        t0 = time.time()
        con.execute(f'CREATE INDEX "{idx_name}" ON main."{tbl}"("{col}")')
        con.execute("CHECKPOINT")
        log(f"  {idx_name}: {fmt_secs(time.time() - t0)}")

    log("migration complete; final row counts:")
    for tbl in TABLES:
        sz = con.execute(f'SELECT count(*) FROM main."{tbl}"').fetchone()[0]
        log(f"  {tbl}: {sz:,} rows (expected {EXPECTED_ROWS[tbl]:,})")

    con.close()

    duckdb_size = DUCKDB_PATH.stat().st_size
    log(f"duckdb file: {DUCKDB_PATH} ({duckdb_size / (1024**3):.2f} GiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
