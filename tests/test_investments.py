"""Investment returns and five-level referral commission tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.constants import REFERRAL_RATES_BPS
from app.extensions import db
from app.models import ReferralCommission, Transaction, User
from app.services.errors import ServiceError
from app.services.investments import (
    credit_due_returns,
    create_investment,
    demo_simulate_next_return,
    portfolio_summary,
)
from app.services.referrals import (
    get_referral_overview,
    pay_commissions_for_investment,
    upline_chain,
)
from app.services.wallet import wallet_totals
from app.utils import add_months, utc_today

from .conftest import fund_wallet, make_user


def _chain(depth: int) -> list[User]:
    """Create a referral chain from a root down to ``depth`` users."""
    users = [
        make_user(
            email="root@example.com",
            full_name="Root Person",
            workspace_name="Chain House",
        )
    ]
    for index in range(1, depth + 1):
        users.append(
            make_user(
                email=f"member{index}@example.com",
                full_name=f"Member {index}",
                referrer=users[-1],
            )
        )
    return users


def test_investment_debits_wallet_and_snapshots_terms(app):
    user = make_user(email="invest@example.com", workspace_name="Invest House")
    fund_wallet(user, 300_000)
    investment = create_investment(
        user=user, plan_code="meridian", amount_cents=250_000
    )
    assert investment.principal_cents == 250_000
    assert investment.monthly_rate_bps == 125
    assert investment.term_months == 12
    assert investment.months_credited == 0
    assert investment.next_credit_on == add_months(utc_today(), 1)
    assert investment.monthly_return_cents == 3_125
    assert wallet_totals(user)["available_cents"] == 50_000

    with pytest.raises(ServiceError):
        create_investment(user=user, plan_code="meridian", amount_cents=250_000)

    with pytest.raises(ServiceError):
        create_investment(user=user, plan_code="unknown", amount_cents=100_000)


def test_five_level_commission_distribution_and_cap(app):
    # root -> A -> B -> C -> D -> E -> investor
    users = _chain(6)
    root, a, b, c, d, e, investor = users
    fund_wallet(investor, 1_000_000)
    investment = create_investment(
        user=investor, plan_code="meridian", amount_cents=1_000_000
    )

    commissions = db.session.scalars(
        select(ReferralCommission)
        .where(ReferralCommission.investment_id == investment.id)
        .order_by(ReferralCommission.level)
    ).all()
    assert len(commissions) == 5
    assert [row.level for row in commissions] == [1, 2, 3, 4, 5]
    assert [row.rate_bps for row in commissions] == list(REFERRAL_RATES_BPS)
    assert [row.amount_cents for row in commissions] == [
        22_500,
        12_500,
        7_500,
        5_000,
        2_500,
    ]
    assert [row.beneficiary_id for row in commissions] == [
        e.id,
        d.id,
        c.id,
        b.id,
        a.id,
    ]

    chain = [beneficiary.id for _, beneficiary in upline_chain(investor)]
    assert chain == [e.id, d.id, c.id, b.id, a.id]
    assert root.id not in chain

    assert wallet_totals(e)["available_cents"] == 22_500
    assert wallet_totals(d)["available_cents"] == 12_500
    assert wallet_totals(c)["available_cents"] == 7_500
    assert wallet_totals(b)["available_cents"] == 5_000
    assert wallet_totals(a)["available_cents"] == 2_500
    assert wallet_totals(root)["available_cents"] == 0

    overview = get_referral_overview(a)
    assert overview.downline_count == 5
    assert overview.direct_count == 1
    assert overview.max_depth == 5
    assert overview.total_earned_cents == 2_500

    root_overview = get_referral_overview(root)
    assert root_overview.max_depth == 5
    assert root_overview.downline_count == 5


def test_commission_payout_is_idempotent(app):
    users = _chain(2)
    _, a, b = users
    fund_wallet(b, 500_000)
    investment = create_investment(
        user=b, plan_code="meridian", amount_cents=500_000
    )
    assert db.session.scalar(select(func.count(ReferralCommission.id))) == 2
    a_before = wallet_totals(a)["available_cents"]

    records = pay_commissions_for_investment(b, investment)
    assert records == []
    assert wallet_totals(a)["available_cents"] == a_before
    assert db.session.scalar(select(func.count(ReferralCommission.id))) == 2

    # The unique constraint is the database-level backstop.
    assert db.session.scalar(select(func.count(ReferralCommission.id))) == 2


def test_return_settlement_catches_up_and_never_double_pays(app):
    user = make_user(email="returns@example.com", workspace_name="Returns House")
    fund_wallet(user, 300_000)
    investment = create_investment(
        user=user, plan_code="meridian", amount_cents=300_000
    )
    monthly = investment.monthly_return_cents
    assert monthly == 3_750

    # Backdate the position by three months and rewind its cursor.
    start = add_months(utc_today(), -3) - timedelta(days=1)
    investment.start_on = start
    investment.next_credit_on = add_months(start, 1)
    investment.matures_on = add_months(start, investment.term_months)
    db.session.commit()

    first = credit_due_returns(user_id=user.id)
    assert first.credited_count == 3
    assert first.credited_cents == monthly * 3
    assert wallet_totals(user)["available_cents"] == 300_000 - 300_000 + monthly * 3

    second = credit_due_returns(user_id=user.id)
    assert second.credited_count == 0

    returns = db.session.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.kind == "return",
        )
    ).all()
    assert len(returns) == 3
    assert {row.details["month_number"] for row in returns} == {1, 2, 3}

    # A manual demo settlement advances to month four without duplicating it.
    transaction = demo_simulate_next_return(user=user, investment_id=investment.id)
    assert transaction.details["month_number"] == 4
    db.session.refresh(investment)
    assert investment.months_credited == 4

    fourth = credit_due_returns(user_id=user.id)
    assert fourth.credited_count == 0


def test_investment_matures_after_full_term(app):
    user = make_user(email="mature@example.com", workspace_name="Mature House")
    fund_wallet(user, 100_000)
    investment = create_investment(
        user=user, plan_code="foundation", amount_cents=100_000
    )
    start = add_months(utc_today(), -7)
    investment.start_on = start
    investment.next_credit_on = add_months(start, 1)
    investment.matures_on = add_months(start, investment.term_months)
    db.session.commit()

    result = credit_due_returns(user_id=user.id)

    db.session.refresh(investment)
    assert investment.months_credited == investment.term_months
    assert investment.status == "matured"
    assert result.credited_count == investment.term_months
    assert result.investments_matured == 1

    with pytest.raises(ServiceError):
        demo_simulate_next_return(user=user, investment_id=investment.id)


def test_portfolio_summary_isolated_by_user(app):
    first = make_user(email="first@example.com", workspace_name="First House")
    second = make_user(email="second@example.com", workspace_name="Second House")
    fund_wallet(first, 200_000)
    create_investment(user=first, plan_code="foundation", amount_cents=100_000)

    first_summary = portfolio_summary(first)
    second_summary = portfolio_summary(second)

    assert first_summary["active_principal_cents"] == 100_000
    assert first_summary["monthly_income_cents"] == 750
    assert second_summary["active_principal_cents"] == 0
    assert second_summary["investments"] == []

