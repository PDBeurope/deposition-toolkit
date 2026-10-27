"""Lazy engine creation and short, explicit transaction boundaries."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from .settings import get_ledger_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create the pool on first use; connect only when executing database work."""
    return create_engine(
        get_ledger_settings().url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on failure, and always release the session.

    Use inside ledger operations. Return validated data, not attached ORM objects,
    and finish the transaction before running a stage or other lengthy work.
    """
    with Session(get_engine()) as session, session.begin():
        yield session


def check_connection() -> None:
    """Verify connectivity without creating tables or changing application data."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
