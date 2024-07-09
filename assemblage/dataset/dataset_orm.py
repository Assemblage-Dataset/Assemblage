'''
Object model for Assemblage dataset
Chang Liu
'''

import datetime
import json

from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, create_engine, LargeBinary, Float
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
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


Base = declarative_base()


class Binary(Base):
    __tablename__ = 'binaries'

    id = Column(Integer, primary_key=True, autoincrement=True,)
    file_name = Column(String(length=255))
    path = Column(String(length=255))
    platform = Column(String(length=15))
    build_mode = Column(String(length=15))
    toolset_version = Column(String(length=15))
    github_url = Column(String(length=255))
    optimization = Column(String(length=15))
    pushed_at = Column(DateTime, default=datetime.datetime.utcnow)
    size = Column(Integer, default=0)


class Function(Base):
    __tablename__ = 'functions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(length=255))
    source_file = Column(String(length=255))
    intersect_ratio = Column(Float)
    rvas = Column(String(length=255))
    binary_id = Column(Integer, ForeignKey('binaries.id'))


class Line(Base):
    __tablename__ = 'lines'
    id = Column(Integer, primary_key=True, autoincrement=True)
    line_number = Column(Integer)
    rva = Column(String(length=255))
    length = Column(Integer)
    source_code = Column(Text)
    function_id = Column(Integer, ForeignKey('functions.id'),)


def init_clean_database(db_str):
    """ init and drop all data in original database """
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
        print("Creating tables, don't exit program")
        Base.metadata.create_all(engine)
    except Exception as err:
        print(err)
    print("Finished")
