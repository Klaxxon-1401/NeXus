"""Wallet, deposit and withdrawal request orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from flask import current_app
from sqlalchemy import select

from ..extensions import db
from ..models import Transaction, User, Wallet, utcnow
from ..utils import basis_points, mask_wallet_address
from . import ledger
from .errors import ServiceError
from .payments import (
    demo_mode_enabled,
    deposit_address_for,
    validate_destination_address,
    validate_network,
    validate_source_address,
    validate_tx_hash,
)


def _validate_amount(amount_cents: int, *, kind: str) -> None:
    if kind == "deposit":
        minimum = current_app.config["DEPOSIT_MIN_CENTS"]
        maximum = current_app.config["DEPOSIT_MAX_CENTS"]
    else:
        minimum = current_app.config["WITHDRAWAL_MIN_CENTS"]
        maximum = current_app.config["WITHDRAWAL_MAX_CENTS"]
    if amount_cents < minimum:
        raise ServiceError(f"Minimum amount is ${minimum / 100:,.2f}.")
    if amount_cents > maximum:
        raise ServiceError(f"Maximum amount is ${maximum / 100:,.2f}.")


def _user_wallet(user: User) -> Wallet:
    return ledger.get_wallet_for_update(user_id=user.id, tenant_id=user.tenant_id)


def request_deposit(
    *,
    user: User,
    amount_cents: int,
    network: str,
    source_address: str,
    tx_hash: str | None = None,
) -> Transaction:
    """Create a pending deposit intent after format validation.

    No balance changes yet: a deposit becomes spendable only after provider
    confirmation. This keeps unverified browser input from minting funds.
    """
    _validate_amount(amount_cents, kind="deposit")
    network = validate_network(network)
    source_address = validate_source_address(source_address, network)
    tx_hash = validate_tx_hash(tx_hash)

    if tx_hash:
        duplicate = db.session.scalar(
            select(Transaction).where(
                Transaction.tenant_id == user.tenant_id,
                Transaction.external_reference == tx_hash,
                Transaction.kind == "deposit",
            )
        )
        if duplicate:
            raise ServiceError("That transaction hash has already been submitted.")

    platform_address = deposit_address_for(network)
    request = Transaction(
        tenant_id=user.tenant_id,
        user_id=user.id,
        kind="deposit",
        direction="credit",
        status="pending",
        amount_cents=amount_cents,
        balance_after_cents=None,
        description=f"Deposit via {network.title()} awaiting confirmation",
        external_reference=tx_hash,
        details={
            "network": network,
            "source_address": source_address,
            "platform_address": platform_address,
            "provider": "manual-demo",
        },
    )
    db.session.add(request)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return request


def demo_confirm_deposit(*, user: User, transaction_id: uuid.UUID) -> Transaction:
    """Simulate a verified provider webhook confirming a deposit.

    This method is deliberately demo-only. In production the equivalent
    mutation must live behind provider signature verification and an
    idempotency check against the provider event id.
    """
    if not demo_mode_enabled():
        raise ServiceError("Deposit simulation is disabled.", code="demo_disabled")

    request = db.session.scalar(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
            Transaction.tenant_id == user.tenant_id,
            Transaction.kind == "deposit",
        )
    )
    if request is None:
        raise ServiceError("Deposit request not found.", code="not_found")
    if request.status != "pending":
        raise ServiceError("That deposit has already been processed.")
    if request.amount_cents <= 0:
        raise ServiceError("Invalid deposit amount.")

    wallet = _user_wallet(user)
    # The provider, not the account owner, is the authority for this state
    # transition in production. DEMO_MODE makes the shortcut explicit.
    request.status = "completed"
    request.settled_at = utcnow()
    request.details = {**request.details, "confirmed_by": "demo-simulation"}
    wallet.available_balance_cents += request.amount_cents
    request.balance_after_cents = wallet.available_balance_cents
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return request


def request_withdrawal(
    *,
    user: User,
    amount_cents: int,
    network: str,
    destination_address: str,
) -> Transaction:
    """Reserve funds and create a pending withdrawal for review/payout."""
    _validate_amount(amount_cents, kind="withdrawal")
    network = validate_network(network)
    destination_address = validate_destination_address(destination_address, network)

    wallet = _user_wallet(user)
    if wallet.available_balance_cents < amount_cents:
        raise ServiceError("Your available balance is too low for this withdrawal.")

    request = ledger.reserve_pending_withdrawal(
        wallet=wallet,
        amount_cents=amount_cents,
        description=f"Withdrawal to {mask_wallet_address(destination_address)}",
        details={
            "network": network,
            "destination_address": destination_address,
            "review": "pending",
            "provider": "manual-demo",
        },
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return request


def _get_pending_withdrawal(user: User, transaction_id: uuid.UUID) -> Transaction:
    request = db.session.scalar(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user.id,
            Transaction.tenant_id == user.tenant_id,
            Transaction.kind == "withdrawal",
        )
    )
    if request is None:
        raise ServiceError("Withdrawal request not found.", code="not_found")
    if request.status != "pending":
        raise ServiceError("That withdrawal has already been processed.")
    return request


def demo_approve_withdrawal(*, user: User, transaction_id: uuid.UUID) -> Transaction:
    """Simulate an operator/provider completing a payout.

    The reserved amount already left available cash. Approval only clears the
    pending hold and records completion.
    """
    if not demo_mode_enabled():
        raise ServiceError("Withdrawal simulation is disabled.", code="demo_disabled")

    request = _get_pending_withdrawal(user, transaction_id)
    wallet = _user_wallet(user)
    if wallet.pending_withdrawal_cents < request.amount_cents:
        raise ServiceError("The pending withdrawal balance is inconsistent.")
    wallet.pending_withdrawal_cents -= request.amount_cents
    request.status = "completed"
    request.settled_at = utcnow()
    request.details = {**request.details, "review": "approved", "approved_by": "demo-simulation"}
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return request


def demo_reject_withdrawal(*, user: User, transaction_id: uuid.UUID) -> Transaction:
    """Reject a pending withdrawal and return the reserved funds."""
    if not demo_mode_enabled():
        raise ServiceError("Withdrawal simulation is disabled.", code="demo_disabled")

    request = _get_pending_withdrawal(user, transaction_id)
    wallet = _user_wallet(user)
    if wallet.pending_withdrawal_cents < request.amount_cents:
        raise ServiceError("The pending withdrawal balance is inconsistent.")

    wallet.pending_withdrawal_cents -= request.amount_cents
    request.status = "rejected"
    request.settled_at = utcnow()
    request.details = {**request.details, "review": "rejected", "reviewed_by": "demo-simulation"}
    ledger.credit(
        wallet=wallet,
        amount_cents=request.amount_cents,
        kind="withdrawal_refund",
        description=f"Refund for withdrawal {request.reference}",
        details={"original_transaction": request.reference, "reason": "request rejected"},
        idempotency_key=f"withdrawal-refund:{request.id}",
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return request


def wallet_totals(user: User) -> dict[str, int]:
    """Return wallet headline figures from one consistent query set."""
    wallet = db.session.scalar(
        select(Wallet).where(Wallet.user_id == user.id, Wallet.tenant_id == user.tenant_id)
    )
    if wallet is None:  # pragma: no cover - invariant guard
        raise ServiceError("Wallet not found.", code="wallet_missing")

    transactions = db.session.scalars(
        select(Transaction).where(
            Transaction.tenant_id == user.tenant_id,
            Transaction.user_id == user.id,
            Transaction.status == "completed",
        )
    ).all()
    lifetime_deposits = sum(
        t.amount_cents for t in transactions if t.kind == "deposit" and t.direction == "credit"
    )
    lifetime_withdrawals = sum(
        t.amount_cents
        for t in transactions
        if t.kind == "withdrawal" and t.direction == "debit"
    )
    return {
        "available_cents": wallet.available_balance_cents,
        "pending_cents": wallet.pending_withdrawal_cents,
        "lifetime_deposits_cents": lifetime_deposits,
        "lifetime_withdrawals_cents": lifetime_withdrawals,
    }
