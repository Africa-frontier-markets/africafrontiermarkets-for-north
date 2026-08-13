"""Alembic environment configuration."""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from config.database import Base
from config.config import get_settings

settings = get_settings()
"""
AFM Alembic environment.

Pulls DATABASE_URL from the same Settings object the app uses
(config.config.get_settings), already normalized for Render-style
postgres:// URLs via common/db_url.py — so migrations always target the
same database the app connects to, with no separate URL to keep in sync.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import Base + all models so their tables register on Base.metadata
from config.database import Base
from config.config import get_settings
from payment_hub import models as payment_models  # noqa: F401  (registers Transaction/User)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override with actual DB URL
config.set_main_option("sqlalchemy.url", settings.database_url.replace("+asyncpg", ""))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
target_metadata = Base.metadata

settings = get_settings()
# Alembic's own async pool should be tiny — this connection lives only for
# the duration of the migration, not the app's request traffic.
config.set_main_option("sqlalchemy.url", settings.resolved_database_url)


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (`alembic upgrade head --sql`)."""
    url = settings.resolved_database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()
def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connect_args = {"ssl": True} if settings.db_ssl_required else {}
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
    asyncio.run(run_migrations_online())
