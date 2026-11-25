"""Application configuration.

Values are read from environment variables so the same code can run locally
against SQLite and in production against PostgreSQL behind a TLS proxy.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_uri() -> str:
    """Return a SQLAlchemy URI, normalising Heroku-style Postgres URLs."""
    uri = os.getenv("DATABASE_URL", "").strip()
    if not uri:
        return f"sqlite:///{BASE_DIR / 'instance' / 'sigma.db'}"
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return uri


load_dotenv(BASE_DIR / ".env")


class Config:
    # ---------------------------------------------------------------- core
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-this-secret-key")
    APP_NAME = "Sigma Investments"
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "desk@sigma.example")
    ENV_NAME = os.getenv("APP_ENV", "development").lower()
    DEBUG = False
    TESTING = False

    # ------------------------------------------------------------ database
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }

    # ------------------------------------------------------------- session
    SESSION_COOKIE_NAME = "sigma_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.getenv("SESSION_HOURS", "8")))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)

    # --------------------------------------------------------------- forms
    WTF_CSRF_ENABLED = True
    # Flask-WTF expects this value in seconds and passes it to itsdangerous
    # as ``max_age``. A timedelta here fails at request time on modern
    # itsdangerous releases, so keep it an integer.
    WTF_CSRF_TIME_LIMIT = 4 * 60 * 60
    MAX_CONTENT_LENGTH = 256 * 1024  # No uploads; keep request bodies small.

    # ------------------------------------------------------------ security
    TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)
    FORCE_HTTPS = _env_bool("FORCE_HTTPS", False)
    RATELIMIT_ENABLED = _env_bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "300 per hour")
    MIN_PASSWORD_LENGTH = 12

    # ---------------------------------------------------------- demo / ops
    # Destructive simulation endpoints are available only while this is true.
    DEMO_MODE = _env_bool("DEMO_MODE", True)
    DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "SigmaDemo!2026")
    DEPOSIT_MIN_CENTS = 2_500          # $25.00
    DEPOSIT_MAX_CENTS = 5_000_000      # $50,000.00
    WITHDRAWAL_MIN_CENTS = 2_000       # $20.00
    WITHDRAWAL_MAX_CENTS = 5_000_000   # $50,000.00

    # Simulated treasury addresses. A real implementation would receive these
    # from a custody / payment provider and never from application config.
    DEPOSIT_ADDRESSES = {
        "ethereum": os.getenv(
            "DEPOSIT_ADDRESS_ETHEREUM",
            "0x71C9A0d5D6c4FfB2B5e7aE4f2D8e3C7b9A1d6F20",
        ),
        "bitcoin": os.getenv(
            "DEPOSIT_ADDRESS_BITCOIN",
            "bc1qsigma0demo0address0only0000000000000000",
        ),
        "solana": os.getenv(
            "DEPOSIT_ADDRESS_SOLANA",
            "SigmaDemo1111111111111111111111111111111111",
        ),
    }

    # Page size for ledger views.
    TRANSACTIONS_PER_PAGE = 12


class DevelopmentConfig(Config):
    ENV_NAME = "development"
    DEBUG = True
    DEMO_MODE = _env_bool("DEMO_MODE", True)
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)


class TestingConfig(Config):
    ENV_NAME = "testing"
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    DEMO_MODE = True
    SECRET_KEY = "test-secret-key-with-sufficient-length"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ProductionConfig(Config):
    ENV_NAME = "production"
    DEBUG = False
    DEMO_MODE = False
    SESSION_COOKIE_SECURE = True
    TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", True)
    FORCE_HTTPS = _env_bool("FORCE_HTTPS", True)

    @classmethod
    def validate(cls) -> None:
        """Fail closed when production-critical settings are missing."""
        problems: list[str] = []
        secret = os.getenv("SECRET_KEY", "")
        if len(secret) < 32:
            problems.append("SECRET_KEY must be set to at least 32 characters")
        if not os.getenv("DATABASE_URL", "").strip():
            problems.append("DATABASE_URL must point at PostgreSQL")
        if os.getenv("DEMO_MODE", "false").lower() in {"1", "true", "yes"}:
            problems.append("DEMO_MODE must be disabled in production")
        if not _env_bool("TRUST_PROXY_HEADERS", True):
            problems.append("TRUST_PROXY_HEADERS must be enabled behind a TLS proxy")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
