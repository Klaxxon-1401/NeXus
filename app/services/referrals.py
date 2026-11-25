"""Referral graph traversal and commission distribution."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from ..constants import REFERRAL_RATES_BPS
from ..extensions import db
from ..models import (
    Investment,
    Referral,
    ReferralCommission,
    Transaction,
    User,
)
from ..utils import basis_points
from . import ledger
from .errors import ServiceError


@dataclass(slots=True)
class NetworkMember:
    user: User
    level: int
    joined_at: datetime
    portfolio_cents: int = 0
    earned_from_member_cents: int = 0


@dataclass(slots=True)
class ReferralOverview:
    total_earned_cents: int = 0
    downline_count: int = 0
    direct_count: int = 0
    max_depth: int = 0
    by_level: list[dict] = field(default_factory=list)
    members: list[NetworkMember] = field(default_factory=list)
    recent_commissions: list[Transaction] = field(default_factory=list)


def _direct_referral(investor_id: uuid.UUID, tenant_id: uuid.UUID) -> Referral | None:
    return db.session.scalar(
        select(Referral).where(
            Referral.referred_user_id == investor_id,
            Referral.tenant_id == tenant_id,
            Referral.status == "active",
        )
    )


def upline_chain(investor: User, *, max_levels: int = 5) -> list[tuple[int, User]]:
    """Walk direct referral edges upward, returning at most five beneficiaries.

    Cycle protection is defensive: database constraints prevent self-referral,
    but a corrupt import must never loop forever or pay the same person twice.
    """
    chain: list[tuple[int, User]] = []
    seen: set[uuid.UUID] = {investor.id}
    current = investor

    for level in range(1, max_levels + 1):
        referral = _direct_referral(current.id, investor.tenant_id)
        if referral is None:
            break
        beneficiary = db.session.scalar(
            select(User).where(
                User.id == referral.referrer_id,
                User.tenant_id == investor.tenant_id,
                User.status == "active",
            )
        )
        if beneficiary is None or beneficiary.id in seen:
            break
        chain.append((level, beneficiary))
        seen.add(beneficiary.id)
        current = beneficiary
    return chain


def pay_commissions_for_investment(investor: User, investment: Investment) -> list[ReferralCommission]:
    """Calculate and pay level 1–5 commissions for one new investment.

    Formula (integer cents, no floats)::

        commission = round_half_up(principal_cents × rate_bps / 10_000)

    For a $10,000 investment the payouts are $225.00, $125.00, $75.00,
    $50.00 and $25.00 for levels one through five. Commissions in this
    prototype are platform-funded and do not reduce the investor's principal.
    The parent investment transaction and every payout are committed together
    by the investment service.
    """
    records: list[ReferralCommission] = []

    for level, beneficiary in upline_chain(investor):
        rate_bps = REFERRAL_RATES_BPS[level - 1]
        amount_cents = basis_points(investment.principal_cents, rate_bps)
        if amount_cents <= 0:
            continue

        idempotency_key = f"referral:{investment.id}:{level}:{beneficiary.id}"
        existing = ledger.find_by_idempotency_key(
            tenant_id=investor.tenant_id, key=idempotency_key
        )
        if existing is not None:
            continue

        wallet = ledger.get_wallet_for_update(
            user_id=beneficiary.id, tenant_id=investor.tenant_id
        )
        transaction = ledger.credit(
            wallet=wallet,
            amount_cents=amount_cents,
            kind="referral",
            description=f"Level {level} commission from {investor.private_name}",
            investment_id=investment.id,
            details={
                "level": level,
                "rate_bps": rate_bps,
                "source_user_id": str(investor.id),
                "investment_principal_cents": investment.principal_cents,
            },
            idempotency_key=idempotency_key,
        )
        commission = ReferralCommission(
            tenant_id=investor.tenant_id,
            beneficiary_id=beneficiary.id,
            source_user_id=investor.id,
            investment_id=investment.id,
            transaction_id=transaction.id,
            level=level,
            rate_bps=rate_bps,
            amount_cents=amount_cents,
        )
        db.session.add(commission)
        records.append(commission)

    return records


def get_referral_overview(user: User, *, max_levels: int = 5) -> ReferralOverview:
    """Build the current user's five-level downline and earnings summary."""
    all_referrals = db.session.scalars(
        select(Referral).where(
            Referral.tenant_id == user.tenant_id,
            Referral.status == "active",
        )
    ).all()

    children: dict[uuid.UUID, list[Referral]] = defaultdict(list)
    for referral in all_referrals:
        children[referral.referrer_id].append(referral)

    # Breadth-first traversal with a visited set caps cycles and repeated IDs.
    discovered: list[tuple[uuid.UUID, int, datetime]] = []
    visited: set[uuid.UUID] = {user.id}
    frontier: list[tuple[uuid.UUID, int]] = [(user.id, 0)]
    while frontier:
        parent_id, depth = frontier.pop(0)
        if depth >= max_levels:
            continue
        for referral in children.get(parent_id, []):
            child_id = referral.referred_user_id
            if child_id in visited:
                continue
            visited.add(child_id)
            discovered.append((child_id, depth + 1, referral.created_at))
            frontier.append((child_id, depth + 1))

    member_ids = [member_id for member_id, _, _ in discovered]
    users: dict[uuid.UUID, User] = {}
    portfolio_by_user: dict[uuid.UUID, int] = {}
    earned_by_source: dict[uuid.UUID, int] = {}

    if member_ids:
        user_rows = db.session.scalars(
            select(User).where(
                User.id.in_(member_ids),
                User.tenant_id == user.tenant_id,
            )
        ).all()
        users = {row.id: row for row in user_rows}

        portfolio_rows = db.session.execute(
            select(Investment.user_id, func.coalesce(func.sum(Investment.principal_cents), 0))
            .where(
                Investment.tenant_id == user.tenant_id,
                Investment.user_id.in_(member_ids),
                Investment.status.in_(("active", "matured")),
            )
            .group_by(Investment.user_id)
        ).all()
        portfolio_by_user = {row[0]: int(row[1]) for row in portfolio_rows}

        commission_rows = db.session.execute(
            select(
                ReferralCommission.source_user_id,
                func.coalesce(func.sum(ReferralCommission.amount_cents), 0),
            )
            .where(
                ReferralCommission.tenant_id == user.tenant_id,
                ReferralCommission.beneficiary_id == user.id,
                ReferralCommission.source_user_id.in_(member_ids),
            )
            .group_by(ReferralCommission.source_user_id)
        ).all()
        earned_by_source = {row[0]: int(row[1]) for row in commission_rows}

    members: list[NetworkMember] = []
    for member_id, level, joined_at in discovered:
        member_user = users.get(member_id)
        if member_user is None:
            continue
        members.append(
            NetworkMember(
                user=member_user,
                level=level,
                joined_at=joined_at,
                portfolio_cents=portfolio_by_user.get(member_id, 0),
                earned_from_member_cents=earned_by_source.get(member_id, 0),
            )
        )
    members.sort(key=lambda member: (member.level, member.joined_at))

    earnings_rows = db.session.execute(
        select(
            ReferralCommission.level,
            func.coalesce(func.sum(ReferralCommission.amount_cents), 0),
        )
        .where(
            ReferralCommission.tenant_id == user.tenant_id,
            ReferralCommission.beneficiary_id == user.id,
        )
        .group_by(ReferralCommission.level)
    ).all()
    earnings_by_level = {int(row[0]): int(row[1]) for row in earnings_rows}
    count_by_level = {
        level: sum(1 for member in members if member.level == level)
        for level in range(1, max_levels + 1)
    }

    by_level = [
        {
            "level": level,
            "rate_bps": REFERRAL_RATES_BPS[level - 1],
            "rate_percent": f"{REFERRAL_RATES_BPS[level - 1] / 100:.2f}%",
            "members": count_by_level[level],
            "earnings_cents": earnings_by_level.get(level, 0),
        }
        for level in range(1, max_levels + 1)
    ]

    recent_commissions = db.session.scalars(
        select(Transaction)
        .where(
            Transaction.tenant_id == user.tenant_id,
            Transaction.user_id == user.id,
            Transaction.kind == "referral",
            Transaction.status == "completed",
        )
        .order_by(Transaction.created_at.desc())
        .limit(5)
    ).all()

    return ReferralOverview(
        total_earned_cents=sum(earnings_by_level.values()),
        downline_count=len(members),
        direct_count=count_by_level[1],
        max_depth=max((member.level for member in members), default=0),
        by_level=by_level,
        members=members,
        recent_commissions=list(recent_commissions),
    )


def total_referral_earnings(user: User) -> int:
    total = db.session.scalar(
        select(func.coalesce(func.sum(ReferralCommission.amount_cents), 0)).where(
            ReferralCommission.tenant_id == user.tenant_id,
            ReferralCommission.beneficiary_id == user.id,
        )
    )
    return int(total or 0)
