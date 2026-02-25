"""
Personal Finance AI — Database Models
======================================
PostgreSQL with SQLAlchemy async.
- Row-level isolation via user_id on every table
- Indexes on user_id + date fields for query speed
- Soft-delete on transactions (is_deleted flag)
- Audit log for all critical actions
"""

import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, String, Float, Boolean, DateTime, Date, Integer,
    Text, ForeignKey, Index, BigInteger, JSON, Enum as SAEnum,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


# ============================================
# BASE
# ============================================

class Base(DeclarativeBase):
    pass


# ============================================
# ENUMS
# ============================================

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class TransactionType(str, enum.Enum):
    INCOME = "income"
    EXPENSE = "expense"


class TransactionSource(str, enum.Enum):
    MANUAL = "manual"
    OCR = "ocr"
    IMPORT = "import"


# ============================================
# MODELS
# ============================================

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id = Column(String(50), unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=True)
    display_name = Column(String(200), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    password_hash = Column(String(200), nullable=True)  # Hashed
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    transactions = relationship("Transaction", back_populates="user", lazy="selectin")
    budgets = relationship("Budget", back_populates="user", lazy="selectin")
    goals = relationship("Goal", back_populates="user", lazy="selectin")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_tx_user_date", "user_id", "transaction_date"),
        Index("ix_tx_user_category", "user_id", "category"),
        Index("ix_tx_user_deleted", "user_id", "is_deleted"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    type = Column(SAEnum(TransactionType), nullable=False)
    amount = Column(Float, nullable=False)
    category = Column(String(100), nullable=False, default="Lainnya")
    description = Column(Text, nullable=True)
    merchant = Column(String(200), nullable=True)
    transaction_date = Column(Date, default=date.today, nullable=False, index=True)
    source = Column(SAEnum(TransactionSource), default=TransactionSource.MANUAL)
    idempotency_key = Column(String(100), unique=True, nullable=True)
    is_deleted = Column(Boolean, default=False)  # Soft delete
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship
    user = relationship("User", back_populates="transactions")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budget_user_cat", "user_id", "category"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(100), nullable=False)
    monthly_limit = Column(Float, nullable=False)
    period = Column(String(20), default="monthly")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="budgets")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)
    deadline = Column(Date, nullable=True)
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="goals")


class OTPCode(Base):
    """One-time lifetime activation codes. Used once, then marked used."""
    __tablename__ = "otp_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_hash = Column(String(200), nullable=False)  # Hashed, never stored plain
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    used_by_telegram_id = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Audit trail for all critical actions."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_user_time", "user_id", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action = Column(String(100), nullable=False)  # e.g., "transaction.create", "auth.login"
    details = Column(JSON, nullable=True)  # Extra context (never secrets)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIMemory(Base):
    """Per-user conversation memory for contextual AI Q&A."""
    __tablename__ = "ai_memory"
    __table_args__ = (
        Index("ix_memory_user_time", "user_id", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================
# ENGINE + SESSION
# ============================================

_async_engine = None
_async_session_factory = None


def get_async_engine(database_url: str):
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _async_engine


def get_session_factory(engine) -> async_sessionmaker:
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    return _async_session_factory


async def get_db():
    """Dependency for FastAPI routes."""
    from app.config import settings
    engine = get_async_engine(settings.DATABASE_URL)
    factory = get_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Create all tables on startup."""
    from app.config import settings
    engine = get_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
