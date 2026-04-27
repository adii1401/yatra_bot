import asyncio
import os
import sys
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from alembic import context

# Add your project root to the path so it can find your bot module
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# Import your database config
from bot.database.db_config import Base, DATABASE_URL

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 🚨 Dynamically set the database URL from your db_config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Synchronous helper for the async context."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # 🚨 FIX: Supabase Cold-Start Retry Loop for Migrations
    for attempt in range(3):
        try:
            async with connectable.connect() as connection:
                # Run the synchronous migrations inside an async context wrapper
                await connection.run_sync(do_run_migrations)
            
            # If we reach here, migrations succeeded! Break the loop.
            break 
            
        except Exception as e:
            is_timeout = isinstance(e, (TimeoutError, asyncio.CancelledError)) or "timeout" in str(e).lower()
            if is_timeout and attempt < 2:
                print(f"🔄 Supabase is waking up... Migration timeout (Attempt {attempt + 1}/3). Retrying in 3s...")
                await asyncio.sleep(3)
                continue
            
            # If we run out of retries or it's a different error, crash normally
            print(f"❌ Migration failed: {e}")
            raise

    await connectable.dispose()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # 🚨 Fire off the async migration loop
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()