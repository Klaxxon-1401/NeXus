"""Append-oriented transaction ledger model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from .investment import Investment
    from .user import User


def new_reference() -> str:
    return f"TX-{uuid.uuid4().hex[:12].upper()}"


class Transaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An auditable movement or request.

    Amounts are always positive; ``direction`` gives the sign. Completed rows
    snapshot the wallet's available balance after the mutation so statements
    can be reconstructed and reconciled later.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="transaction_amount_positive"),
        CheckConstraint("direction IN ('credit', 'debit')", name="transaction_direction"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'rejected', 'failed')",
            name="transaction_status",
        ),
        CheckConstraint(
            "kind IN ('deposit','withdrawal','withdrawal_refund','investment','return','referral')",
            name="transaction_kind",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_transaction_tenant_idempotency"),
        Index("ix_transactions_tenant_user_created", "tenant_id", "user_id", "created_at"),
        Index("ix_transactions_tenant_kind_status", "tenant_id", "kind", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    investment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("investments.id", ondelete="SET NULL"),
        nullable=True,
    )
    reference: Mapped[str] = mapped_column(
        String(24), unique=True, nullable=False, default=new_reference
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="completed", nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Gateway metadata (network, source address, payout id, level, etc.).
    # Never store secrets, private keys or full card numbers here.
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship()
    investment: Mapped["Investment | None"] = relationship()

    @property
    def signed_amount_cents(self) -> int:
        return self.amount_cents if self.direction == "credit" else -self.amount_cents

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Transaction {self.reference} {self.kind} {self.status}>"
