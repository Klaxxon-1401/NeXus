"""Authenticated overview dashboard."""

from __future__ import annotations

from sqlalchemy import func, select

from flask import Blueprint, g, render_template

from ..extensions import db
from ..models import Transaction
from ..security import login_required
from ..services.investments import portfolio_summary
from ..services.referrals import get_referral_overview
from ..services.wallet import wallet_totals
from ..utils import build_area_chart

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/dashboard")
@login_required
def index():
    user = g.user
    summary = portfolio_summary(user)
    referral = get_referral_overview(user)
    totals = wallet_totals(user)

    recent_transactions = db.session.scalars(
        select(Transaction)
        .where(
            Transaction.tenant_id == user.tenant_id,
            Transaction.user_id == user.id,
        )
        .order_by(Transaction.created_at.desc())
        .limit(6)
    ).all()

    pending_requests = db.session.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.tenant_id == user.tenant_id,
            Transaction.user_id == user.id,
            Transaction.status == "pending",
            Transaction.kind.in_(("deposit", "withdrawal")),
        )
    )

    projection = summary["projection"]
    chart = build_area_chart([int(item["amount_cents"]) for item in projection])
    chart_labels = [item["month"] for item in projection]
    next_month_income = projection[0]["amount_cents"] if projection else 0

    total_portfolio_value = summary["active_principal_cents"] + totals["available_cents"]

    return render_template(
        "dashboard/index.html",
        summary=summary,
        referral=referral,
        totals=totals,
        recent_transactions=recent_transactions,
        pending_requests=pending_requests or 0,
        chart=chart,
        chart_labels=chart_labels,
        next_month_income=next_month_income,
        total_portfolio_value=total_portfolio_value,
        active_page="dashboard",
    )
