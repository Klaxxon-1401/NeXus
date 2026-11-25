"""Investment purchase, return settlement and portfolio projections."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from flask import current_app
from sqlalchemy import func, select

from ..constants import PLANS, Plan
from ..extensions import db
from ..models import Investment, Transaction, User
from ..utils import add_months, utc_today, utcnow
from . import ledger
from .errors import ServiceError
from .referrals import pay_commissions_for_investment


@dataclass(slots=True)
class SettlementResult:
    credited_count: int
    credited_cents: int
    investments_matured: int


@dataclass(slots=True)
class ScheduleItem:
    investment: Investment
    month_number: int
    due_on: date
    amount_cents: int
    paid: bool


def investment_schedule(investment: Investment, *, include_paid: bool = False) -> list[ScheduleItem]:
    """Return the contractual monthly schedule for an investment.

    ``months_credited`` is the ledger's materialised cursor. A schedule item is
    marked paid when its month index is below that cursor.
    """
    items: list[ScheduleItem] = []
    for index in range(investment.term_months):
        paid = index < investment.months_credited
        if not include_paid and paid:
            continue
        due_on = add_months(investment.start_on, index + 1)
        items.append(
            ScheduleItem(
                investment=investment,
                month_number=index + 1,
                due_on=due_on,
                amount_cents=investment.monthly_return_cents,
                paid=paid,
            )
        )
    return items


def upcoming_installments(
    investments: Iterable[Investment], *, limit: int = 6
) -> list[ScheduleItem]:
    due_items: list[ScheduleItem] = []
    for investment in investments:
        if investment.status != "active":
            continue
        due_items.extend(investment_schedule(investment))
    due_items.sort(key=lambda item: (item.due_on, item.investment.id))
    return due_items[:limit]


def projection_series(
    investments: Iterable[Investment], *, months: int = 12
) -> list[dict[str, int | str]]:
    """Project scheduled payouts into calendar-month buckets for the chart."""
    today = utc_today()
    buckets: dict[tuple[int, int], int] = defaultdict(int)
    start = date(today.year, today.month, 1)
    month_sequence = [add_months(start, offset) for offset in range(months)]

    for investment in investments:
        if investment.status != "active":
            continue
        for item in investment_schedule(investment):
            if item.due_on < today:
                continue
            if item.due_on >= add_months(start, months):
                continue
            buckets[(item.due_on.year, item.due_on.month)] += item.amount_cents

    return [
        {
            "month": month.strftime("%b"),
            "year": month.year,
            "label": month.strftime("%b %Y"),
            "amount_cents": buckets.get((month.year, month.month), 0),
        }
        for month in month_sequence
    ]


def get_user_investments(user: User) -> list[Investment]:
    return list(
        db.session.scalars(
            select(Investment)
            .where(
                Investment.tenant_id == user.tenant_id,
                Investment.user_id == user.id,
            )
            .order_by(Investment.created_at.desc())
        ).all()
    )


def _plan_for_code(plan_code: str) -> Plan:
    plan = PLANS.get(plan_code)
    if plan is None:
        raise ServiceError("Choose one of the available plans.")
    return plan


def create_investment(*, user: User, plan_code: str, amount_cents: int) -> Investment:
    """Debit wallet cash, open a position and distribute referral commissions.

    The investment debit and all commissions are one database transaction.
    Either the position and every associated ledger entry exist, or none do.
    """
    plan = _plan_for_code(plan_code)
    if amount_cents < plan.min_cents:
        raise ServiceError(
            f"{plan.name} requires at least ${plan.min_cents / 100:,.0f}."
        )
    if amount_cents > 5_000_000_00:  # $5m prototype guard rail
        raise ServiceError("Amount exceeds the prototype position limit.")

    wallet = ledger.get_wallet_for_update(user_id=user.id, tenant_id=user.tenant_id)
    if wallet.available_balance_cents < amount_cents:
        raise ServiceError("Add funds to your wallet before investing.")

    today = utc_today()
    investment = Investment(
        tenant_id=user.tenant_id,
        user_id=user.id,
        plan_code=plan.code,
        plan_name=plan.name,
        principal_cents=amount_cents,
        monthly_rate_bps=plan.monthly_rate_bps,
        term_months=plan.term_months,
        status="active",
        start_on=today,
        matures_on=add_months(today, plan.term_months),
        # First payout is one month after opening; each settlement advances
        # this cursor without drifting if a job runs late.
        next_credit_on=add_months(today, 1),
    )
    db.session.add(investment)
    db.session.flush()

    ledger.debit(
        wallet=wallet,
        amount_cents=amount_cents,
        kind="investment",
        description=f"{plan.name} allocation",
        investment_id=investment.id,
        details={"plan_code": plan.code, "term_months": plan.term_months},
    )

    pay_commissions_for_investment(user, investment)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return investment


def _settle_one(investment: Investment) -> Transaction | None:
    """Credit exactly one scheduled month. Caller owns the transaction."""
    if investment.status != "active":
        return None
    if investment.months_credited >= investment.term_months:
        investment.status = "matured"
        return None

    next_month_number = investment.months_credited + 1
    idempotency_key = f"return:{investment.id}:{next_month_number}"
    existing = ledger.find_by_idempotency_key(
        tenant_id=investment.tenant_id, key=idempotency_key
    )
    if existing is not None:
        raise ServiceError("This return period has already been settled.")

    amount_cents = investment.monthly_return_cents
    if amount_cents <= 0:
        raise ServiceError("This investment's return amount is not positive.")

    wallet = ledger.get_wallet_for_update(
        user_id=investment.user_id, tenant_id=investment.tenant_id
    )
    scheduled_on = investment.next_credit_on
    transaction = ledger.credit(
        wallet=wallet,
        amount_cents=amount_cents,
        kind="return",
        description=(
            f"{investment.plan_name} return · month {next_month_number} "
            f"of {investment.term_months}"
        ),
        investment_id=investment.id,
        details={
            "month_number": next_month_number,
            "scheduled_on": scheduled_on.isoformat(),
            "principal_cents": investment.principal_cents,
            "rate_bps": investment.monthly_rate_bps,
        },
        idempotency_key=idempotency_key,
        settled_at=utcnow(),
    )
    # Advance the materialised schedule cursor. Idempotency keys prevent a
    # retried job from paying the same month twice.
    investment.months_credited += 1
    investment.next_credit_on = add_months(investment.next_credit_on, 1)
    if investment.months_credited >= investment.term_months:
        investment.status = "matured"
    db.session.flush()
    return transaction


def credit_due_returns(
    *,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    as_of: date | None = None,
) -> SettlementResult:
    """Catch up all instalments due on or before ``as_of``.

    This is the function a production scheduler would call for every tenant.
    It is idempotent at the payment level via the return idempotency key.
    """
    as_of = as_of or utc_today()
    query = select(Investment).where(
        Investment.status == "active",
        Investment.next_credit_on <= as_of,
    )
    if tenant_id is not None:
        query = query.where(Investment.tenant_id == tenant_id)
    if user_id is not None:
        query = query.where(Investment.user_id == user_id)
    query = query.order_by(Investment.next_credit_on).with_for_update()

    investments = db.session.scalars(query).all()
    credited_count = 0
    credited_cents = 0
    matured = 0

    for investment in investments:
        while (
            investment.status == "active"
            and investment.next_credit_on <= as_of
            and investment.months_credited < investment.term_months
        ):
            was_active = investment.status == "active"
            transaction = _settle_one(investment)
            if transaction is None:
                break
            credited_count += 1
            credited_cents += transaction.amount_cents
            if was_active and investment.status == "matured":
                matured += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return SettlementResult(
        credited_count=credited_count,
        credited_cents=credited_cents,
        investments_matured=matured,
    )


def demo_simulate_next_return(*, user: User, investment_id: uuid.UUID) -> Transaction:
    """Credit one upcoming month early for demonstration purposes.

    Guarded by DEMO_MODE at the route and service layer. Production payouts
    must be driven by the scheduler using ``credit_due_returns`` instead.
    """
    if not current_app.config.get("DEMO_MODE"):
        raise ServiceError("Return simulation is disabled.", code="demo_disabled")

    investment = db.session.scalar(
        select(Investment)
        .where(
            Investment.id == investment_id,
            Investment.tenant_id == user.tenant_id,
            Investment.user_id == user.id,
        )
        .with_for_update()
    )
    if investment is None:
        raise ServiceError("Investment not found.", code="not_found")
    if investment.status != "active":
        raise ServiceError("This investment has already matured.")

    transaction = _settle_one(investment)
    if transaction is None:
        raise ServiceError("No return is available to settle.")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return transaction


def portfolio_summary(user: User) -> dict:
    investments = get_user_investments(user)
    active = [inv for inv in investments if inv.status == "active"]
    active_principal = sum(inv.principal_cents for inv in active)

    returns_earned = db.session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_cents), 0)).where(
            Transaction.tenant_id == user.tenant_id,
            Transaction.user_id == user.id,
            Transaction.kind == "return",
            Transaction.status == "completed",
        )
    )
    returns_earned = int(returns_earned or 0)
    monthly_income = sum(inv.monthly_return_cents for inv in active)

    allocation_rows: dict[str, dict] = {}
    for investment in active:
        entry = allocation_rows.setdefault(
            investment.plan_code,
            {
                "code": investment.plan_code,
                "name": investment.plan_name,
                "amount_cents": 0,
            },
        )
        entry["amount_cents"] += investment.principal_cents
    allocations = sorted(
        allocation_rows.values(), key=lambda item: item["amount_cents"], reverse=True
    )
    for allocation in allocations:
        allocation["percent"] = (
            round(allocation["amount_cents"] / active_principal * 100)
            if active_principal
            else 0
        )

    projection = projection_series(active)
    return {
        "investments": investments,
        "active": active,
        "active_principal_cents": active_principal,
        "returns_earned_cents": returns_earned,
        "monthly_income_cents": monthly_income,
        "allocations": allocations,
        "next_installments": upcoming_installments(active, limit=6),
        "projection": projection,
        "projection_max_cents": max((item["amount_cents"] for item in projection), default=0),
        "active_count": len(active),
    }
