"""Authentication, registration and session tests."""

from __future__ import annotations

from sqlalchemy import func, select

from app.extensions import db
from app.models import Referral, Tenant, User, Wallet

from .conftest import DEFAULT_PASSWORD, login, make_user


def test_public_pages_render(client):
    for path in ("/", "/login", "/signup"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Sigma" in response.data


def test_health_probe(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_signup_creates_tenant_user_and_wallet(client):
    response = client.post(
        "/signup",
        data={
            "full_name": "Jordan Lee",
            "email": "jordan@example.com",
            "password": DEFAULT_PASSWORD,
            "confirm_password": DEFAULT_PASSWORD,
            "workspace_name": "Jordan Capital",
            "referral_code": "",
            "accept_terms": "y",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")

    user = db.session.scalar(select(User).where(User.email == "jordan@example.com"))
    assert user is not None
    assert user.role == "owner"
    assert user.password_hash != DEFAULT_PASSWORD
    assert user.check_password(DEFAULT_PASSWORD)
    assert user.tenant.name == "Jordan Capital"
    assert user.wallet.available_balance_cents == 0

    with client.session_transaction() as session:
        assert session["user_id"] == str(user.id)
        assert session["tenant_id"] == str(user.tenant_id)


def test_signup_with_invite_joins_referrer_workspace(client):
    referrer = make_user(
        email="owner@example.com",
        full_name="Owner Person",
        workspace_name="Shared House",
    )
    response = client.post(
        "/signup",
        data={
            "full_name": "Invited Person",
            "email": "invited@example.com",
            "password": DEFAULT_PASSWORD,
            "confirm_password": DEFAULT_PASSWORD,
            "workspace_name": "",
            "referral_code": referrer.referral_code,
            "accept_terms": "y",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    invited = db.session.scalar(
        select(User).where(User.email == "invited@example.com")
    )
    assert invited.tenant_id == referrer.tenant_id
    assert invited.role == "investor"

    edge = db.session.scalar(
        select(Referral).where(Referral.referred_user_id == invited.id)
    )
    assert edge is not None
    assert edge.referrer_id == referrer.id
    assert edge.code_used == referrer.referral_code


def test_signup_rejects_short_password_and_missing_workspace(client):
    short = client.post(
        "/signup",
        data={
            "full_name": "Short Pass",
            "email": "short@example.com",
            "password": "short",
            "confirm_password": "short",
            "workspace_name": "Short Workspace",
            "referral_code": "",
            "accept_terms": "y",
        },
    )
    assert short.status_code == 200
    assert db.session.scalar(select(func.count(User.id))) == 0
    assert db.session.scalar(select(func.count(Tenant.id))) == 0

    missing = client.post(
        "/signup",
        data={
            "full_name": "No Workspace",
            "email": "none@example.com",
            "password": DEFAULT_PASSWORD,
            "confirm_password": DEFAULT_PASSWORD,
            "workspace_name": "",
            "referral_code": "",
            "accept_terms": "y",
        },
    )
    assert missing.status_code == 200
    assert b"Choose a workspace name" in missing.data
    assert db.session.scalar(select(func.count(User.id))) == 0


def test_duplicate_email_is_rejected(client):
    make_user(email="taken@example.com", workspace_name="First")
    response = client.post(
        "/signup",
        data={
            "full_name": "Another Person",
            "email": "taken@example.com",
            "password": DEFAULT_PASSWORD,
            "confirm_password": DEFAULT_PASSWORD,
            "workspace_name": "Second",
            "referral_code": "",
            "accept_terms": "y",
        },
    )
    assert response.status_code == 200
    assert b"already exists" in response.data
    assert db.session.scalar(select(func.count(Tenant.id))) == 1


def test_login_generic_failure_and_success(client):
    user = make_user(email="login@example.com", workspace_name="Login House")

    wrong = login(client, user.email, "not-the-right-password")
    assert wrong.status_code == 200
    assert b"Email or password is incorrect" in wrong.data

    unknown = login(client, "missing@example.com", "whatever-password")
    assert unknown.status_code == 200
    assert b"Email or password is incorrect" in unknown.data

    success = login(client, user.email)
    assert success.status_code == 302
    assert success.headers["Location"].endswith("/dashboard")

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"Welcome back" in dashboard.data

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_open_redirect_target_is_ignored(client):
    user = make_user(email="redirect@example.com", workspace_name="Redirect House")
    response = client.post(
        "/login",
        data={
            "email": user.email,
            "password": DEFAULT_PASSWORD,
            "next": "//attacker.example/steal",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_protected_pages_require_session(client):
    response = client.get("/wallet")
    assert response.status_code == 302
    assert "/login?next=/wallet" in response.headers["Location"]


def test_wallet_created_once_per_user(app):
    user = make_user(email="wallet@example.com", workspace_name="Wallet House")
    count = db.session.scalar(
        select(func.count(Wallet.id)).where(Wallet.user_id == user.id)
    )
    assert count == 1
