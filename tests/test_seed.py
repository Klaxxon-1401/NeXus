"""Hosted-demo account repair tests.

The Render database was seeded by a release that predates the admin account.
``seed_demo`` returns early once the tenant exists, so ``ensure_demo_accounts``
must be able to add and repair sign-ins without disturbing ledger data.
"""

from __future__ import annotations

from sqlalchemy import select

from app.extensions import db
from app.models import Transaction, User, Wallet
from app.seed import DEMO_PEOPLE, ensure_demo_accounts, seed_demo
from app.services.auth import authenticate_user

ADMIN_EMAIL = "admin@sigma.demo"


def _delete_admin() -> None:
    admin = db.session.scalar(select(User).where(User.email == ADMIN_EMAIL))
    wallet = db.session.scalar(select(Wallet).where(Wallet.user_id == admin.id))
    if wallet is not None:
        db.session.delete(wallet)
    db.session.delete(admin)
    db.session.commit()


def test_ensure_demo_accounts_creates_missing_admin(app):
    assert seed_demo() is True
    _delete_admin()
    transaction_count = db.session.scalar(select(Transaction.id).limit(1))
    assert transaction_count is not None

    result = ensure_demo_accounts()

    assert ADMIN_EMAIL in result["created"]
    admin = db.session.scalar(select(User).where(User.email == ADMIN_EMAIL))
    assert admin is not None
    assert admin.role == "admin"
    assert admin.status == "active"
    assert admin.tenant.slug == "meridian-circle"
    assert admin.wallet is not None
    assert authenticate_user(ADMIN_EMAIL, app.config["DEMO_PASSWORD"]) is not None


def test_ensure_demo_accounts_repairs_role_and_password(app):
    assert seed_demo() is True
    admin = db.session.scalar(select(User).where(User.email == ADMIN_EMAIL))
    admin.role = "investor"
    admin.set_password("DifferentPassword!2026")
    db.session.commit()

    result = ensure_demo_accounts()

    assert ADMIN_EMAIL in result["repaired"]
    assert admin.role == "admin"
    assert authenticate_user(ADMIN_EMAIL, app.config["DEMO_PASSWORD"]) is not None


def test_ensure_demo_accounts_is_idempotent(app):
    assert seed_demo() is True

    first = ensure_demo_accounts()
    second = ensure_demo_accounts()

    assert first["created"] == []
    assert second["created"] == []
    assert second["repaired"] == []
    assert len(second) == 2


def test_login_page_lists_admin_demo_account(app, client):
    assert seed_demo() is True
    ensure_demo_accounts()

    response = client.get("/login")

    assert response.status_code == 200
    assert ADMIN_EMAIL.encode() in response.data
    assert ADMIN_EMAIL in {person[1] for person in DEMO_PEOPLE}


def test_ensure_demo_accounts_preserves_balances(app):
    assert seed_demo() is True
    alex = db.session.scalar(select(User).where(User.email == "alex@sigma.demo"))
    before = alex.wallet.available_balance_cents
    assert before > 0

    ensure_demo_accounts()

    db.session.refresh(alex.wallet)
    assert alex.wallet.available_balance_cents == before
