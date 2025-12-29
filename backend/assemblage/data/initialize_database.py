from sqlalchemy import create_engine, text, inspect
import subprocess
import logging
from assemblage.config import CoordinatorSettings

# Basic setup of the DB on fresh installs/when it's deleted. 
# Definitely could use some TLC (are two engines necessary?)
# TODO: could definitely use fewer plain SQL queries, if desired
# Will also break if we move from Postgres. 

def conditional_init_db(db_name : str, db_url : str):

    # We connect to the template1 database instead of the assemblage database
    # because template1 is guaranteed to exist
    s = CoordinatorSettings()
    s.db_name = "template1"
    engine = create_engine(s.databaseURL)

    # Check for existence of assemblage DB from template1, and create it if it doesn't exist
    db_exists = False
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        db_exists = conn.execute(
            text("select exists (SELECT datname FROM pg_catalog.pg_database WHERE datname=:db)"),
            {"db": db_name}
        ).scalar()
        
        if not db_exists:
            logging.info(f"No database '{db_name}' found. Automatically setting up database...")
            conn.execute(text(f"CREATE DATABASE {db_name}"))

    assemblage_engine = create_engine(db_url)
    table_count = 0
    with assemblage_engine.connect() as conn:

        table_count = len( inspect(conn).get_table_names() )

    if table_count == 0:
        logging.info(f"No tables found in database {db_name}. Running Alembic migration...")
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            text=True,
            capture_output=True
        )

    engine.dispose()
    assemblage_engine.dispose()
    logging.info(f"Database ready")