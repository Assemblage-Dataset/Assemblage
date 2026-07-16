'''
Object model for Assemblage dataset
Chang Liu
'''

import datetime
import json

from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, create_engine, LargeBinary, Float, BigInteger
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql.expression import column
from sqlalchemy.sql.schema import ForeignKey
from sqlalchemy_utils import create_database, database_exists
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlite3 import Connection as SQLite3Connection

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
    __tablename__ = 'binaries'

    id = Column(Integer, primary_key=True, autoincrement=True,)
    file_name = Column(String(length=256))
    platform = Column(String(length=16))
    build_mode = Column(String(length=32))
    toolset_version = Column(String(length=32))
    github_url = Column(String(length=256))
    optimization = Column(String(length=8))
    repo_last_update = Column(Integer)
    size = Column(Integer, default=0)
    path = Column(String(length=256))
    license = Column(String(length=128), default='')
    hash = Column(String(length=64))
    repo_commit = Column(String(length=64))
    binary_format = Column(String(length=8), default='')  # "PE" or "ELF"

class Function(Base):
    __tablename__ = 'functions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(length=512))
    hash = Column(String(length=64))
    binary_id = Column(Integer, ForeignKey('binaries.id'))
    top_comments = Column(Text)
    # body_comments = Column(Text)
    source_codes = Column(Text)
    # source_codes_ctags = Column(Text)
    prototype = Column(Text)
    source_file = Column(Text)

class RVA(Base):
    __tablename__ = 'rvas'
    id = Column(Integer, primary_key=True, autoincrement=True)
    start = Column(BigInteger)
    end = Column(BigInteger)
    function_id = Column(Integer, ForeignKey('functions.id'))

class Line(Base):
    __tablename__ = 'lines'
    id = Column(Integer, primary_key=True, autoincrement=True)
    line_number = Column(Integer)
    source_file = Column(String(length=512))
    source_code = Column(Text)
    rva = Column(String(length=20))
    length = Column(Integer)
    function_id = Column(Integer, ForeignKey('functions.id'),)

class PDB(Base):
    __tablename__ = 'pdbs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    binary_id = Column(Integer, ForeignKey('binaries.id'),)
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
    except Exception as err:
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
