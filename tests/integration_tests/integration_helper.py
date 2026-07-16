"""
Various functions for seeding and clearing data, as well as constants used by integration testing.
I don't touch the alembic tables, but everything else is fair game.
"""

import logging

import assemblage.consts as const
import assemblage.data.db as db
import assemblage.database.models as model
import assemblage.mq.messages as msg
import sqlalchemy as sqla
from assemblage.consts import TEST_DB_ADDR

helper_dbm = db.DBManager(TEST_DB_ADDR)

logger = logging.getLogger(__name__)


def apply_test_db_settings(settings):
    """Point a Settings object at TEST_DB_ADDR.

    Coordinator (and any code that builds its engine from ``settings``) then
    talks to the same database the helpers seed, regardless of whether that is
    the old compose ``assemblage-test-db`` host or a scratch db on localhost.
    """
    url = sqla.engine.make_url(TEST_DB_ADDR)
    settings.db_host = url.host
    settings.db_port = url.port or 5432
    settings.db_name = url.database
    settings.db_user = url.username
    settings.db_pass = url.password
    return settings


def seed_database_projects():
    """
    Seeds the database with:
    * 3 test projects: PROJECT_1, PROJECT_2, PROJECT_3
    """

    dict1 = msg.ScraperDataOutSingle(
        "PROJECT_1",
        "URL_1",
        "LANG",
        11,
        "DESCRIPTION",
        "2025-11-15 12:28:25",
        "2025-11-15 12:41:59",
        5,
        "BUILD_SYS",
        "BRANCH",
    ).to_dict()
    dict2 = msg.ScraperDataOutSingle(
        "PROJECT_2",
        "URL_2",
        "LANG",
        12,
        "DESCRIPTION",
        "2025-11-15 12:28:25",
        "2025-11-15 12:41:59",
        10,
        "BUILD_SYS",
        "BRANCH",
    ).to_dict()
    dict3 = msg.ScraperDataOutSingle(
        "PROJECT_3",
        "URL_3",
        "LANG",
        13,
        "DESCRIPTION",
        "2025-11-15 12:28:25",
        "2025-11-15 12:41:59",
        15,
        "BUILD_SYS",
        "BRANCH",
    ).to_dict()
    with helper_dbm.get_session() as session:
        session.add(model.RepoDO(**dict1))
        session.add(model.RepoDO(**dict2))
        session.add(model.RepoDO(**dict3))


def seed_database_buildopts():
    buildopt1 = {
        "platform": "linux",
        "language": "c++",
        "compiler_name": "clang",
        "build_system": "all",
        "library": "x64",
        "enable": True,
        "compiler_version": "10.0.0",
        "save_assembly": True,
    }
    buildopt2 = {
        "platform": "linux",
        "language": "c++",
        "compiler_name": "gcc",
        "build_system": "all",
        "library": "x64",
        "enable": True,
        "compiler_version": "10.0.0",
        "save_assembly": True,
    }
    with helper_dbm.get_session() as session:
        session.add(model.BuildOpt(**buildopt1))
        session.add(model.BuildOpt(**buildopt2))


def seed_database_statuses_unstarted():
    """
    Seeds the database with 6 total build status options (every project has 2 build opts)
    """
    project1_b1 = {
        "clone_status": const.CloneStatus.NOT_STARTED,
        "build_status": const.BuildStatus.INIT,
        "build_opt_id": 1,
        "repo_id": 1,
    }
    project1_b2 = {
        "clone_status": const.CloneStatus.NOT_STARTED,
        "build_status": const.BuildStatus.INIT,
        "build_opt_id": 2,
        "repo_id": 1,
    }
    project2_b1 = {
        "clone_status": const.CloneStatus.NOT_STARTED,
        "build_status": const.BuildStatus.INIT,
        "build_opt_id": 1,
        "repo_id": 2,
    }
    project2_b2 = {
        "clone_status": const.CloneStatus.NOT_STARTED,
        "build_status": const.BuildStatus.INIT,
        "build_opt_id": 2,
        "repo_id": 2,
    }
    project3_b1 = {
        "clone_status": const.CloneStatus.NOT_STARTED,
        "build_status": const.BuildStatus.INIT,
        "build_opt_id": 1,
        "repo_id": 3,
    }
    project3_b2 = {
        "clone_status": const.CloneStatus.NOT_STARTED,
        "build_status": const.BuildStatus.INIT,
        "build_opt_id": 2,
        "repo_id": 3,
    }
    with helper_dbm.get_session() as session:
        session.add(model.Status(**project1_b1))
        session.add(model.Status(**project1_b2))
        session.add(model.Status(**project2_b1))
        session.add(model.Status(**project2_b2))
        session.add(model.Status(**project3_b1))
        session.add(model.Status(**project3_b2))


def truncate_all():
    """
    Clears all the tables. Note that these commands are database specific and may need to be changed if
    we migrate from the current PostgreSQL system. Doesn't touch Alembic table since we don't test on it.

    The three primary parts of this command are 1) delete all table rows 2) restart any identity sequences
    3) cascade across other tables if foreign keys are involved. Not all tables need 2) and 3), but including
    them in all the commands allows for flexibility if the schema changes.
    """
    with helper_dbm.get_session() as session:
        session.execute(sqla.sql.text("TRUNCATE TABLE b_status RESTART IDENTITY CASCADE"))
        session.execute(sqla.sql.text("TRUNCATE TABLE binaries RESTART IDENTITY CASCADE"))
        session.execute(sqla.sql.text("TRUNCATE TABLE buildopt RESTART IDENTITY CASCADE"))
        session.execute(sqla.sql.text("TRUNCATE TABLE projects RESTART IDENTITY CASCADE"))
