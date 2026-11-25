"""Tenant (client workspace) model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from .user import User


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A client workspace.

    Every investor, balance, investment, transaction and referral belongs to
    exactly one tenant. A user invited through a referral joins the inviter's
    workspace; self-service registration creates a new workspace.
    """

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="tenant_status"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(90), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tenant {self.slug}>"
