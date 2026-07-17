"""SQLModel ORM aligned to the audited live database schema.

The live PostgreSQL database (docker volume ``assemblage_db-data``) uses
**no PG enum types** for its columns: migration ``d3e4f5a6b7c8`` converted
every enum-typed column back to VARCHAR (see backend/alembic/README.md).
Status/priority columns store the enum member **NAMES** (uppercase
``'SUCCESS'``, ``'NOT_STARTED'``, ``'LOW'``) — SQLAlchemy's ``Enum`` type
serializes a Python enum by name into the varchar column. The wire format
(RabbitMQ) carries enum values (lowercase); both conventions are frozen.

The status columns therefore keep their Python enum field types (so call
sites can compare against and assign ``CloneStatus``/``BuildStatus``/
``PriorityStatus`` members) but are backed by a non-native ``sa.Enum``
(``native_enum=False``) whose DDL is a plain ``VARCHAR(N)`` matching the
migrated schema exactly. This keeps ``alembic check`` diff-free against a
freshly-migrated database and against the live one.
"""

import datetime

import sqlalchemy as sa
from sqlmodel import (
    Column,
    Field,
    Relationship,
    SQLModel,
)

from assemblage.enums import BuildStatus, CloneStatus, PriorityStatus


def _name_enum(enum_cls: type, length: int) -> sa.Enum:
    """A varchar-backed ``sa.Enum`` that stores enum member NAMES.

    ``native_enum=False`` emits ``VARCHAR(length)`` (no PG enum type);
    ``create_constraint=False`` suppresses the CHECK constraint the live
    schema never had. Storage stays the member name, identical to the
    previous native-enum behaviour.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
    )


class BuildOpt(SQLModel, table=True):
    """build option for how to build a repo"""

    __tablename__ = "buildopt"
    id: int | None = Field(default=None, primary_key=True)
    platform: str = Field(max_length=255, default="")
    language: str = Field(max_length=255, default="")
    compiler_name: str = Field(max_length=10, default="")
    # fa6e74da04d4 made these three nullable; they never reverted.
    compiler_flag: str | None = Field(max_length=255, default="")
    build_system: str | None = Field(max_length=255, default="")
    build_command: str | None = Field(max_length=255, default="")
    library: str = Field(max_length=255, default="")
    enable: bool = False
    # d22baf7c9f47 added compiler_version/save_assembly NOT NULL; d3e4f5a6b7c8
    # relaxed both to nullable. 2f796fb07698 added toolset_version nullable.
    compiler_version: str | None = Field(default=None, max_length=25)
    save_assembly: bool | None = Field(default=None)
    toolset_version: str | None = Field(default=None, max_length=255)
    # d3e4f5a6b7c8 added build_type NOT NULL DEFAULT 'RelWithDebInfo'.
    build_type: str = Field(
        default="RelWithDebInfo",
        sa_column=Column(
            sa.VARCHAR(length=32),
            nullable=False,
            server_default="RelWithDebInfo",
        ),
    )
    # e9d4c1f2a3b5 added codegen_backend NOT NULL DEFAULT '' ('' = native
    # C/C++ toolchains; Rust builders set llvm/cranelift/gcc).
    codegen_backend: str = Field(
        default="",
        sa_column=Column(
            sa.VARCHAR(length=32),
            nullable=False,
            server_default="",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"BuildOpt(platform={self.platform}, "
            f"language={self.language}, compiler flag={self.compiler_flag}, "
            f"compiler name={self.compiler_name})"
        )


class Status(SQLModel, table=True):
    """the build/clone status of repo with a specific build option"""

    __tablename__ = "b_status"

    id: int = Field(primary_key=True)
    # priority high: 2, mid: 1, low 0
    priority: PriorityStatus = Field(
        default=PriorityStatus.LOW,
        sa_column=Column(_name_enum(PriorityStatus, 16), index=True, nullable=False),
    )
    # 0 : not started 1 : processing 2 : failed 3 : success 10 : command failed
    clone_status: CloneStatus = Field(
        default=CloneStatus.NOT_STARTED,
        sa_column=Column(_name_enum(CloneStatus, 32), index=True, nullable=False),
    )
    clone_msg: str = Field(max_length=255, default="")
    build_status: BuildStatus = Field(
        default=BuildStatus.INIT,
        sa_column=Column(_name_enum(BuildStatus, 32), index=True, nullable=False),
    )
    build_msg: str = ""
    build_opt_id: int | None = Field(default=None, foreign_key="buildopt.id")  # cascade
    repo_id: int = Field(foreign_key="projects.id")  # cascade

    mod_timestamp: int = -1
    build_time: int = -1
    commit_hexsha: str = Field(max_length=255, default="")
    binaries: list["BuildDO"] = Relationship(
        back_populates="status", sa_relationship_kwargs={"cascade": "all, delete"}
    )
    project: "RepoDO" = Relationship(back_populates="statuses")


class BuildDO(SQLModel, table=True):
    """Build object to collect build information - How binaries are built"""

    __tablename__ = "binaries"
    id: int | None = Field(default=None, primary_key=True)
    file_name: str = Field(max_length=255, default="")
    description: str = ""
    build_date: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    disassembled: bool = False
    optimization: str = Field(max_length=16, default="")

    status_id: int = Field(foreign_key="b_status.id")  # cascade
    status: Status | None = Relationship(back_populates="binaries")

    def __repr__(self) -> str:
        return f"Repo(File name={self.file_name})"


class IrArtifactDO(SQLModel, table=True):
    """One IR stage tarball produced for one build (``ir_artifacts``).

    Grain is (build, stage), not (build, crate): IR is packed one gzipped tarball
    per stage because per-crate objects would multiply S3 round-trips for many tiny
    text files. ``crates`` therefore lists what is *inside* the tarball, and
    ``s3_key`` points at the tarball itself.

    Added 2026-07-17 by migration ``a7f3b21c5d84``. Purely additive -- the live
    schema's existing tables are untouched, so pre-IR rows stay valid and every
    reader that ignores this table is unaffected.
    """

    __tablename__ = "ir_artifacts"
    id: int | None = Field(default=None, primary_key=True)
    status_id: int = Field(foreign_key="b_status.id")
    # IrStage member NAME, matching the varchar-stores-names convention the rest of
    # the live schema uses (the RabbitMQ wire carries the lowercase value instead).
    stage: str = Field(max_length=16, default="")
    scope: str = Field(max_length=16, default="")  # IrScope: repo | all
    s3_key: str = Field(max_length=512, default="")
    file_count: int = 0
    crate_count: int = 0
    raw_bytes: int = 0
    stored_bytes: int = 0
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    def __repr__(self) -> str:
        return f"IrArtifact(stage={self.stage}, key={self.s3_key})"


class RepoDO(SQLModel, table=True):
    """
    ORM model for repo
    """

    __tablename__ = "projects"
    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(max_length=255, default="", unique=True)
    owner_id: int = 0
    name: str = Field(max_length=255, default="")
    description: str = ""
    language: str = Field(max_length=255, default="")
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
    fork_from: int = 0
    deleted: bool = False
    updated_at: datetime.datetime = Field(default=datetime.datetime(1970, 1, 1, 0, 0, 1))
    forked_commit_id: int = 0
    # d33a95ecc21a extended branch to varchar(255).
    branch: str = Field(max_length=255, default="master")
    # priority high: 2, mid: 1, low 0
    priority: PriorityStatus = Field(
        default=PriorityStatus.LOW,
        sa_column=Column(_name_enum(PriorityStatus, 16), index=True, nullable=False),
    )
    size: int = 0
    build_system: str = Field(max_length=255, default="", index=True)
    # da9af5c6d2e0 added commit_hexsha nullable; b1c2d3e4f5a6 added license nullable.
    commit_hexsha: str | None = Field(default=None, max_length=255)
    license: str | None = Field(default="", max_length=255)
    statuses: list[Status] = Relationship(
        back_populates="project", sa_relationship_kwargs={"cascade": "all, delete"}
    )

    def __repr__(self) -> str:
        return f"Repo(id={self.id}, name={self.name}, url={self.url})"


class ScraperData(SQLModel, table=True):
    """
    Tracks persistent data of the scraper(s) of the project
    """

    __tablename__ = "scrapers"
    id: int = Field(primary_key=True)
    start_time: int = 0
    end_time: int = 0
    owner_uuid: str = Field(max_length=255, default="")
