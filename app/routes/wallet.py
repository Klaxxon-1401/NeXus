"""Wallet deposit and withdrawal flows."""

from __future__ import annotations

import uuid

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from ..extensions import db, limiter
from ..forms import DepositForm, WithdrawalForm
from ..models import Transaction, User
from ..security import demo_only, login_required
from ..services.errors import ServiceError
from ..services.wallet import (
    demo_approve_withdrawal,
    demo_confirm_deposit,
    demo_reject_withdrawal,
    request_deposit,
    request_withdrawal,
    wallet_totals,
)

wallet_bp = Blueprint("wallet", __name__, url_prefix="/wallet")


def _wallet_context(
    *,
    deposit_form: DepositForm | None = None,
    withdrawal_form: WithdrawalForm | None = None,
) -> dict:
    pending_deposits = _query_transactions(g.user, kind="deposit")
    pending_withdrawals = _query_transactions(g.user, kind="withdrawal")
    recent = _query_transactions(g.user, kind=None, pending=False)
    return {
        "totals": wallet_totals(g.user),
        "deposit_form": deposit_form or DepositForm(),
        "withdrawal_form": withdrawal_form or WithdrawalForm(),
        "deposit_addresses": current_app.config["DEPOSIT_ADDRESSES"],
        "pending_deposits": pending_deposits,
        "pending_withdrawals": pending_withdrawals,
        "recent_transactions": recent,
        "active_page": "wallet",
    }


def _query_transactions(
    user: User, *, kind: str | None, pending: bool = True
) -> list[Transaction]:
    query = select(Transaction).where(
        Transaction.tenant_id == user.tenant_id,
        Transaction.user_id == user.id,
    )
    if pending:
        query = query.where(
            Transaction.status == "pending",
            Transaction.kind == kind,
        )
    return list(
        db.session.scalars(
            query.order_by(Transaction.created_at.desc()).limit(7)
        ).all()
    )


@wallet_bp.get("")
@login_required
def index():
    return render_template("wallet/index.html", **_wallet_context())


@wallet_bp.post("/deposit")
@login_required
@limiter.limit("10 per minute", methods=["POST"])
def deposit():
    form = DepositForm()
    if form.validate_on_submit():
        try:
            request_deposit(
                user=g.user,
                amount_cents=form.amount.cents,
                network=form.network.data,
                source_address=form.source_address.data,
                tx_hash=form.tx_hash.data or None,
            )
        except ServiceError as exc:
            flash(exc.message, "error")
        else:
            flash(
                "Deposit request created. It stays pending until the transfer is verified.",
                "success",
            )
            return redirect(url_for("wallet.index"))
    return render_template(
        "wallet/index.html",
        **_wallet_context(deposit_form=form, withdrawal_form=WithdrawalForm()),
    ), 400


@wallet_bp.post("/withdraw")
@login_required
@limiter.limit("10 per minute", methods=["POST"])
def withdraw():
    form = WithdrawalForm()
    if form.validate_on_submit():
        try:
            request_withdrawal(
                user=g.user,
                amount_cents=form.amount.cents,
                network=form.network.data,
                destination_address=form.destination_address.data,
            )
        except ServiceError as exc:
            flash(exc.message, "error")
        else:
            flash(
                "Withdrawal requested. The amount is reserved until review completes.",
                "success",
            )
            return redirect(url_for("wallet.index"))
    return render_template(
        "wallet/index.html",
        **_wallet_context(deposit_form=DepositForm(), withdrawal_form=form),
    ), 400


@wallet_bp.post("/deposits/<uuid:transaction_id>/confirm")
@login_required
@demo_only
@limiter.limit("20 per minute", methods=["POST"])
def confirm_deposit(transaction_id: uuid.UUID):
    try:
        transaction = demo_confirm_deposit(user=g.user, transaction_id=transaction_id)
    except ServiceError as exc:
        flash(exc.message, "error")
    else:
        flash(
            f"Demo deposit ${transaction.amount_cents / 100:,.2f} credited to available cash.",
            "success",
        )
    return redirect(url_for("wallet.index", _anchor="activity"))


@wallet_bp.post("/withdrawals/<uuid:transaction_id>/approve")
@login_required
@demo_only
@limiter.limit("20 per minute", methods=["POST"])
def approve_withdrawal(transaction_id: uuid.UUID):
    try:
        transaction = demo_approve_withdrawal(user=g.user, transaction_id=transaction_id)
    except ServiceError as exc:
        flash(exc.message, "error")
    else:
        flash(f"Demo withdrawal {transaction.reference} marked as paid.", "success")
    return redirect(url_for("wallet.index", _anchor="pending"))


@wallet_bp.post("/withdrawals/<uuid:transaction_id>/reject")
@login_required
@demo_only
@limiter.limit("20 per minute", methods=["POST"])
def reject_withdrawal(transaction_id: uuid.UUID):
    try:
        demo_reject_withdrawal(user=g.user, transaction_id=transaction_id)
    except ServiceError as exc:
        flash(exc.message, "error")
    else:
        flash("Withdrawal rejected and the reserved funds returned to your balance.", "success")
    return redirect(url_for("wallet.index", _anchor="pending"))
