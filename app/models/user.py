"""User identity and authentication model."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from .tenant import Tenant
    from .wallet import Wallet

REFERRAL_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_referral_code(length: int = 8) -> str:
    """Human-readable, non-ambiguous invite code (e.g. ``K7M4PZQX``)."""
    return "".join(secrets.choice(REFERRAL_CODE_ALPHABET) for _ in range(length))


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'investor')", name="user_role"
        ),
        CheckConstraint(
            "status IN ('active', 'suspended')", name="user_status"
        ),
        Index("ix_users_tenant_created", "tenant_id", "created_at"),
    )

    # Email is globally unique because sign-in happens before tenant selection.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="investor", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    referral_code: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, default=generate_referral_code
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    wallet: Mapped["Wallet"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    # -- password helpers ---------------------------------------------------
    def set_password(self, password: str) -> None:
        # Werkzeug's scrypt implementation is memory-hard and stores its
        # parameters in the encoded hash, allowing future upgrades.
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def first_name(self) -> str:
        return self.full_name.split(" ", 1)[0]

    @property
    def initials(self) -> str:
        parts = [part for part in self.full_name.split() if part]
        return "".join(part[0].upper() for part in parts[:2])

    @property
    def private_name(self) -> str:
        """A reduced name for referral trees, where exact identity is unnecessary."""
        parts = self.full_name.split()
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0]} {parts[-1][0]}."

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email}>"
