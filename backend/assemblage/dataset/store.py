import datetime
import random
import time
import logging

import sqlalchemy.exc
from sqlalchemy import select, update, create_engine, func, or_, delete, insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import desc, true
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import Insert
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, create_engine, LargeBinary, Float
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql.expression import column
from sqlalchemy.sql.schema import ForeignKey
from sqlalchemy_utils import create_database, database_exists
from sqlalchemy.engine import Engine
from sqlalchemy import event
from dataset_orm import Binary, Function, Line, Base, init_clean_database, RVA, PDB

BULK_CHUNK_SIZE = 5000

class Dataset_DB:
    """ manager for db query and connection """

    def __init__(self, db_addr):
        self.db_addr = db_addr
        self.engine = create_engine(db_addr, echo=False,
                                    pool_pre_ping=True
                                    )

    def shutdown(self):
        """ Close DB connection """
        self.engine.dispose()

    def get_binary_by_id(self, bin_id):
        with Session(self.engine) as session:
            query = select(Binary).where(Binary.id == bin_id)
            result = session.execute(query).first()
            return result[0].path

    def add_binary(self, github_url, file_name, platform, build_mode, pushed_at, toolset_version, optimization, path, size):
        with Session(self.engine) as session:
            new_binary = Binary(github_url=github_url,
                                file_name=file_name,
                                platform=platform,
                                path=path,
                                build_mode=build_mode,
                                toolset_version=toolset_version,
                                pushed_at=pushed_at,
                                optimization=optimization,
                                size=size)
            session.add(new_binary)
            session.commit()
            return new_binary.id

    def bulk_add_binaries(self, binaries):
        """ used to import lot of repos at a time """
        rows = list(binaries)
        if not rows:
            return
        with self.engine.begin() as conn:
            for i in range(0, len(rows), BULK_CHUNK_SIZE):
                conn.execute(insert(Binary), rows[i:i + BULK_CHUNK_SIZE])

    def add_function(self, name, source_file, rvas, binary_id):
        with Session(self.engine) as session:
            new_function = Function(name=name,
                                    source_file=source_file,
                                    rvas=rvas,
                                    binary_id=binary_id)
            session.add(new_function)
            session.commit()
            return new_function.id

    def bulk_add_functions(self, functions):
        """ used to import lot of repos at a time """
        if not functions:
            return
        with self.engine.begin() as conn:
            for i in range(0, len(functions), BULK_CHUNK_SIZE):
                conn.execute(insert(Function), functions[i:i + BULK_CHUNK_SIZE])

    def bulk_add_pdbs(self, pdbs):
        """ used to import lot of repos at a time """
        if not pdbs:
            return
        with self.engine.begin() as conn:
            for i in range(0, len(pdbs), BULK_CHUNK_SIZE):
                conn.execute(insert(PDB), pdbs[i:i + BULK_CHUNK_SIZE])

    def bulk_add_rvas(self, rvas):
        """ used to import lot of repos at a time """
        if not rvas:
            return
        with self.engine.begin() as conn:
            for i in range(0, len(rvas), BULK_CHUNK_SIZE):
                conn.execute(insert(RVA), rvas[i:i + BULK_CHUNK_SIZE])

    def add_line(self, line_number, rva, length, source_code, function_id):
        with Session(self.engine) as session:
            new_line = Line(line_number=line_number,
                            rva=rva,
                            length=length,
                            source_code=source_code,
                            function_id=function_id)
            session.add(new_line)
            session.commit()
            return new_line.id

    def bulk_add_lines(self, lines):
        """ used to import lot of repos at a time """
        if not lines:
            return
        with self.engine.begin() as conn:
            for i in range(0, len(lines), BULK_CHUNK_SIZE):
                conn.execute(insert(Line), lines[i:i + BULK_CHUNK_SIZE])

    def bulk_flush(self, binaries, functions, lines, rvas, pdbs,
                   include_functions=True, include_lines=True,
                   include_rvas=True, include_pdbs=True):
        """Insert all tables, one per-table transaction so a failure in one
        table doesn't roll back successfully-prepared rows in another. Each
        chunk is its own transaction; on chunk failure we log loudly and
        continue with the next chunk rather than losing the whole batch.
        """
        bin_rows = list(binaries)

        def _insert_chunked(table, rows, label):
            for i in range(0, len(rows), BULK_CHUNK_SIZE):
                chunk = rows[i:i + BULK_CHUNK_SIZE]
                try:
                    with self.engine.begin() as conn:
                        conn.execute(insert(table), chunk)
                except sqlalchemy.exc.SQLAlchemyError as e:
                    logging.error(
                        "bulk_flush: %s chunk %d-%d (size %d) failed: %s",
                        label, i, i + len(chunk), len(chunk), e,
                    )
                    # Best-effort row-by-row recovery so a single bad row
                    # does not lose the whole chunk.
                    for row in chunk:
                        try:
                            with self.engine.begin() as conn:
                                conn.execute(insert(table), [row])
                        except sqlalchemy.exc.SQLAlchemyError as e2:
                            logging.error(
                                "bulk_flush: %s row dropped: %s (row=%r)",
                                label, e2, row,
                            )

        _insert_chunked(Binary, bin_rows, "binaries")
        if include_functions and functions:
            _insert_chunked(Function, functions, "functions")
        if include_lines and lines:
            _insert_chunked(Line, lines, "lines")
        if include_rvas and rvas:
            _insert_chunked(RVA, rvas, "rvas")
        if include_pdbs and pdbs:
            _insert_chunked(PDB, pdbs, "pdbs")

    def delete_binary(self, binary_id, filepath=None):
        with Session(self.engine) as session:
            if filepath:
                q = delete(Binary).where(Binary.path==filepath)
            else:
                q = delete(Binary).where(Binary.id==binary_id)
            session.execute(q)
            session.commit()
    
    def update_license(self, url, license):
        with Session(self.engine) as session:
            q = update(Binary).where(Binary.github_url == url).values(license=license)
            session.execute(q)
            session.commit()

    def update_version(self, url, version):
        with Session(self.engine) as session:
            q = update(Binary).where(Binary.github_url == url).values(repo_commit=version)
            session.execute(q)
            session.commit()

    def get_all_urls(self):
        with Session(self.engine) as session:
            query = select(Binary.github_url).where(Binary.license=="").distinct()
            result = session.execute(query).all()
            return [res[0] for res in result]

    def get_all_bins(self):
        with Session(self.engine) as session:
            query = select(Binary)
            result = session.execute(query).all()
            for res in result:
                yield res[0]

    def get_func_by_binid(self, binid):
        with Session(self.engine) as session:
            query = select(Function).where(Function.binary_id==binid)
            result = session.execute(query).all()
            for res in result:
                yield res[0]

    def get_rva_by_funcid(self, funcid):
        with Session(self.engine) as session:
            query = select(RVA).where(RVA.function_id==funcid)
            result = session.execute(query).all()
            for res in result:
                yield res[0]
    
    def update_func_hash(self, funcid, hashval):
        with Session(self.engine) as session:
            q = update(Function).where(Function.id == funcid).values(hash=hashval)
            session.execute(q)
            session.commit()

    def init(self):
        init_clean_database(self.db_addr)