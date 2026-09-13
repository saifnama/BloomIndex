"""Alembic migration runtime configuration for SQLite.

Configures offline and async online schema migration runners using the
canonical database path from session configuration.
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Add repository root to sys.path so backend modules can be imported.
sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")),
)

from backend.src.db import models  # noqa: E402,F401
from backend.src.db.session import DB_PATH, Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Use canonical database path defined in database session configuration.
DB_FILE = DB_PATH


def run_migrations_offline() -> None:
    """Run migrations in offline mode, outputting SQL scripts."""
    # Enable batch mode for table alteration support in SQLite.
    context.configure(
        url=f"sqlite:///{DB_FILE}",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Apply migrations synchronously within an active connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in online mode using an async SQLite engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = f"sqlite+aiosqlite:///{DB_FILE}"
    # Prevent SQLite file lock contention during schema migrations.
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
