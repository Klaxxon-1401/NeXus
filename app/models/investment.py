"""Investment position model."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from .user import User


class Investment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A principal amount allocated to one illustrative monthly-return plan.

    The plan's rate is copied onto the position at purchase time. This is an
    intentional snapshot: later changes to a plan's marketing rate must never
    rewrite the terms of an existing investment.
    """

    __tablename__ = "investments"
    __table_args__ = (
        CheckConstraint("principal_cents > 0", name="investment_principal_positive"),
        CheckConstraint(
            "monthly_rate_bps >= 0 AND monthly_rate_bps <= 10000",
            name="investment_rate_range",
        ),
        CheckConstraint("term_months > 0", name="investment_term_positive"),
        CheckConstraint(
            "months_credited >= 0 AND months_credited <= term_months",
            name="investment_credited_range",
        ),
        CheckConstraint(
            "status IN ('active', 'matured', 'cancelled')", name="investment_status"
        ),
        Index("ix_investments_tenant_user", "tenant_id", "user_id"),
        Index("ix_investments_tenant_next_credit", "tenant_id", "next_credit_on"),
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
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(48), nullable=False)
    principal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    monthly_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    months_credited: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    start_on: Mapped[date] = mapped_column(Date, nullable=False)
    matures_on: Mapped[date] = mapped_column(Date, nullable=False)
    # next_credit_on is advanced after each successful monthly settlement. It
    # makes catch-up jobs and missed-payout detection straightforward.
    next_credit_on: Mapped[date] = mapped_column(Date, nullable=False)

    user: Mapped["User"] = relationship()

    @property
    def monthly_return_cents(self) -> int:
        # Rounded half-up to the nearest cent. Using integer cents avoids
        # floating point drift across many small monthly postings.
        return (self.principal_cents * self.monthly_rate_bps + 5_000) // 10_000

    @property
    def monthly_rate_percent(self) -> str:
        return f"{self.monthly_rate_bps / 100:.2f}%"

    @property
    def remaining_months(self) -> int:
        return max(0, self.term_months - self.months_credited)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Investment {self.plan_code} {self.principal_cents} user={self.user_id}>"
