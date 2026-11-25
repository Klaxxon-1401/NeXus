"""Registration and authentication services."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from ..extensions import db
from ..models import Referral, Tenant, User, Wallet
from ..models.user import generate_referral_code
from ..utils import (
    clean_text,
    is_valid_email,
    normalize_email,
    normalize_referral_code,
    slugify,
)
from .errors import ServiceError

# Used to equalise timing when an email does not exist. It is never a real
# account and contains no secret.
_TIMING_DUMMY_HASH = generate_password_hash("not-a-real-password", method="scrypt")

_COMMON_PASSWORDS = {
    "password1234",
    "passwordpassword",
    "qwertyuiop12",
    "letmein123456",
    "123456789012",
}


@dataclass(slots=True)
class RegistrationResult:
    user: User
    tenant: Tenant
    joined_workspace: bool
    referrer: User | None


def _unique_slug(base: str) -> str:
    candidate = slugify(base)
    if not db.session.scalar(select(Tenant.id).where(Tenant.slug == candidate)):
        return candidate
    for _ in range(20):
        suffixed = f"{candidate}-{secrets.token_hex(2)}"
        if not db.session.scalar(select(Tenant.id).where(Tenant.slug == suffixed)):
            return suffixed
    raise ServiceError("Could not reserve a workspace address. Please try again.")


def _unique_referral_code() -> str:
    for _ in range(20):
        code = generate_referral_code()
        if not db.session.scalar(select(User.id).where(User.referral_code == code)):
            return code
    raise ServiceError("Could not allocate an invite code. Please try again.")


def _validate_password(password: str) -> None:
    minimum = current_app.config["MIN_PASSWORD_LENGTH"]
    if len(password) < minimum:
        raise ServiceError(f"Use at least {minimum} characters for your password.")
    if len(password) > 128:
        raise ServiceError("Password is too long.")
    if password.casefold() in _COMMON_PASSWORDS:
        raise ServiceError("That password is too common. Choose a stronger one.")


def _normalize_name(full_name: str) -> str:
    name = clean_text(full_name, max_length=80)
    if len(name) < 2:
        raise ServiceError("Enter your full name.")
    return name


def register_user(
    *,
    full_name: str,
    email: str,
    password: str,
    workspace_name: str,
    referral_code: str,
    user_agent: str | None = None,
) -> RegistrationResult:
    """Create a user, an isolated workspace or invited membership, and a wallet.

    Invitation model: a valid invite code joins the inviter's tenant. This lets
    the commission graph span multiple people while every financial row remains
    isolated from other client workspaces.
    """
    normalized_email = normalize_email(email)
    if not is_valid_email(normalized_email):
        raise ServiceError("Enter a valid email address.")
    _validate_password(password)
    name = _normalize_name(full_name)

    if db.session.scalar(select(User.id).where(User.email == normalized_email)):
        raise ServiceError("An account with that email already exists.")

    normalized_code = normalize_referral_code(referral_code)
    referrer: User | None = None
    tenant: Tenant
    joined = False

    if normalized_code:
        referrer = db.session.scalar(
            select(User).where(
                User.referral_code == normalized_code,
                User.status == "active",
            )
        )
        if not referrer or referrer.tenant.status != "active":
            raise ServiceError("That invite code is not valid or is no longer active.")
        tenant = referrer.tenant
        joined = True
    else:
        cleaned_workspace = clean_text(workspace_name, max_length=60)
        if len(cleaned_workspace) < 3:
            raise ServiceError("Choose a workspace name of at least 3 characters.")
        tenant = Tenant(name=cleaned_workspace, slug=_unique_slug(cleaned_workspace))
        db.session.add(tenant)
        db.session.flush()

    user = User(
        tenant_id=tenant.id,
        email=normalized_email,
        full_name=name,
        role="investor" if joined else "owner",
        referral_code=_unique_referral_code(),
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    wallet = Wallet(
        tenant_id=tenant.id,
        user_id=user.id,
        available_balance_cents=0,
        pending_withdrawal_cents=0,
    )
    db.session.add(wallet)

    if referrer is not None:
        db.session.add(
            Referral(
                tenant_id=tenant.id,
                referrer_id=referrer.id,
                referred_user_id=user.id,
                code_used=normalized_code,
            )
        )

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        current_app.logger.warning(
            "registration_conflict email_domain=%s joined=%s",
            normalized_email.rsplit("@", 1)[-1],
            joined,
        )
        raise ServiceError("That account or invite could not be created. Please try again.") from exc

    current_app.logger.info(
        "registration_success tenant_id=%s user_id=%s joined=%s",
        tenant.id,
        user.id,
        joined,
    )
    return RegistrationResult(
        user=user, tenant=tenant, joined_workspace=joined, referrer=referrer
    )


def authenticate_user(email: str, password: str) -> User | None:
    """Return an active user for valid credentials, otherwise ``None``.

    The same generic outcome is used for unknown users, suspended users and bad
    passwords. A dummy verification is performed for unknown emails to reduce
    the timing signal between those cases.
    """
    normalized_email = normalize_email(email)
    user = db.session.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        # Keep the failure path computationally similar to a real check.
        from werkzeug.security import check_password_hash

        check_password_hash(_TIMING_DUMMY_HASH, password)
        return None
    if user.status != "active":
        return None
    if user.tenant.status != "active":
        return None
    if not user.check_password(password):
        return None
    return user


def record_login(user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
