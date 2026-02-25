"""
Auth Module — JWT + OTP Registration
=====================================
- JWT access tokens (15min) + refresh tokens (7d)
- One-time lifetime OTP activation (hashed, 5min expiry)
- Role-based access: admin vs user
- Admin auto-creation on first register
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import User, OTPCode, AuditLog, UserRole


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============================================
# PASSWORD HASHING
# ============================================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ============================================
# JWT TOKENS
# ============================================

def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ============================================
# OTP SYSTEM (One-time lifetime activation)
# ============================================

def generate_otp() -> str:
    """Generate a 6-digit OTP code."""
    return f"{secrets.randbelow(900000) + 100000}"


def hash_otp(code: str) -> str:
    """Hash OTP with SHA-256 for secure storage."""
    return hashlib.sha256(code.encode()).hexdigest()


async def create_otp(db: AsyncSession, admin_user_id: str) -> str:
    """Admin creates an OTP for new user registration. Returns plain code."""
    code = generate_otp()
    otp = OTPCode(
        code_hash=hash_otp(code),
        created_by=admin_user_id,
        expires_at=datetime.utcnow() + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
    )
    db.add(otp)
    await db.flush()
    return code


async def verify_otp(db: AsyncSession, code: str) -> bool:
    """Verify an OTP code. Returns True if valid."""
    code_hash = hash_otp(code)
    result = await db.execute(
        select(OTPCode).where(
            OTPCode.code_hash == code_hash,
            OTPCode.used == False,
            OTPCode.expires_at > datetime.utcnow(),
        )
    )
    otp = result.scalar_one_or_none()
    if otp is None:
        return False

    # Mark as used (one-time lifetime)
    otp.used = True
    return True


# ============================================
# USER MANAGEMENT
# ============================================

async def get_user_by_telegram_id(db: AsyncSession, telegram_id: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id, User.is_active == True)
    )
    return result.scalar_one_or_none()


async def get_user_count(db: AsyncSession) -> int:
    result = await db.execute(select(User))
    return len(result.scalars().all())


async def register_user(db: AsyncSession, telegram_id: str,
                        username: str = None, display_name: str = None,
                        otp_code: str = None) -> dict:
    """
    Register a new user.
    - First user becomes Admin automatically (no OTP needed).
    - Subsequent users require valid OTP from Admin.
    """
    # Check if already registered
    existing = await get_user_by_telegram_id(db, telegram_id)
    if existing:
        return {'success': False, 'error': 'Sudah terdaftar'}

    # Check max users
    user_count = await get_user_count(db)
    if user_count >= settings.MAX_USERS:
        return {'success': False, 'error': f'Maks {settings.MAX_USERS} pengguna tercapai'}

    # First user = admin (no OTP needed)
    if user_count == 0:
        user = User(
            telegram_id=telegram_id,
            username=username,
            display_name=display_name,
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(user)
        await db.flush()

        # Audit log
        db.add(AuditLog(
            user_id=user.id,
            action="auth.register_admin",
            details={"telegram_id": telegram_id},
        ))
        return {'success': True, 'role': 'admin', 'user_id': str(user.id)}

    # Subsequent users need OTP
    if not otp_code:
        return {'success': False, 'error': 'Kode OTP diperlukan. Minta admin kirim /approve'}

    otp_valid = await verify_otp(db, otp_code)
    if not otp_valid:
        return {'success': False, 'error': 'Kode OTP tidak valid atau sudah kadaluarsa'}

    user = User(
        telegram_id=telegram_id,
        username=username,
        display_name=display_name,
        role=UserRole.USER,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    # Mark OTP as used by this user
    code_hash = hash_otp(otp_code)
    result = await db.execute(
        select(OTPCode).where(OTPCode.code_hash == code_hash)
    )
    otp_record = result.scalar_one_or_none()
    if otp_record:
        otp_record.used_by_telegram_id = telegram_id

    # Audit log
    db.add(AuditLog(
        user_id=user.id,
        action="auth.register_user",
        details={"telegram_id": telegram_id, "approved_via": "otp"},
    ))

    return {'success': True, 'role': 'user', 'user_id': str(user.id)}


async def audit_action(db: AsyncSession, user_id, action: str, details: dict = None):
    """Log an audit action. Details must never contain secrets."""
    db.add(AuditLog(
        user_id=user_id,
        action=action,
        details=details,
    ))


async def update_display_name(db: AsyncSession, user_id, new_name: str) -> dict:
    """Update user's display name. Does NOT affect any other data."""
    from sqlalchemy import update
    await db.execute(
        update(User).where(User.id == user_id).values(display_name=new_name)
    )
    return {'success': True, 'name': new_name}

