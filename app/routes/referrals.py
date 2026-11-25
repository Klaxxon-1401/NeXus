"""Referral network, invite tools and commission breakdown."""

from __future__ import annotations

from flask import Blueprint, g, render_template, url_for
from sqlalchemy import select

from ..extensions import db
from ..models import Referral, User
from ..security import login_required
from ..services.referrals import get_referral_overview

referrals_bp = Blueprint("referrals", __name__, url_prefix="/referrals")


@referrals_bp.get("")
@login_required
def index():
    overview = get_referral_overview(g.user)
    level_members = {
        level["level"]: [
            member for member in overview.members if member.level == level["level"]
        ]
        for level in overview.by_level
    }

    direct_inviter = db.session.scalar(
        select(User)
        .join(Referral, Referral.referrer_id == User.id)
        .where(
            Referral.referred_user_id == g.user.id,
            Referral.tenant_id == g.user.tenant_id,
            Referral.status == "active",
        )
    )

    invite_link = url_for(
        "auth.signup", ref=g.user.referral_code, _external=True
    )

    return render_template(
        "referrals/index.html",
        overview=overview,
        level_members=level_members,
        direct_inviter=direct_inviter,
        invite_link=invite_link,
        active_page="referrals",
    )
