"""Smoke tests for the absorbed dataset pipeline package.

Two guarantees:

1. Every importable ``assemblage.dataset`` module loads cleanly under the
   package layout (catches a stale flat sibling import surviving the P9 move).
2. ``migrate_existing_db`` — the function that was lost when the .git tree was
   destroyed and reconstructed in P9 — is callable and idempotent: it brings an
   older SQLite schema up to the current ORM (adds missing columns, creates the
   documented lookup indexes) and running it twice is a no-op.

Nothing here touches PostgreSQL, MinIO, or the network. The suite requires the
``dataset`` extra (click, tqdm); it skips cleanly when the extra is absent.
"""

import sqlite3

import pytest

# The dataset package pulls in click/tqdm transitively; skip the whole module
# (rather than error) when the optional `dataset` extra is not installed.
pytest.importorskip("tqdm")
pytest.importorskip("click")


def test_import_all_dataset_modules():
    """Every package module imports under the new assemblage.dataset layout."""
    import importlib

    for name in (
        "assemblage.dataset",
        "assemblage.dataset.orm",
        "assemblage.dataset.store",
        "assemblage.dataset.construct",
        "assemblage.dataset.pipeline",
        "assemblage.dataset.cli",
        "assemblage.dataset.daily",
    ):
        module = importlib.import_module(name)
        assert module is not None


def test_migrate_existing_db_is_callable():
    from assemblage.dataset.orm import migrate_existing_db

    assert callable(migrate_existing_db)


# A deliberately old, narrow schema: each table exists but is missing most of
# the columns the current ORM defines. migrate_existing_db must widen them.
_OLD_SCHEMA = """
CREATE TABLE binaries  (id INTEGER PRIMARY KEY, file_name TEXT);
CREATE TABLE functions (id INTEGER PRIMARY KEY, name TEXT, binary_id INTEGER);
CREATE TABLE rvas      (id INTEGER PRIMARY KEY, function_id INTEGER);
CREATE TABLE lines     (id INTEGER PRIMARY KEY, function_id INTEGER);
CREATE TABLE pdbs      (id INTEGER PRIMARY KEY, binary_id INTEGER);
"""


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row[1] for row in rows}


def _indexes(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def test_migrate_existing_db_adds_columns_and_indexes(tmp_path):
    from assemblage.dataset.orm import migrate_existing_db

    db_path = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    conn.commit()
    conn.close()

    migrate_existing_db(db_path)

    # Columns the ORM defines but the old schema lacked are now present.
    bin_cols = _columns(db_path, "binaries")
    assert {"optimization", "hash", "binary_format", "repo_commit", "license"} <= bin_cols
    # The six columns R5 adds for Rust support (build_mode already rode along on
    # binaries; the other five are net-new). All must be added idempotently.
    assert {"compiler", "language", "codegen_backend", "build_mode"} <= bin_cols
    func_cols = _columns(db_path, "functions")
    assert {"top_comments", "source_codes", "prototype", "source_file"} <= func_cols
    assert {"demangled_name", "origin"} <= func_cols
    assert {"start", "end"} <= _columns(db_path, "rvas")
    assert {"line_number", "rva", "length", "source_code"} <= _columns(db_path, "lines")
    assert "pdb_path" in _columns(db_path, "pdbs")

    # Documented lookup indexes exist.
    idx = _indexes(db_path)
    assert {
        "ix_functions_binary_id",
        "ix_rvas_function_id",
        "ix_lines_function_id",
    } <= idx


def test_migrate_existing_db_is_idempotent(tmp_path):
    from assemblage.dataset.orm import migrate_existing_db

    db_path = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    conn.commit()
    conn.close()

    migrate_existing_db(db_path)
    cols_after_first = {
        t: _columns(db_path, t) for t in ("binaries", "functions", "rvas", "lines", "pdbs")
    }
    idx_after_first = _indexes(db_path)

    # A second run must not raise and must not change the schema.
    migrate_existing_db(db_path)
    cols_after_second = {
        t: _columns(db_path, t) for t in ("binaries", "functions", "rvas", "lines", "pdbs")
    }
    idx_after_second = _indexes(db_path)

    assert cols_after_first == cols_after_second
    assert idx_after_first == idx_after_second
