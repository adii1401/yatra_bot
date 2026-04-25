import os
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

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

class Expense(Base):
    __tablename__ = 'expenses'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), nullable=False)
    payer_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, nullable=False)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserLocation(Base):
    __tablename__ = 'user_locations'
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), unique=True)
    name = Column(String)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TripDocument(Base):
    __tablename__ = 'trip_documents'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), nullable=False)
    uploader_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False)
    file_id = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    caption = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

class GroupMember(Base):
    __tablename__ = 'group_members'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, ForeignKey('users.telegram_id', ondelete="CASCADE"), nullable=False)

class Landmark(Base):
    __tablename__ = 'landmarks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    stay_info = Column(String)
    food_info = Column(String)
    sight_info = Column(String)

class TripPlan(Base):
    __tablename__ = 'trip_plans'
    chat_id = Column(BigInteger, ForeignKey('trip_groups.chat_id', ondelete="CASCADE"), primary_key=True)
    plan_text = Column(String, nullable=False)

# ==========================================
# CONNECTION SETUP
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "sqlite+aiosqlite:///./yatra_bot.db"
else:
    # Ensure URL is clean and uses the asyncpg driver
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# SQLite config for local testing
if DATABASE_URL.startswith("sqlite"):
    engine = create_async_engine(
        DATABASE_URL,
        echo=False
    )
# Supabase config for production on Render
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=5,
        pool_recycle=300,
        connect_args={
            "statement_cache_size": 0,  
            "timeout": 60,              
            "command_timeout": 60       
        }
    )

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)