"""Signup, sign-in and sign-out routes."""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select

from ..constants import DEMO_ACCOUNTS
from ..extensions import db, limiter
from ..forms import LoginForm, SignupForm
from ..models import User
from ..security import end_session, load_logged_in_user, start_session
from ..services.auth import authenticate_user, record_login, register_user
from ..services.errors import ServiceError
from ..utils import normalize_referral_code, safe_redirect_target

auth_bp = Blueprint("auth", __name__)


def _demo_accounts() -> list[dict]:
    if not current_app.config.get("DEMO_MODE"):
        return []
    emails = [account["email"] for account in DEMO_ACCOUNTS]
    existing = set(
        db.session.scalars(select(User.email).where(User.email.in_(emails))).all()
    )
    return [
        {**account, "password": current_app.config["DEMO_PASSWORD"]}
        for account in DEMO_ACCOUNTS
        if account["email"] in existing
    ]


def _referrer_preview(code: str) -> User | None:
    normalized = normalize_referral_code(code)
    if not normalized:
        return None
    return db.session.scalar(
        select(User).where(
            User.referral_code == normalized,
            User.status == "active",
        )
    )


@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def signup():
    if g.user is not None:
        return redirect(url_for("dashboard.index"))

    form = SignupForm()
    invite_param = request.args.get("ref", "")
    if request.method == "GET" and invite_param:
        form.referral_code.data = normalize_referral_code(invite_param)

    if form.validate_on_submit():
        try:
            result = register_user(
                full_name=form.full_name.data,
                email=form.email.data,
                password=form.password.data,
                workspace_name=form.workspace_name.data,
                referral_code=form.referral_code.data,
                user_agent=request.user_agent.string,
            )
        except ServiceError as exc:
            flash(exc.message, "error")
        else:
            start_session(result.user)
            record_login(result.user)
            if result.joined_workspace:
                flash(
                    f"Welcome to {result.tenant.name}. Your account is ready.", "success"
                )
            else:
                flash(
                    f"{result.tenant.name} is ready. Fund your wallet to open a position.",
                    "success",
                )
            return redirect(url_for("dashboard.index"))

    preview = _referrer_preview(form.referral_code.data or invite_param)
    return (
        render_template(
            "auth/signup.html",
            form=form,
            referrer_preview=preview,
            invite_code=normalize_referral_code(form.referral_code.data or invite_param),
            active_page="signup",
        ),
        200,
    )


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 40 per hour", methods=["POST"])
def login():
    if g.user is not None:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    next_target = safe_redirect_target(
        request.values.get("next") or request.args.get("next")
    )

    if form.validate_on_submit():
        user = authenticate_user(form.email.data, form.password.data)
        if user is None:
            # Generic response: do not disclose whether an account exists.
            flash("Email or password is incorrect.", "error")
        else:
            start_session(user)
            record_login(user)
            flash(f"Welcome back, {user.first_name}.", "success")
            return redirect(next_target or url_for("dashboard.index"))

    return render_template(
        "auth/login.html",
        form=form,
        next_target=next_target or "",
        demo_accounts=_demo_accounts(),
        active_page="login",
    )


@auth_bp.post("/logout")
def logout():
    end_session()
    flash("You have been signed out.", "success")
    return redirect(url_for("public.landing"))
