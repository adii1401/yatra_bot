import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, DateTime, ForeignKey, func, UniqueConstraint, text
from sqlalchemy import pool
from dotenv import load_dotenv

load_dotenv()

# 🛠️ Database URL Normalization
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./yatra_bot.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# 🛠️ Engine Configuration with PgBouncer Fixes
engine = create_async_engine(
    DATABASE_URL,
    poolclass=pool.NullPool,  # Critical for Supabase free tier connection limits
    echo=False,
    connect_args={
        "prepared_statement_cache_size": 0,  # Fixes DuplicatePreparedStatementError on PgBouncer
        "statement_cache_size": 0            # Disables caching that conflicts with transaction pooling
    }
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

Base = declarative_base()

# ================= MODEL DEFINITIONS =================

class User(Base):
    __tablename__ = "users"
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String)
    username = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class TripGroup(Base):
    __tablename__ = "trip_groups"
    chat_id = Column(BigInteger, primary_key=True, index=True)
    trip_name = Column(String, nullable=True)
    destination_name = Column(String, nullable=True)
    dest_lat = Column(Float, nullable=True) 
    dest_lon = Column(Float, nullable=True) 
    member_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class GroupMember(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("trip_groups.chat_id"))
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"))
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (UniqueConstraint('chat_id', 'user_id', name='_chat_user_uc'),)

class UserLocation(Base):
    __tablename__ = "user_locations"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), unique=True)
    latitude = Column(Float)
    longitude = Column(Float)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("trip_groups.chat_id"))
    payer_id = Column(BigInteger, ForeignKey("users.telegram_id"))
    amount = Column(Float)
    description = Column(String)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PackingItem(Base):
    __tablename__ = "packing_items"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("trip_groups.chat_id"))
    item_name = Column(String)
    is_checked = Column(Boolean, default=False)
    checked_by = Column(String, nullable=True)

class TripPlan(Base):
    __tablename__ = "trip_plans"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("trip_groups.chat_id"))
    plan_text = Column(String)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

# 👇 THESE WERE MISSING 👇
class TripDocument(Base):
    __tablename__ = "trip_documents"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("trip_groups.chat_id"))
    uploader_id = Column(BigInteger, ForeignKey("users.telegram_id"))
    file_id = Column(String)
    file_type = Column(String)
    caption = Column(String, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

class Landmark(Base):
    __tablename__ = "landmarks"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, ForeignKey("trip_groups.chat_id"))
    name = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    notes = Column(String, nullable=True)
# 👆 ======================= 👆

# ================= DB INITIALIZATION =================

async def init_db():
    if DATABASE_URL.startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))