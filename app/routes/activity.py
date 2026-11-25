"""Paginated account activity ledger."""

from __future__ import annotations

from flask import Blueprint, g, render_template, request
from sqlalchemy import select

from ..constants import TRANSACTION_KIND_LABELS, TRANSACTION_STATUS_LABELS
from ..extensions import db
from ..models import Transaction
from ..security import login_required

activity_bp = Blueprint("activity", __name__, url_prefix="/activity")


@activity_bp.get("")
@login_required
def index():
    kind = request.args.get("kind", "")
    status = request.args.get("status", "")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    if kind not in TRANSACTION_KIND_LABELS:
        kind = ""
    if status not in TRANSACTION_STATUS_LABELS:
        status = ""

    statement = select(Transaction).where(
        Transaction.tenant_id == g.user.tenant_id,
        Transaction.user_id == g.user.id,
    )
    if kind:
        statement = statement.where(Transaction.kind == kind)
    if status:
        statement = statement.where(Transaction.status == status)
    statement = statement.order_by(Transaction.created_at.desc())

    pagination = db.paginate(
        statement,
        page=page,
        per_page=12,
        error_out=False,
    )

    return render_template(
        "activity/index.html",
        pagination=pagination,
        transactions=pagination.items,
        selected_kind=kind,
        selected_status=status,
        kinds=TRANSACTION_KIND_LABELS,
        statuses=TRANSACTION_STATUS_LABELS,
        active_page="activity",
    )
