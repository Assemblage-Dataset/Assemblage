from sqlalchemy import create_engine, text, inspect
import subprocess
import logging
from assemblage.config import CoordinatorSettings

# Basic setup of the DB on fresh installs/when it's deleted. 
# Definitely could use some TLC (are two engines necessary?)
# TODO: could definitely use fewer plain SQL queries, if desired

def conditional_init_db(settings: CoordinatorSettings):

    # We connect to the template1 database instead of the assemblage database
    # because template1 is guaranteed to exist
    s = CoordinatorSettings()
    s.db_name = "template1"
    engine = create_engine(s.databaseURL)

    # Check for existence of assemblage DB, and create it if it doesn't exist
    db_exists = False
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        db_exists = conn.execute(
            text("select exists (SELECT datname FROM pg_catalog.pg_database WHERE datname=:db)"),
            {"db": settings.db_name}
        ).scalar()
        
        if not db_exists:
            logging.info(f"No database '{settings.db_name}' found. Automatically setting up database...")
            conn.execute(text(f"CREATE DATABASE {settings.db_name}"))

    if not db_exists:
        logging.info(f"No tables found. Running Alembic migration...")
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            text=True,
            capture_output=True
        )

    engine.dispose()
    logging.info(f"Database ready")