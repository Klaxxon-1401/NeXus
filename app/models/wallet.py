"""User wallet/balance model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from .user import User


class Wallet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single-currency wallet whose amounts are stored as integer cents.

    ``available_balance_cents`` is spendable cash. When a withdrawal is
    requested its amount moves into ``pending_withdrawal_cents`` immediately,
    preventing the same funds being spent twice while operations review it.
    """

    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("available_balance_cents >= 0", name="wallet_available_nonnegative"),
        CheckConstraint("pending_withdrawal_cents >= 0", name="wallet_pending_nonnegative"),
        Index("ix_wallets_tenant_user", "tenant_id", "user_id", unique=True),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    available_balance_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    pending_withdrawal_cents: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="wallet")

    @property
    def total_balance_cents(self) -> int:
        return self.available_balance_cents + self.pending_withdrawal_cents

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Wallet user={self.user_id} available={self.available_balance_cents}>"
