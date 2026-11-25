"""Public pages and the unauthenticated health probe."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import text

from ..constants import PLANS, REFERRAL_LEVELS
from ..extensions import db, limiter

public_bp = Blueprint("public", __name__)


@public_bp.get("/")
def landing():
    return render_template(
        "public/landing.html",
        plans=PLANS.values(),
        referral_levels=REFERRAL_LEVELS,
        active_page="home",
    )


@public_bp.get("/healthz")
@limiter.exempt
def healthz():
    """Liveness/readiness probe; deliberately excludes sensitive detail."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - only hit when the DB is down
        db.session.rollback()
        return jsonify({"status": "unavailable"}), 503
    return jsonify({"status": "ok", "service": "sigma-investments"})
