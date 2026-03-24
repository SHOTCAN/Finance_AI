"""
Personal Finance AI — Configuration
====================================
All settings from environment variables. Secrets never logged.
"""

import os
from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- App ---
    APP_NAME: str = "Personal Finance AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    PORT: int = 8000

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/finance_ai"
    DATABASE_URL_SYNC: str = "postgresql://postgres:password@localhost:5432/finance_ai"

    # --- Auth ---
    JWT_SECRET: str = "change-me-in-production-use-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_EXPIRE_DAYS: int = 7
    OTP_EXPIRE_MINUTES: int = 5
    MAX_USERS: int = 6  # Minimum 6 users

    # --- Telegram ---
    TELEGRAM_TOKEN: str = ""
    ADMIN_TELEGRAM_ID: str = ""  # First registered user becomes admin

    # --- Groq API (5 keys rotation) ---
    GROQ_API_KEY_1: str = ""
    GROQ_API_KEY_2: str = ""
    GROQ_API_KEY_3: str = ""
    GROQ_API_KEY_4: str = ""
    GROQ_API_KEY_5: str = ""

    # --- Google Cloud ---
    GOOGLE_SERVICE_ACCOUNT_FILE: str = "ai-keuangan-488515-f0b54598d94a.json"
    GOOGLE_SERVICE_ACCOUNT_JSON: str = ""  # JSON string for cloud deploy (if file not available)
    GOOGLE_SHEETS_ENABLED: bool = True

    # --- Rate Limiting ---
    RATE_LIMIT_PER_MINUTE: int = 60

    @property
    def groq_keys(self) -> List[str]:
        """Collect all non-empty Groq API keys."""
        keys = [
            self.GROQ_API_KEY_1,
            self.GROQ_API_KEY_2,
            self.GROQ_API_KEY_3,
            self.GROQ_API_KEY_4,
            self.GROQ_API_KEY_5,
        ]
        return [k for k in keys if k]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
# Force minimum 6 users regardless of what Pydantic loaded from the environment variables
if getattr(settings, 'MAX_USERS', 0) < 6:
    settings.MAX_USERS = 6
