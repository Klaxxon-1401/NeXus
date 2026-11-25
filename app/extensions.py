"""Shared Flask extensions."""

from __future__ import annotations

import sqlite3

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .models import Base

db = SQLAlchemy(model_class=Base)
csrf = CSRFProtect()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour"],
    headers_enabled=True,
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # pragma: no cover
    """Enable SQLite integrity and concurrency settings during development."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
