"""Engine factory and a re-raising session context manager.

Replaces ``data/db.py``'s ``get_session`` (which swallowed every exception and
rolled back silently). Here the context manager commits on success and — on any
error — rolls back and **re-raises**, so the coordinator's ConsumerLoop sees the
failure and requeues the delivery instead of silently losing it.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


def make_engine(url: str) -> Engine:
    """Build the coordinator's SQLAlchemy engine.

    ``pool_pre_ping`` recycles stale connections; the connect timeout keeps a
    dead database from hanging startup forever.
    """
    return create_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 100},
    )


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a session that commits on success, else rolls back and re-raises."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
