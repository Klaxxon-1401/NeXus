"""Portfolio overview, allocation form and demo return settlement."""

from __future__ import annotations

import uuid

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from ..constants import PLANS
from ..extensions import limiter
from ..forms import InvestmentForm
from ..models import Investment
from ..security import demo_only, login_required
from ..services.errors import ServiceError
from ..services.investments import (
    create_investment,
    demo_simulate_next_return,
    investment_schedule,
    portfolio_summary,
)
from ..services.referrals import total_referral_earnings
from ..services.wallet import wallet_totals

portfolio_bp = Blueprint("portfolio", __name__, url_prefix="/portfolio")


def _portfolio_context(form: InvestmentForm) -> dict:
    summary = portfolio_summary(g.user)
    schedules = {
        investment.id: investment_schedule(investment, include_paid=False)[:4]
        for investment in summary["active"]
    }
    return {
        "summary": summary,
        "schedules": schedules,
        "form": form,
        "plans": PLANS.values(),
        "totals": wallet_totals(g.user),
        "referral_earnings_cents": total_referral_earnings(g.user),
        "active_page": "portfolio",
    }


@portfolio_bp.get("")
@login_required
def index():
    selected = request.args.get("plan", "meridian")
    if selected not in PLANS:
        selected = "meridian"
    form = InvestmentForm(plan_code=selected)
    return render_template(
        "portfolio/index.html",
        **_portfolio_context(form),
    )


@portfolio_bp.post("/invest")
@login_required
@limiter.limit("10 per minute", methods=["POST"])
def invest():
    form = InvestmentForm()
    if not form.validate_on_submit():
        first_error = next(
            (message for errors in form.errors.values() for message in errors),
            "Check the investment details and try again.",
        )
        flash(first_error, "error")
        return redirect(url_for("portfolio.index", plan=request.form.get("plan_code", "meridian")))

    try:
        investment = create_investment(
            user=g.user,
            plan_code=form.plan_code.data,
            amount_cents=form.amount.cents,
        )
    except ServiceError as exc:
        flash(exc.message, "error")
        return redirect(url_for("portfolio.index", plan=form.plan_code.data))

    flash(
        f"{investment.plan_name} position opened for ${investment.principal_cents / 100:,.2f}.",
        "success",
    )
    return redirect(url_for("portfolio.index"))


@portfolio_bp.post("/<uuid:investment_id>/simulate-return")
@login_required
@demo_only
@limiter.limit("20 per minute", methods=["POST"])
def simulate_return(investment_id: uuid.UUID):
    try:
        transaction = demo_simulate_next_return(
            user=g.user, investment_id=investment_id
        )
    except ServiceError as exc:
        flash(exc.message, "error")
    else:
        flash(
            f"Simulated ${transaction.amount_cents / 100:,.2f} return credited for one month.",
            "success",
        )
    return redirect(url_for("portfolio.index"))
