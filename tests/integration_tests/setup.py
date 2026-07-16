import assemblage.data.db as db
import sqlalchemy as sqla
from alembic import command
from alembic.config import Config

from tests.constants import TEST_DB_ADDR


def setup():
    """
    Upgrades the assemblage-test-db with alembic upgrade head.
    """
    # DBManager calls create_engine, which creates DB if it doesn't exist
    dbm = db.DBManager(TEST_DB_ADDR)

    # Overwrite the default DB address with the test address
    config = Config("/app/alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DB_ADDR)

    # Build tables
    command.upgrade(config, "head")
    # Note: this line runs env.py, which disables loggers from working properly.
    # Hence all the informational logs in this script use prints instead of loggers.
    # I could spend a few hours figuring out how to get the loggers back, or I could use prints, so we're using prints.

    # Check that tables are as expected
    engine = sqla.create_engine(TEST_DB_ADDR)
    inspector = sqla.inspect(engine)
    tables = set(inspector.get_table_names())

    print(f"SETUP: Found tables: {tables}")
    expected_tables = {
        "b_status",
        "projects",
        "alembic_version",
        "binaries",
        "buildopt",
        "scrapers",
    }
    assert expected_tables == tables
    print("SETUP: Success. Exit and restart with proper command.")
    print("OK")


setup()
