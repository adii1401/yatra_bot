import os
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

Base = declarative_base()

# ==========================================
# TABLES - SCHEMA DEFINITION
# ==========================================

class User(Base):
    __tablename__ = 'users'
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, nullable=False)
    username = Column(String, nullable=True)
    upi_id = Column(String, nullable=True)

class TripGroup(Base):
    __tablename__ = 'trip_groups'
    chat_id = Column(BigInteger, primary_key=True, index=True)
    trip_name = Column(String, default="New Adventure")
    destination_name = Column(String, nullable=True)
    gallery_link = Column(String, nullable=True)
    dest_lat = Column(Float, nullable=True)
    dest_lon = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class GroupMember(Base):
    __tablename__ = 'group_members'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False)
    
    # 🛠️ CONSTRAINT: Essential for on_conflict_do_nothing logic
    __table_args__ = (UniqueConstraint('chat_id', 'user_id', name='_chat_user_uc'),)

class UserLocation(Base):
    __tablename__ = 'user_locations'
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), unique=True)
    name = Column(String)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), nullable=False)
    payer_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, nullable=False)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# ==========================================
# CONNECTION SETUP
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "sqlite+aiosqlite:///./yatra_bot.db"
else:
    # Standardize to asyncpg
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_async_engine(DATABASE_URL, echo=False)
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=NullPool,  # Best for Supavisor Transaction mode
        connect_args={
            "statement_cache_size": 0,
            "timeout": 60,
            "command_timeout": 60,
        }
    )
    # 🛠️ THE CRITICAL FIX: Hardcode version on the SYNC engine to stop probe crashes
    engine.sync_engine.dialect.server_version_info = (15, 0)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    """Initializes tables and applies Autocommit for initial schema creation."""
    async with engine.begin() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.run_sync(Base.metadata.create_all)