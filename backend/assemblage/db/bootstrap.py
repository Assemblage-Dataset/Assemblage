"""First-boot database bring-up (folded in from ``data/initialize_database.py``).

On a fresh deployment the coordinator's target database may not exist yet, or
may exist with no tables. ``conditional_init_db`` connects to the always-present
``template1`` database to create the target if missing, then runs
``alembic upgrade head`` when the target has no tables. This is the runbook the
coordinator performs automatically so a clean volume comes up without manual
migration steps.
"""

import logging
import subprocess

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from assemblage.settings import DatabaseSettings

logger = logging.getLogger(__name__)


def conditional_init_db(db: DatabaseSettings) -> None:
    """Create the target database if absent and migrate it to head if empty."""
    # template1 is guaranteed to exist; use it to check for / create the target.
    template_url = make_url(db.url).set(database="template1")
    template_engine = create_engine(template_url)
    try:
        with template_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            exists = conn.execute(
                text(
                    "SELECT EXISTS (SELECT datname FROM pg_catalog.pg_database WHERE datname=:db)"
                ),
                {"db": db.database},
            ).scalar()
            if not exists:
                logger.info("database %r not found; creating it", db.database)
                conn.execute(text(f"CREATE DATABASE {db.database}"))
    finally:
        template_engine.dispose()

    app_engine = create_engine(db.url)
    try:
        with app_engine.connect() as conn:
            table_count = len(inspect(conn).get_table_names())
    finally:
        app_engine.dispose()

    if table_count == 0:
        logger.info("no tables in %r; running alembic upgrade head", db.database)
        subprocess.run(["alembic", "upgrade", "head"], check=True, text=True, capture_output=True)
    logger.info("database ready")
