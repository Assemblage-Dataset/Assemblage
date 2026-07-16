"""Integration-suite fixtures: live-DB guard + schema bring-up.

These fixtures run only for tests collected under tests/integration_tests
(a package-scoped conftest), so importing this module during a plain unit-test
collection is side-effect free — the guard and the migration only fire when an
integration test actually runs.
"""

import pathlib

import pytest
import sqlalchemy as sqla
from alembic import command
from alembic.config import Config
from assemblage.consts import TEST_DB_ADDR

# backend/alembic.ini — %(here)s inside it resolves script_location to
# backend/alembic, so this works regardless of the process cwd.
_ALEMBIC_INI = pathlib.Path(__file__).resolve().parents[2] / "backend" / "alembic.ini"


def alembic_config_for(url: str) -> Config:
    """An Alembic Config pinned to ``url`` (env.py honours a preset url)."""
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session", autouse=True)
def _guard_live_database() -> None:
    """Refuse to run against the production ``assemblage`` database.

    The live volume holds ~30k real projects; the seeding helpers truncate
    tables. A target database literally named 'assemblage' is almost certainly
    the production corpus, so fail loudly rather than destroy it. Point
    TEST_DB_ADDR at a scratch database (e.g. 'assemblage_test') instead.
    """
    db_name = sqla.engine.make_url(TEST_DB_ADDR).database
    if db_name == "assemblage":
        pytest.fail(
            "Refusing to run integration tests against a database named "
            "'assemblage' (the production corpus). Set TEST_DB_ADDR to a "
            "scratch database such as '.../assemblage_test'.",
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_database(_guard_live_database: None) -> None:
    """Bring the scratch database up to head so the seeding helpers have tables."""
    command.upgrade(alembic_config_for(TEST_DB_ADDR), "head")
