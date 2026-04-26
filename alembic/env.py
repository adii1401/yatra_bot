import asyncio
import os
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool
from alembic import context
from dotenv import load_dotenv

load_dotenv()

# Import your models so Alembic can see them
from bot.database.db_config import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """
    Always read from environment.
    Never use the hardcoded SQLite URL from alembic.ini in production.
    """
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./yatra_bot.db")
    # Normalize to asyncpg for Postgres
    url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    """Run migrations without a DB connection (generates SQL script only)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,      # detects column type changes
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    connectable = create_async_engine(
        get_url(),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())