'''
object model in database

Yihao Sun
'''

import datetime
import json

from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql.expression import column
from sqlalchemy.sql.schema import ForeignKey
from sqlalchemy_utils import create_database, database_exists


Base = declarative_base()


class Status(Base):
    """ the build/clone status of repo with a specific build option """
    __tablename__ = 'b_status'

    _id = Column(Integer, primary_key=True, autoincrement=True,)
    # priority high: 2, mid: 1, low 0
    priority = Column(Integer, default=0, nullable=False, index=True)
    # 0 : Normal 1 : Prioritized
    clone_status = Column(Integer, default=0)
    clone_msg = Column(String(length=255), default='')
    build_status = Column(Integer, default=0)
    build_msg = Column(Text, default='')
    build_opt_id = Column(Integer, ForeignKey('buildopt._id', ondelete="CASCADE"))
    repo_id = Column(Integer, ForeignKey("projects._id", ondelete="CASCADE"))
    mod_timestamp = Column(Integer, default=-1)
    build_time = Column(Integer, default=-1)
    # commit_hexsha = Column(String(length=255), default='')
    binaries = relationship('BuildDO', cascade="all, delete", passive_deletes=True)

    @property
    def id(self):
        # pylint: disable=missing-function-docstring,invalid-name
        return self._id


class BuildOpt(Base):
    """ build option for how to build a repo """
    __tablename__ = 'buildopt'
    _id = Column(Integer, primary_key=True)
    # git = Column(String(length=255), default='')
    platform = Column(String(length=255), default='')
    language = Column(String(length=255), default='')
    compiler_name = Column(String(length=10), default='')
    compiler_flag = Column(String(length=255), default='')
    build_system = Column(String(length=255), default='')
    build_command = Column(String(length=255), default='')
    library = Column(String(length=255), default='')
    enable = Column(Boolean, default=False)

    def __repr__(self) -> str:
        return f'BuildOpt(platform={self.platform}, ,platform={self.platform}, ' \
               f'language={self.language}, compiler flag={self.compiler_flag}), ' \
               f'compiler name={self.compiler_name})'

    @property
    def id(self):
        # pylint: disable=missing-function-docstring,invalid-name
        return self._id


class BuildDO(Base):
    """ Build object to collect build information - How binaries are built"""
    __tablename__ = 'binaries'
    _id = Column(Integer, primary_key=True, autoincrement=True,)
    file_name = Column(String(length=255))
    description = Column(Text, default='')
    build_date = Column(DateTime, default=datetime.datetime.utcnow)
    disasmed = Column(Boolean, default=False)
    status_id = Column(Integer, ForeignKey('b_status._id', ondelete="CASCADE"))

    def __repr__(self):
        return f'Repo(File name={self.file_name})'

    @property
    def id(self):
        # pylint: disable=missing-function-docstring,invalid-name
        return self._id


class RepoDO(Base):
    """
    ORM model for repo
    """
    __tablename__ = 'projects'
    _id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(length=255), default='', unique=True)
    owner_id = Column(Integer, default=0)
    name = Column(String(length=255))
    description = Column(Text, default='')
    language = Column(String(length=255), default='')
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    fork_from = Column(Integer, default=0)
    deleted = Column(Boolean, default=False)
    updated_at = Column(
        DateTime, default=datetime.datetime(1970, 1, 1, 0, 0, 1))
    forked_commit_id = Column(Integer, default=0)
    # priority high: 2, mid: 1, low 0
    priority = Column(Integer, default=0)
    size = Column(Integer, default=0)
    commit = Column(String(length=16), default='')
    build_system = Column(String(length=255), default='', index=True)
    reserved = Column(String(length=64), default='')
    statuses = relationship("Status", cascade="all, delete",
                            passive_deletes=True)

    def __repr__(self):
        return f'Repo(id={self._id} ,name={self.name}, url={self.url}, head={self.commit})'

    @property
    def id(self):
        # pylint: disable=missing-function-docstring,invalid-name
        return self._id


def init_clean_database(db_str):
    """ init and drop all data in original database """
    try:
        engine = create_engine(db_str, connect_args={'connect_timeout': 10})
    except Exception as err:
        print("Cant establish DB connection to", db_str, err)
        return
    try:
        sessionmaker(engine).close_all()
        BuildDO.__table__.drop(engine)
        Status.__table__.drop(engine)
        RepoDO.__table__.drop(engine)
        BuildOpt.__table__.drop(engine)
    except Exception as err:
        print(err)
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


if __name__ == '__main__':
    with open("assemblage/configure/coordinator_config.json") as f:
        coordinator_config = json.load(f)
    init_clean_database(coordinator_config["db_path"])
