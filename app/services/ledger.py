"""Low-level wallet ledger primitives.

Every balance mutation goes through this module. That keeps the transaction
snapshot, idempotency and non-negative constraints consistent. Public service
functions own the surrounding database transaction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from ..extensions import db
from ..models import Transaction, Wallet, utcnow
from .errors import ServiceError


def get_wallet_for_update(*, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Wallet:
    """Fetch a wallet with a row lock on databases that support it.

    PostgreSQL honours ``FOR UPDATE``, serialising concurrent balance changes.
    SQLite serialises writers at the database level; this still provides a
    single-writer safety net for local development.
    """
    wallet = db.session.scalar(
        select(Wallet)
        .where(Wallet.user_id == user_id, Wallet.tenant_id == tenant_id)
        .with_for_update()
    )
    if wallet is None:
        raise ServiceError("Wallet not found.", code="wallet_missing")
    return wallet


def find_by_idempotency_key(*, tenant_id: uuid.UUID, key: str) -> Transaction | None:
    return db.session.scalar(
        select(Transaction).where(
            Transaction.tenant_id == tenant_id,
            Transaction.idempotency_key == key,
        )
    )


def credit(
    *,
    wallet: Wallet,
    amount_cents: int,
    kind: str,
    description: str,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    investment_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    external_reference: str | None = None,
    settled_at: datetime | None = None,
) -> Transaction:
    """Credit available cash and append a completed transaction."""
    if amount_cents <= 0:
        raise ServiceError("Credit amount must be positive.")
    wallet.available_balance_cents += amount_cents
    tx = Transaction(
        tenant_id=tenant_id or wallet.tenant_id,
        user_id=user_id or wallet.user_id,
        investment_id=investment_id,
        kind=kind,
        direction="credit",
        status="completed",
        amount_cents=amount_cents,
        balance_after_cents=wallet.available_balance_cents,
        description=description,
        details=details or {},
        idempotency_key=idempotency_key,
        external_reference=external_reference,
        settled_at=settled_at or utcnow(),
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def debit(
    *,
    wallet: Wallet,
    amount_cents: int,
    kind: str,
    description: str,
    investment_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Transaction:
    """Debit available cash and append a completed transaction."""
    if amount_cents <= 0:
        raise ServiceError("Debit amount must be positive.")
    if wallet.available_balance_cents < amount_cents:
        raise ServiceError("Your available balance is too low.")
    wallet.available_balance_cents -= amount_cents
    tx = Transaction(
        tenant_id=wallet.tenant_id,
        user_id=wallet.user_id,
        investment_id=investment_id,
        kind=kind,
        direction="debit",
        status="completed",
        amount_cents=amount_cents,
        balance_after_cents=wallet.available_balance_cents,
        description=description,
        details=details or {},
        idempotency_key=idempotency_key,
        settled_at=utcnow(),
    )
    db.session.add(tx)
    db.session.flush()
    return tx


def reserve_pending_withdrawal(
    *,
    wallet: Wallet,
    amount_cents: int,
    description: str,
    details: dict[str, Any],
) -> Transaction:
    """Move funds from available to pending without crediting the recipient.

    The request is a pending debit. If operations rejects it, a refund
    transaction returns the amount to available cash.
    """
    if amount_cents <= 0:
        raise ServiceError("Withdrawal amount must be positive.")
    if wallet.available_balance_cents < amount_cents:
        raise ServiceError("Your available balance is too low.")
    wallet.available_balance_cents -= amount_cents
    wallet.pending_withdrawal_cents += amount_cents
    tx = Transaction(
        tenant_id=wallet.tenant_id,
        user_id=wallet.user_id,
        kind="withdrawal",
        direction="debit",
        status="pending",
        amount_cents=amount_cents,
        # Snapshot spendable cash after the hold was placed.
        balance_after_cents=wallet.available_balance_cents,
        description=description,
        details=details,
    )
    db.session.add(tx)
    db.session.flush()
    return tx
