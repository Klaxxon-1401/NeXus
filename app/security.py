"""Session authentication, request context and security headers."""

from __future__ import annotations

import functools
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import (
    abort,
    current_app,
    flash,
    g,
    redirect,
    request,
    session,
    url_for,
)

from sqlalchemy import select

from .extensions import db
from .models import User, Wallet
from .utils import safe_redirect_target


def start_session(user: User) -> None:
    """Start a fresh server-signed browser session.

    Clearing before writing a new identity prevents session fixation. The
    session cookie is HTTP-only and SameSite=Lax (Secure in production).
    """
    session.clear()
    session.permanent = True
    session["user_id"] = str(user.id)
    session["tenant_id"] = str(user.tenant_id)
    session["authenticated_at"] = datetime.now(timezone.utc).timestamp()
    session["session_nonce"] = secrets.token_urlsafe(18)


def end_session() -> None:
    session.clear()


def load_logged_in_user() -> None:
    """Populate request-scoped auth and tenant context before each request."""
    g.user = None
    g.tenant = None
    g.wallet = None

    if request.endpoint == "static":
        return

    raw_user_id = session.get("user_id")
    raw_tenant_id = session.get("tenant_id")
    if not raw_user_id or not raw_tenant_id:
        return

    try:
        user_id = uuid.UUID(raw_user_id)
        tenant_id = uuid.UUID(raw_tenant_id)
    except (TypeError, ValueError):
        end_session()
        return

    user = db.session.get(User, user_id)
    if (
        user is None
        or user.tenant_id != tenant_id
        or user.status != "active"
        or user.tenant.status != "active"
    ):
        end_session()
        return

    # An absolute server-side cap in addition to the signed cookie's expiry.
    authenticated_at = session.get("authenticated_at")
    lifetime_seconds = current_app.permanent_session_lifetime.total_seconds()
    now_ts = datetime.now(timezone.utc).timestamp()
    if (
        not isinstance(authenticated_at, (int, float))
        or now_ts - float(authenticated_at) > lifetime_seconds
    ):
        end_session()
        return

    g.user = user
    g.tenant = user.tenant
    g.wallet = db.session.scalar(
        select(Wallet).where(
            Wallet.user_id == user.id,
            Wallet.tenant_id == user.tenant_id,
        )
    )


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Sign in to continue.", "warning")
            next_path = safe_redirect_target(request.full_path.rstrip("?"))
            if request.method == "GET" and next_path:
                return redirect(url_for("auth.login", next=next_path))
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def demo_only(view):
    """Hide simulation endpoints completely outside local demo deployments."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_app.config.get("DEMO_MODE"):
            abort(404)
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Restrict access to users with the 'admin' role."""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Sign in to continue.", "warning")
            next_path = safe_redirect_target(request.full_path.rstrip("?"))
            if request.method == "GET" and next_path:
                return redirect(url_for("auth.login", next=next_path))
            return redirect(url_for("auth.login"))
        if g.user.role != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def post_only_redirect(endpoint: str):
    """Helper used by templates to avoid dead links to form-only actions."""
    return redirect(url_for(endpoint))


def add_security_headers(response):
    """Apply a conservative browser security baseline to every response."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "object-src 'none'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'"
    )
    if request.is_secure:
        csp += "; upgrade-insecure-requests"
    response.headers.setdefault("Content-Security-Policy", csp)

    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

    if g.get("user") is not None or request.endpoint in {"auth.login", "auth.signup"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers.add("Vary", "Cookie")

    return response


def is_same_origin_request() -> bool:
    """Used by webhook/stub boundaries that cannot use a CSRF form token."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return urlsplit(origin).netloc == request.host
