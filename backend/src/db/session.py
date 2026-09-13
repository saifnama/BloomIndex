"""Database session, engine configuration, and SQLite pragma hooks.

Manages the asynchronous SQLite engine using aiosqlite, sets production
pragmas on each connection, and provides async session factories and
FastAPI dependencies.
"""

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

# The database defaults to living next to the schema code
# (backend/src/db/bloomindex.sqlite) so it travels with its migrations.
# Can be overridden via the BLOOMINDEX_DB_DIR environment variable.
_DB_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.environ.get("BLOOMINDEX_DB_DIR", _DB_THIS_DIR)
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "bloomindex.sqlite")

# SQLite URL formatted for the aiosqlite async driver.
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


# --- SQLite connection pragmas ---


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record):
    """Apply high-concurrency PRAGMA settings on every new connection.

    Enables WAL mode for non-blocking reads, normal synchronous mode
    for write throughput, foreign key checks, a 5-second lock retry
    window, 20 MB page cache, and in-memory temporary tables.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA cache_size=-20000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


async def get_db():
    """FastAPI dependency yielding an async database session per request."""
    async with AsyncSessionLocal() as session:
        yield session
