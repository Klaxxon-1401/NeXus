"""Shared pytest fixtures and small factories for the prototype test suite."""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from app.models import User
from app.services.auth import register_user

DEFAULT_PASSWORD = "CorrectHorse!2026"


@pytest.fixture()
def app():
    application = create_app("testing")
    application.config.update(
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        DEMO_MODE=True,
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_user(
    *,
    email: str,
    full_name: str = "Test Person",
    workspace_name: str = "Test Workspace",
    referrer: User | None = None,
    password: str = DEFAULT_PASSWORD,
) -> User:
    """Register a user directly through the service layer.

    When a referrer is supplied the new user joins that referrer's tenant and
    a direct referral edge is created, exactly like the public signup route.
    """
    result = register_user(
        full_name=full_name,
        email=email,
        password=password,
        workspace_name=workspace_name,
        referral_code=referrer.referral_code if referrer else "",
    )
    return result.user


def fund_wallet(user: User, cents: int) -> None:
    from app.services.wallet import demo_confirm_deposit, request_deposit

    request = request_deposit(
        user=user,
        amount_cents=cents,
        network="ethereum",
        source_address="0x" + "11" * 20,
    )
    demo_confirm_deposit(user=user, transaction_id=request.id)


def login(client, email: str, password: str = DEFAULT_PASSWORD):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
