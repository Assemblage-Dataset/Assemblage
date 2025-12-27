'''
Rework models for database with SQLModels
'''

import datetime
from email.policy import default
import json
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import  String, Text, DateTime, Boolean, create_engine
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql.schema import ForeignKey
from sqlalchemy_utils import create_database, database_exists
from sqlmodel import Integer, Enum, Field, Session, SQLModel, create_engine, select, Relationship, Column
from assemblage.consts import BuildStatus, CloneStatus, PriorityStatus, OptLevel, SupportedArchitecture, SupportedCompiler, SupportedLanguage, SupportedPlatform
from pydantic import HttpUrl

Base = declarative_base()

class BuildOpt(SQLModel, table=True):
    """ build option for how to build a repo """
    __tablename__ = 'buildopt'
    id: int = Field( primary_key=True)
    # git = Column(String(length=255), default="")
    platform: SupportedPlatform = Field(max_length=255)
    language: SupportedLanguage = Field(max_length=255)
    compiler_name: SupportedCompiler = Field(max_length=10)
    compiler_flag: str | None = Field(max_length=255)
    compiler_version: str = Field(max_length=25)
    toolset_version: str | None = Field(max_length=255)
    build_system: str | None = Field(max_length=255) # ideally this should be a build system  enum. also not sure used
    build_command: str | None = Field(max_length=255) # Dont think this is used. 
    library: SupportedArchitecture = Field(max_length=255) # bad name... architecture better? 
    save_assembly: bool = False
    enable: bool = False
    statuses: list["Status"] = Relationship(back_populates="build_opt")
    

    def __repr__(self) -> str:
        return f'BuildOpt(platform={self.platform}, ,platform={self.platform}, ' \
               f'language={self.language}, compiler flag={self.compiler_flag}), ' \
               f'compiler name={self.compiler_name})'


class Status(SQLModel, table=True):
    """ the build/clone status of repo with a specific build option """
    __tablename__ = 'b_status'

    id: int = Field( primary_key=True)
    # priority high: 2, mid: 1, low 0
    priority: PriorityStatus = Field(default=PriorityStatus.LOW, index=True, nullable=False)
    # 0 : not started 1 : processing 2 : failed 3 : success 10 : command failed
    clone_status: CloneStatus = Field( default=CloneStatus.NOT_STARTED, index=True)
    clone_msg: str = Field(max_length=255, default="")
    build_status: BuildStatus = Field( default=BuildStatus.INIT, index=True)
    build_msg: str = ""
    build_opt_id: int | None = Field(
        default=None, foreign_key="buildopt.id")  # cascade
    repo_id: int = Field( foreign_key="projects.id")  # cascade

    mod_timestamp: int = -1
    build_time: int = -1
    commit_hexsha: str = Field(max_length=255, default="")
    build_opt: BuildOpt | None = Relationship(back_populates="statuses")

    binaries: List["BuildDO"] = Relationship(
        back_populates="status", sa_relationship_kwargs={"cascade": "all, delete"})
    project: "RepoDO" = Relationship(back_populates="statuses")

class BuildDO(SQLModel, table=True):
    """ Build object to collect build information - How binaries are built"""
    __tablename__ = 'binaries'
    id: int = Field( primary_key=True)
    file_name: str = Field(max_length=255, default="")
    description: str = ""
    build_date: datetime.datetime = Field(
        default=datetime.datetime.now(datetime.timezone.utc))
    disassembled: bool = False
    optimization: OptLevel = OptLevel.NONE
    status_id: int = Field( foreign_key="b_status.id")  # cascade
    status: Status | None = Relationship(back_populates="binaries")

    def __repr__(self):
        return f'Repo(File name={self.file_name})'
    class Config:
        use_enum_values = True
        

class RepoDO(SQLModel, table=True):
    """
    ORM model for repo
    TODO: add output dir for clone location of code too( though tco)
    """
    __tablename__ = 'projects'
    id: int = Field( primary_key=True)
    # Column(String(length=255), default="", unique=True)
    url: str = Field(max_length=255, default="", unique=True)
    owner_id: int = 0 
    name: str = Field(max_length=255, default="")
    description: str = ""
    language: SupportedLanguage = Field(max_length=255, default="")
    created_at: datetime.datetime = Field(
        default_factory=datetime.datetime.now(datetime.timezone.utc))
    fork_from: int = 0
    deleted: bool = False
    updated_at: datetime.datetime = Field(
        default=datetime.datetime(1970, 1, 1, 0, 0, 1))
    forked_commit_id: int = 0
    branch: str = Field(max_length=255, default="main")
    # priority high: 2, mid: 1, low 0. 
    priority: PriorityStatus = Field(default=PriorityStatus.LOW, index=True)
    size: int = 0
    # replace this with Enum too?
    build_system: str = Field(max_length=255, default="", index=True)
    statuses: List[Status] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete"})
    commit_hexsha: str = Field(max_length=255, default="", nullable=True)

    def __repr__(self):
        return f'Repo(id={self._id} ,name={self.name}, url={self.url})'
    class Config:
        use_enum_values = True
        
class ScraperData(SQLModel, table=True):
    """
    Tracks persistent data of the scraper(s) of the project
    """
    __tablename__ = 'scrapers'
    id: int = Field( primary_key=True )
    start_time: int = 0
    end_time: int = 0
    owner_uuid: str = Field(max_length=255, default="")