"""Wallet deposit, withdrawal and tenant-isolation tests."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import Transaction
from app.services.errors import ServiceError
from app.services.wallet import (
    demo_approve_withdrawal,
    demo_confirm_deposit,
    demo_reject_withdrawal,
    request_deposit,
    request_withdrawal,
    wallet_totals,
)

from .conftest import fund_wallet, login, make_user


def test_deposit_is_pending_until_confirmed(app):
    user = make_user(email="deposit@example.com", workspace_name="Deposit House")
    request = request_deposit(
        user=user,
        amount_cents=250_000,
        network="ethereum",
        source_address="0x" + "ab" * 20,
        tx_hash="0x" + "cd" * 32,
    )
    assert request.status == "pending"
    assert wallet_totals(user)["available_cents"] == 0

    confirmed = demo_confirm_deposit(user=user, transaction_id=request.id)
    assert confirmed.status == "completed"
    assert confirmed.settled_at is not None
    assert wallet_totals(user)["available_cents"] == 250_000

    with pytest.raises(ServiceError):
        demo_confirm_deposit(user=user, transaction_id=request.id)


def test_deposit_amount_limits_and_duplicate_hash(app):
    user = make_user(email="limits@example.com", workspace_name="Limit House")
    with pytest.raises(ServiceError):
        request_deposit(
            user=user,
            amount_cents=100,
            network="ethereum",
            source_address="0x" + "ab" * 20,
        )

    tx_hash = "0x" + "ef" * 32
    request_deposit(
        user=user,
        amount_cents=5_000,
        network="ethereum",
        source_address="0x" + "ab" * 20,
        tx_hash=tx_hash,
    )
    with pytest.raises(ServiceError):
        request_deposit(
            user=user,
            amount_cents=5_000,
            network="ethereum",
            source_address="0x" + "ab" * 20,
            tx_hash=tx_hash,
        )


def test_withdrawal_reserves_and_rejection_refunds(app):
    user = make_user(email="withdraw@example.com", workspace_name="Withdraw House")
    fund_wallet(user, 100_000)

    request = request_withdrawal(
        user=user,
        amount_cents=30_000,
        network="ethereum",
        destination_address="0x" + "cd" * 20,
    )
    assert request.status == "pending"
    totals = wallet_totals(user)
    assert totals["available_cents"] == 70_000
    assert totals["pending_cents"] == 30_000

    demo_reject_withdrawal(user=user, transaction_id=request.id)
    totals = wallet_totals(user)
    assert totals["available_cents"] == 100_000
    assert totals["pending_cents"] == 0
    assert db.session.get(Transaction, request.id).status == "rejected"

    refunds = db.session.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.kind == "withdrawal_refund",
        )
    )
    assert refunds == 1

    again = request_withdrawal(
        user=user,
        amount_cents=40_000,
        network="ethereum",
        destination_address="0x" + "cd" * 20,
    )
    demo_approve_withdrawal(user=user, transaction_id=again.id)
    totals = wallet_totals(user)
    assert totals["available_cents"] == 60_000
    assert totals["pending_cents"] == 0
    assert db.session.get(Transaction, again.id).status == "completed"


def test_withdrawal_cannot_exceed_available_balance(app):
    user = make_user(email="overdraw@example.com", workspace_name="Overdraw House")
    fund_wallet(user, 10_000)
    with pytest.raises(ServiceError):
        request_withdrawal(
            user=user,
            amount_cents=20_000,
            network="ethereum",
            destination_address="0x" + "cd" * 20,
        )


def test_cross_tenant_actions_are_denied(app, client):
    owner = make_user(
        email="owner-a@example.com",
        workspace_name="Tenant A",
        full_name="Owner A",
    )
    outsider = make_user(
        email="owner-b@example.com",
        workspace_name="Tenant B",
        full_name="Owner B",
    )
    fund_wallet(owner, 100_000)

    deposit = request_deposit(
        user=owner,
        amount_cents=10_000,
        network="ethereum",
        source_address="0x" + "ab" * 20,
    )
    login(client, outsider.email)
    # Another tenant's pending deposit cannot be confirmed by ID.
    response = client.post(
        f"/wallet/deposits/{deposit.id}/confirm", follow_redirects=False
    )
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Transaction, deposit.id).status == "pending"

    # Another tenant's investment cannot be settled by ID.
    from app.services.investments import create_investment

    investment = create_investment(
        user=owner, plan_code="foundation", amount_cents=10_000
    )
    response = client.post(
        f"/portfolio/{investment.id}/simulate-return", follow_redirects=False
    )
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(type(investment), investment.id).months_credited == 0


def test_activity_filters_scope_to_current_user(app, client):
    user = make_user(email="activity@example.com", workspace_name="Activity House")
    fund_wallet(user, 50_000)
    login(client, user.email)

    deposits = client.get("/activity?kind=deposit")
    assert deposits.status_code == 200
    table = deposits.data.split(b"<tbody>", 1)[1].split(b"</tbody>", 1)[0]
    assert b"Deposit via Ethereum" in table
    assert b"allocation" not in table

    investments = client.get("/activity?kind=investment")
    assert investments.status_code == 200
    assert b"No entries match this filter" in investments.data

