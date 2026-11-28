"""Admin panel for reviewing and approving wallet operations."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select, func

from ..extensions import db
from ..models import Transaction, User
from ..security import admin_required
from ..services.errors import ServiceError
from ..services.wallet import (
    demo_confirm_deposit,
    demo_approve_withdrawal,
    demo_reject_withdrawal,
    wallet_totals,
)

if TYPE_CHECKING:
    from ..models import Tenant

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _get_pending_deposits(tenant_id: uuid.UUID | None = None):
    """Get all pending deposit transactions, optionally filtered by tenant."""
    query = select(Transaction).where(
        Transaction.kind == "deposit",
        Transaction.status == "pending",
    )
    if tenant_id:
        query = query.where(Transaction.tenant_id == tenant_id)
    return list(
        db.session.scalars(
            query.order_by(Transaction.created_at.desc()).limit(200)
        ).all()
    )


def _get_pending_withdrawals(tenant_id: uuid.UUID | None = None):
    """Get all pending withdrawal transactions, optionally filtered by tenant."""
    query = select(Transaction).where(
        Transaction.kind == "withdrawal",
        Transaction.status == "pending",
    )
    if tenant_id:
        query = query.where(Transaction.tenant_id == tenant_id)
    return list(
        db.session.scalars(
            query.order_by(Transaction.created_at.desc()).limit(200)
        ).all()
    )


def _get_all_tenants():
    """Get all active tenants with user counts."""
    from ..models import Tenant
    return list(
        db.session.scalars(
            select(Tenant).where(Tenant.status == "active").order_by(Tenant.name)
        ).all()
    )


@admin_bp.get("")
@admin_required
def dashboard():
    """Admin dashboard showing pending operations across all tenants."""
    tenant_id = request.args.get("tenant_id")
    tenant_uuid = uuid.UUID(tenant_id) if tenant_id else None

    pending_deposits = _get_pending_deposits(tenant_uuid)
    pending_withdrawals = _get_pending_withdrawals(tenant_uuid)
    tenants = _get_all_tenants()

    # Summary stats
    total_pending_deposits = sum(t.amount_cents for t in pending_deposits)
    total_pending_withdrawals = sum(t.amount_cents for t in pending_withdrawals)

    return render_template(
        "admin/dashboard.html",
        pending_deposits=pending_deposits,
        pending_withdrawals=pending_withdrawals,
        tenants=tenants,
        selected_tenant_id=tenant_uuid,
        total_pending_deposits=total_pending_deposits,
        total_pending_withdrawals=total_pending_withdrawals,
        active_page="admin",
    )


@admin_bp.post("/deposits/<uuid:transaction_id>/approve")
@admin_required
def approve_deposit(transaction_id: uuid.UUID):
    """Approve a pending deposit and credit the user's wallet."""
    try:
        transaction = demo_confirm_deposit(user=g.user, transaction_id=transaction_id)
    except ServiceError as exc:
        flash(exc.message, "error")
    else:
        flash(
            f"Deposit {transaction.reference} (${transaction.amount_cents / 100:,.2f}) approved and credited to {transaction.user.full_name}.",
            "success",
        )
    return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))


@admin_bp.post("/deposits/<uuid:transaction_id>/reject")
@admin_required
def reject_deposit(transaction_id: uuid.UUID):
    """Reject a pending deposit."""
    transaction = db.session.get(Transaction, transaction_id)
    if transaction is None:
        flash("Deposit request not found.", "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    if transaction.kind != "deposit" or transaction.status != "pending":
        flash("That deposit has already been processed.", "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    transaction.status = "rejected"
    transaction.settled_at = current_app.extensions["db"].session.execute(
        current_app.extensions["db"].text("SELECT NOW() AT TIME ZONE 'UTC'")
    ).scalar()
    transaction.details = {**transaction.details, "review": "rejected", "reviewed_by": f"admin:{g.user.email}"}

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Failed to reject deposit.", "error")
    else:
        flash(f"Deposit {transaction.reference} rejected.", "success")

    return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))


@admin_bp.post("/withdrawals/<uuid:transaction_id>/approve")
@admin_required
def approve_withdrawal(transaction_id: uuid.UUID):
    """Approve a pending withdrawal. Requires external transaction ID."""
    external_tx_id = request.form.get("external_transaction_id", "").strip()

    if not external_tx_id:
        flash("External transaction ID is required to approve a withdrawal.", "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    # Fetch the transaction first to validate
    transaction = db.session.get(Transaction, transaction_id)
    if transaction is None:
        flash("Withdrawal request not found.", "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    if transaction.kind != "withdrawal" or transaction.status != "pending":
        flash("That withdrawal has already been processed.", "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    # Validate it belongs to an active tenant
    if transaction.tenant_id is None:
        flash("Invalid withdrawal request.", "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    try:
        # Use the existing service but we need to set the external reference
        # The service expects the user to be the owner, but admin acts on behalf
        # We'll call the service with the actual user, then update the external reference
        transaction = demo_approve_withdrawal(user=transaction.user, transaction_id=transaction_id)
    except ServiceError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))

    # Update with the external transaction ID provided by admin
    transaction.external_reference = external_tx_id
    transaction.details = {
        **transaction.details,
        "external_transaction_id": external_tx_id,
        "review": "approved",
        "approved_by": f"admin:{g.user.email}",
    }

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Failed to finalize withdrawal approval.", "error")
    else:
        flash(
            f"Withdrawal {transaction.reference} (${transaction.amount_cents / 100:,.2f}) approved for {transaction.user.full_name}. External TX: {external_tx_id}",
            "success",
        )

    return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))


@admin_bp.post("/withdrawals/<uuid:transaction_id>/reject")
@admin_required
def reject_withdrawal(transaction_id: uuid.UUID):
    """Reject a pending withdrawal and return funds to available balance."""
    try:
        transaction = demo_reject_withdrawal(user=g.user, transaction_id=transaction_id)
    except ServiceError as exc:
        flash(exc.message, "error")
    else:
        # Update with admin info
        transaction.details = {
            **transaction.details,
            "review": "rejected",
            "rejected_by": f"admin:{g.user.email}",
        }
        db.session.commit()
        flash(
            f"Withdrawal {transaction.reference} (${transaction.amount_cents / 100:,.2f}) rejected for {transaction.user.full_name}. Funds returned to available balance.",
            "success",
        )

    return redirect(url_for("admin.dashboard", tenant_id=request.args.get("tenant_id")))


@admin_bp.get("/users")
@admin_required
def users():
    """List all users across tenants with wallet summary."""
    from ..models import Wallet

    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25

    query = (
        select(User, Wallet.available_balance_cents, Wallet.pending_withdrawal_cents)
        .outerjoin(Wallet, (Wallet.user_id == User.id) & (Wallet.tenant_id == User.tenant_id))
        .where(User.status == "active")
        .order_by(User.created_at.desc())
    )

    total = db.session.scalar(select(func.count()).select_from(query.subquery()))
    users_with_wallets = list(
        db.session.execute(query.limit(per_page).offset((page - 1) * per_page)).all()
    )

    return render_template(
        "admin/users.html",
        users=users_with_wallets,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=(total + per_page - 1) // per_page,
        active_page="admin_users",
    )