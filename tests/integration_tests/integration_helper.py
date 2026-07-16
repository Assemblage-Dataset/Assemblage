"""Seeding and teardown helpers for the coordinator integration tests.

Rewritten for the P6 store: everything goes through ``CoordinatorStore`` /
``db.models`` (the old ``DBManager`` / ``mq.messages`` seed path is gone).
"""

import datetime
import logging

import sqlalchemy as sqla
from assemblage.db.engine import make_engine, session_scope
from assemblage.db.models import BuildOpt, RepoDO, Status
from assemblage.enums import BuildStatus, CloneStatus
from assemblage.settings import DatabaseSettings

from tests.constants import TEST_DB_ADDR

logger = logging.getLogger(__name__)

_engine = make_engine(TEST_DB_ADDR)

_CREATED_AT = datetime.datetime(2025, 11, 15, 12, 28, 25)
_UPDATED_AT = datetime.datetime(2025, 11, 15, 12, 41, 59)


def make_store():
    """A CoordinatorStore pointed at the scratch test database."""
    from assemblage.db.store import CoordinatorStore

    return CoordinatorStore(make_engine(TEST_DB_ADDR))


def test_database_settings() -> DatabaseSettings:
    """A ``DatabaseSettings`` describing ``TEST_DB_ADDR`` (for app-level wiring)."""
    url = sqla.engine.make_url(TEST_DB_ADDR)
    return DatabaseSettings(
        host=url.host or "localhost",
        port=url.port or 5432,
        database=url.database or "assemblage_test",
        user=url.username or "assemblage",
        password=url.password or "assemblage",
    )


def seed_database_projects(language: str = "c++") -> None:
    """Seed three projects: PROJECT_1, PROJECT_2, PROJECT_3.

    ``language`` defaults to ``"c++"`` (what the scraper stores) so the seeded
    repos match the seeded c++ buildopts under language-aware fan-out.
    """
    with session_scope(_engine) as session:
        for i, owner in enumerate((11, 12, 13), start=1):
            session.add(
                RepoDO(
                    name=f"PROJECT_{i}",
                    url=f"URL_{i}",
                    language=language,
                    owner_id=owner,
                    description="DESCRIPTION",
                    created_at=_CREATED_AT,
                    updated_at=_UPDATED_AT,
                    size=5 * i,
                    build_system="BUILD_SYS",
                    branch="BRANCH",
                )
            )


def seed_database_buildopts() -> None:
    """Seed two 'all' build options (clang, gcc)."""
    with session_scope(_engine) as session:
        for compiler in ("clang", "gcc"):
            session.add(
                BuildOpt(
                    platform="linux",
                    language="c++",
                    compiler_name=compiler,
                    compiler_flag="",
                    build_system="all",
                    build_command="",
                    library="x64",
                    enable=True,
                    compiler_version="10.0.0",
                    save_assembly=True,
                )
            )


def seed_database_statuses_unstarted() -> None:
    """Seed six un-started statuses (each of 3 projects x 2 build options)."""
    with session_scope(_engine) as session:
        for repo_id in (1, 2, 3):
            for opt_id in (1, 2):
                session.add(
                    Status(
                        clone_status=CloneStatus.NOT_STARTED,
                        build_status=BuildStatus.INIT,
                        build_opt_id=opt_id,
                        repo_id=repo_id,
                    )
                )


def truncate_all() -> None:
    """Clear the five ORM tables (PostgreSQL-specific; leaves alembic_version)."""
    with session_scope(_engine) as session:
        for table in ("b_status", "binaries", "buildopt", "projects"):
            session.execute(sqla.text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
