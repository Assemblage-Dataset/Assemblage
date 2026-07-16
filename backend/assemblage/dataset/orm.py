"""
Object model for Assemblage dataset
Chang Liu
"""

import sqlite3
from sqlite3 import Connection as SQLite3Connection

from sqlalchemy import BigInteger, Column, Integer, String, Text, create_engine, event
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql.schema import ForeignKey
from sqlalchemy_utils import create_database, database_exists


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, SQLite3Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=OFF;")
        cursor.execute("PRAGMA cache_size=-64000;")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY;")
        cursor.execute("PRAGMA foreign_keys=OFF;")
        cursor.close()


Base = declarative_base()


class Binary(Base):
    __tablename__ = "binaries"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    file_name = Column(String(length=256))
    platform = Column(String(length=16))
    build_mode = Column(String(length=32))
    toolset_version = Column(String(length=32))
    github_url = Column(String(length=256))
    optimization = Column(String(length=8))
    repo_last_update = Column(Integer)
    size = Column(Integer, default=0)
    path = Column(String(length=256))
    license = Column(String(length=128), default="")
    hash = Column(String(length=64))
    repo_commit = Column(String(length=64))
    binary_format = Column(String(length=8), default="")  # "PE" or "ELF"
    # Compiler/build identity recovered from the PG buildopt join (nullable; old
    # C/C++ rows predate these columns and stay NULL). build_mode above keeps
    # riding as before; codegen_backend/language/compiler are net-new so a Rust
    # binary records its rustc backend and language directly rather than only
    # via the S3 path string.
    compiler = Column(String(length=32), nullable=True)
    language = Column(String(length=32), nullable=True)
    codegen_backend = Column(String(length=32), nullable=True)


class Function(Base):
    __tablename__ = "functions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(length=512))
    hash = Column(String(length=64))
    binary_id = Column(Integer, ForeignKey("binaries.id"))
    top_comments = Column(Text)
    # body_comments = Column(Text)
    source_codes = Column(Text)
    # source_codes_ctags = Column(Text)
    prototype = Column(Text)
    source_file = Column(Text)
    # `name` above stays the mangled/linkage symbol (the stable unique id, as
    # today). demangled_name is net-new: the builder demangles Rust v0 symbols
    # (rustfilt) at build time; C/C++ rows leave it NULL. origin classifies a
    # function as in_repo / dependency / stdlib (Rust only; NULL for C/C++).
    demangled_name = Column(String(length=1024), nullable=True)
    origin = Column(String(length=16), nullable=True)


class RVA(Base):
    __tablename__ = "rvas"
    id = Column(Integer, primary_key=True, autoincrement=True)
    start = Column(BigInteger)
    end = Column(BigInteger)
    function_id = Column(Integer, ForeignKey("functions.id"))


class Line(Base):
    __tablename__ = "lines"
    id = Column(Integer, primary_key=True, autoincrement=True)
    line_number = Column(Integer)
    source_file = Column(String(length=512))
    source_code = Column(Text)
    rva = Column(String(length=20))
    length = Column(Integer)
    function_id = Column(
        Integer,
        ForeignKey("functions.id"),
    )


class PDB(Base):
    __tablename__ = "pdbs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    binary_id = Column(
        Integer,
        ForeignKey("binaries.id"),
    )
    pdb_path = Column(String(length=128))


def init_clean_database(db_str):
    try:
        engine = create_engine(db_str)
    except Exception as err:
        print("Cant establish DB connection to", db_str, err)
        return
    try:
        sessionmaker(engine).close_all()
        Binary.__table__.drop(engine)
        Function.__table__.drop(engine)
        Line.__table__.drop(engine)
    except Exception:
        pass
    try:
        if not database_exists(db_str):
            create_database(db_str)
    except Exception as err:
        print(err)
    try:
        Base.metadata.create_all(engine)
    except Exception as err:
        print(err)
    print("Finished")


# Indexes the dataset queries depend on (see the dataset README):
# (index name, table, column).
_DATASET_INDEXES = (
    ("ix_functions_binary_id", "functions", "binary_id"),
    ("ix_rvas_function_id", "rvas", "function_id"),
    ("ix_lines_function_id", "lines", "function_id"),
)


def migrate_existing_db(db_path):
    """Idempotently bring an existing SQLite dataset database up to the current
    ORM schema.

    The daily pipeline appends into a cumulative SQLite file that may have been
    created by an older revision of these models. For every table the ORM knows
    about, add any column the ORM defines but the on-disk table is missing
    (SQLite ``ALTER TABLE ... ADD COLUMN``), then create the lookup indexes the
    dataset queries rely on. Safe to run repeatedly: every mutation is guarded
    by a ``PRAGMA table_info`` / ``sqlite_master`` check or by
    ``CREATE INDEX IF NOT EXISTS``.

    ``db_path`` is a filesystem path to the SQLite file (not a SQLAlchemy URL).
    """
    dialect = sqlite_dialect.dialect()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        existing_tables = {row[0] for row in cur.fetchall()}

        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                # Absent entirely: fresh databases are created by
                # init_clean_database / create_all, not patched here.
                continue
            cur.execute(f'PRAGMA table_info("{table.name}")')
            existing_columns = {row[1] for row in cur.fetchall()}
            for col in table.columns:
                if col.name in existing_columns:
                    continue
                coltype = col.type.compile(dialect=dialect)
                cur.execute(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}')

        for index_name, table_name, column_name in _DATASET_INDEXES:
            if table_name not in existing_tables:
                continue
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ("{column_name}")'
            )

        conn.commit()
    finally:
        conn.close()
