"""Load metadata and credentials without storing a URL in Alembic config."""

from alembic import context
from sqlalchemy import create_engine, pool

from deposition_toolkit.ledger.models import Base
from deposition_toolkit.ledger.settings import get_ledger_settings


def run_migrations() -> None:
    url = get_ledger_settings().url
    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=Base.metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(
        url,
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
