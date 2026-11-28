"""Deterministic development seed data.

All people and balances are fictional. Seed data exists to demonstrate the
tenant scoping, referral graph and ledger state transitions end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

from flask import current_app
from sqlalchemy import select

from .extensions import db
from .models import (
    Investment,
    Referral,
    ReferralCommission,
    Tenant,
    Transaction,
    User,
    Wallet,
)
from .models.user import generate_referral_code
from .services.auth import register_user
from .services.errors import ServiceError
from .services.investments import create_investment, credit_due_returns
from .services.wallet import (
    demo_confirm_deposit,
    request_deposit,
    request_withdrawal,
)
from .utils import add_months, utc_today

DEMO_PEOPLE = (
    ("Admin User", "admin@sigma.demo", None, "admin"),
    ("Alex Morgan", "alex@sigma.demo", None, ""),
    ("Maya Chen", "maya@sigma.demo", "alex@sigma.demo", "meridian"),
    ("Jonas Reed", "jonas@sigma.demo", "alex@sigma.demo", "foundation"),
    ("Priya Shah", "priya@sigma.demo", "maya@sigma.demo", "meridian"),
    ("Leo Park", "leo@sigma.demo", "priya@sigma.demo", "summit"),
    ("Noor Haddad", "noor@sigma.demo", "leo@sigma.demo", "foundation"),
    ("Elena Rossi", "elena@sigma.demo", "noor@sigma.demo", "meridian"),
    ("Sam Okafor", "sam@sigma.demo", "elena@sigma.demo", "foundation"),
)

# display email, plan, amount in whole dollars, start months ago, deposit dollars
# Demo join dates, in months before today. Ordered so every referrer joined
# before the person they invited, and every member joined before their first
# backdated position opened.
DEMO_JOINED_MONTHS_AGO = {
    "admin@sigma.demo": 12,
    "alex@sigma.demo": 12,
    "maya@sigma.demo": 10,
    "jonas@sigma.demo": 9,
    "priya@sigma.demo": 8,
    "leo@sigma.demo": 7,
    "noor@sigma.demo": 6,
    "elena@sigma.demo": 5,
    "sam@sigma.demo": 4,
}

DEMO_POSITIONS = {
    "admin@sigma.demo": [],
    "alex@sigma.demo": [
        ("meridian", 20_000, 4, 35_000),
        ("foundation", 2_500, 2, 0),
    ],
    "maya@sigma.demo": [("meridian", 12_000, 5, 16_000)],
    "jonas@sigma.demo": [("foundation", 8_000, 2, 10_000)],
    "priya@sigma.demo": [("meridian", 6_000, 3, 8_000)],
    "leo@sigma.demo": [("summit", 5_000, 2, 7_000)],
    "noor@sigma.demo": [("foundation", 4_000, 1, 6_000)],
    "elena@sigma.demo": [("meridian", 3_000, 0, 4_000)],
    "sam@sigma.demo": [("foundation", 1_500, 0, 2_000)],
}


def _demo_eth_address(index: int) -> str:
    return "0x" + f"{index + 1:040x}"


def seed_demo() -> bool:
    """Create the demo workspace if it does not exist.

    Returns ``True`` when rows were created, ``False`` when it was already
    seeded. Never call this from production startup.
    """
    if not current_app.config.get("DEMO_MODE"):
        raise RuntimeError("Refusing to seed demo data outside DEMO_MODE.")

    existing = db.session.scalar(select(Tenant).where(Tenant.slug == "meridian-circle"))
    if existing is not None:
        return False

    password = current_app.config["DEMO_PASSWORD"]
    users: dict[str, User] = {}

    for index, (name, email, referred_by_email, seed_role) in enumerate(DEMO_PEOPLE):
        referrer = users.get(referred_by_email) if referred_by_email else None
        result = register_user(
            full_name=name,
            email=email,
            password=password,
            workspace_name="Meridian Circle",
            referral_code=referrer.referral_code if referrer else "",
            user_agent="sigma-demo-seed",
        )
        users[email] = result.user

        # Set admin role for the admin user
        if seed_role == "admin":
            result.user.role = "admin"

        if not referred_by_email:
            continue

        # Funding comes from a separate demo "treasury" deposit. These are
        # pending intents first, then explicitly confirmed through the same
        # service path a provider webhook would use.
        for plan_code, amount_dollars, _months_ago, deposit_dollars in DEMO_POSITIONS[email]:
            if deposit_dollars:
                request = request_deposit(
                    user=result.user,
                    amount_cents=deposit_dollars * 100,
                    network="ethereum",
                    source_address=_demo_eth_address(index),
                )
                demo_confirm_deposit(user=result.user, transaction_id=request.id)
                _backdate_transaction(request, months_ago=7)
            create_investment(
                user=result.user,
                plan_code=plan_code,
                amount_cents=amount_dollars * 100,
            )

    # Root workspace owner needs the same funding/purchase flow.
    alex = users["alex@sigma.demo"]
    for plan_code, amount_dollars, _months_ago, deposit_dollars in DEMO_POSITIONS["alex@sigma.demo"]:
        if deposit_dollars:
            request = request_deposit(
                user=alex,
                amount_cents=deposit_dollars * 100,
                network="ethereum",
                source_address=_demo_eth_address(99),
            )
            demo_confirm_deposit(user=alex, transaction_id=request.id)
            _backdate_transaction(request, months_ago=7)
        create_investment(
            user=alex,
            plan_code=plan_code,
            amount_cents=amount_dollars * 100,
        )

    # Backdate registrations and referral edges first. A referrer must exist
    # before the invited member, and both must predate the member's positions.
    for email, months_ago in DEMO_JOINED_MONTHS_AGO.items():
        user = users[email]
        joined_at = datetime.combine(
            add_months(utc_today(), -months_ago), time(8, 0), tzinfo=timezone.utc
        )
        user.created_at = joined_at
        user.updated_at = joined_at
        if user.wallet is not None:
            user.wallet.created_at = joined_at
            user.wallet.updated_at = joined_at
        edge = db.session.scalar(
            select(Referral).where(Referral.referred_user_id == user.id)
        )
        if edge is not None:
            edge.created_at = joined_at
            edge.updated_at = joined_at
    db.session.commit()

    # Backdate investment start dates so the schedule has a credible history,
    # then let the normal catch-up job post all due monthly returns.
    for email, positions in DEMO_POSITIONS.items():
        user = users[email]
        investments = list(
            db.session.scalars(
                select(Investment)
                .where(Investment.user_id == user.id)
                .order_by(Investment.created_at.asc())
            ).all()
        )
        for investment, (_plan, _amount, months_ago, _deposit) in zip(investments, positions):
            start = add_months(utc_today(), -months_ago)
            investment.start_on = start
            investment.matures_on = add_months(start, investment.term_months)
            investment.next_credit_on = add_months(start, 1)
            investment.created_at = datetime.combine(
                start, time(9, 30), tzinfo=timezone.utc
            )
            opened_at = datetime.combine(start, time(9, 30), tzinfo=timezone.utc)
            # Align the opening debit and its referral payouts with the
            # backdated position so the demo statement reads chronologically.
            opening_debit = db.session.scalar(
                select(Transaction).where(
                    Transaction.investment_id == investment.id,
                    Transaction.kind == "investment",
                )
            )
            if opening_debit is not None:
                opening_debit.created_at = opened_at
                opening_debit.settled_at = opened_at
            for commission in db.session.scalars(
                select(ReferralCommission).where(
                    ReferralCommission.investment_id == investment.id
                )
            ).all():
                commission.created_at = opened_at
                payout = db.session.get(Transaction, commission.transaction_id)
                if payout is not None:
                    payout.created_at = opened_at
                    payout.settled_at = opened_at
        db.session.commit()

    credit_due_returns()
    _backdate_return_postings()

    # Seed two requests so the wallet approval workflow is visible immediately.
    pending_deposit = request_deposit(
        user=alex,
        amount_cents=2_500_00,
        network="ethereum",
        source_address=_demo_eth_address(120),
    )
    pending_deposit.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db.session.commit()

    request_withdrawal(
        user=alex,
        amount_cents=1_200_00,
        network="ethereum",
        destination_address=_demo_eth_address(121),
    )

    return True


def _unique_referral_code() -> str:
    for _ in range(20):
        code = generate_referral_code()
        if not db.session.scalar(select(User.id).where(User.referral_code == code)):
            return code
    raise RuntimeError("Could not allocate a unique demo referral code.")


DEMO_SAMPLE_KEYS = {
    "deposit": "demo-sample:v1:deposit",
    "withdrawal": "demo-sample:v1:withdrawal",
}


def _demo_operations_user(tenant: Tenant) -> User | None:
    user = db.session.scalar(select(User).where(User.email == "alex@sigma.demo"))
    if user is not None:
        return user
    return db.session.scalar(
        select(User)
        .where(User.tenant_id == tenant.id, User.status == "active")
        .order_by(User.created_at.asc())
    )


def _create_demo_deposit(user: User, *, idempotency_key: str) -> Transaction:
    transaction = request_deposit(
        user=user,
        amount_cents=2_500_00,
        network="ethereum",
        source_address=_demo_eth_address(120),
        transaction_id="TX-DEMO-8F3A21",
    )
    transaction.idempotency_key = idempotency_key
    transaction.details = {**transaction.details, "demo_sample": True}
    db.session.commit()
    return transaction


def _create_demo_withdrawal(user: User, *, idempotency_key: str) -> Transaction:
    transaction = request_withdrawal(
        user=user,
        amount_cents=1_200_00,
        network="ethereum",
        destination_address=_demo_eth_address(121),
    )
    transaction.idempotency_key = idempotency_key
    transaction.details = {**transaction.details, "demo_sample": True}
    db.session.commit()
    return transaction


def ensure_demo_operations() -> dict[str, list[str]]:
    """Guarantee the Operations queue has sample requests to review.

    ``seed_demo`` only writes the sample pending deposit/withdrawal on a fresh
    database, so a hosted database seeded by an older build shows an empty
    Operations page. The samples carry a fixed idempotency key: they are
    created once and are never recreated after an admin approves or rejects
    them. Existing pending requests are respected, not duplicated.
    """
    if not current_app.config.get("DEMO_MODE"):
        raise RuntimeError("Refusing to seed demo operations outside DEMO_MODE.")

    tenant = db.session.scalar(select(Tenant).where(Tenant.slug == "meridian-circle"))
    if tenant is None:
        return {"created": []}

    user = _demo_operations_user(tenant)
    if user is None:
        return {"created": []}

    created: list[str] = []

    for kind, create in (
        ("deposit", _create_demo_deposit),
        ("withdrawal", _create_demo_withdrawal),
    ):
        marker = DEMO_SAMPLE_KEYS[kind]
        exists = db.session.scalar(
            select(Transaction.id).where(
                Transaction.tenant_id == tenant.id,
                Transaction.idempotency_key == marker,
            )
        )
        if exists is not None:
            continue
        pending = db.session.scalar(
            select(Transaction.id).where(
                Transaction.tenant_id == tenant.id,
                Transaction.kind == kind,
                Transaction.status == "pending",
            )
        )
        if pending is not None:
            continue
        try:
            transaction = create(user, idempotency_key=marker)
        except ServiceError:
            # e.g. the wallet cannot cover another reserved withdrawal
            db.session.rollback()
            continue
        created.append(transaction.reference)

    return {"created": created}


def create_demo_operations() -> dict[str, list[str]]:
    """Add a fresh pair of reviewable samples (manual ``seed-operations``)."""
    if not current_app.config.get("DEMO_MODE"):
        raise RuntimeError("Refusing to seed demo operations outside DEMO_MODE.")

    tenant = db.session.scalar(select(Tenant).where(Tenant.slug == "meridian-circle"))
    if tenant is None:
        return {"created": []}
    user = _demo_operations_user(tenant)
    if user is None:
        return {"created": []}

    suffix = uuid.uuid4().hex[:8]
    created: list[str] = []
    for kind, create in (
        ("deposit", _create_demo_deposit),
        ("withdrawal", _create_demo_withdrawal),
    ):
        try:
            transaction = create(
                user, idempotency_key=f"demo-sample:manual:{kind}:{suffix}"
            )
        except ServiceError:
            db.session.rollback()
            continue
        created.append(transaction.reference)
    return {"created": created}



def ensure_demo_accounts() -> dict[str, list[str]]:
    """Create or repair demo sign-in accounts on an existing database.

    ``seed_demo`` is a one-shot bootstrap: once the Meridian Circle tenant
    exists it returns early, so accounts added to ``DEMO_PEOPLE`` by later
    releases never reach a database seeded by an older build (this is how the
    hosted admin account went missing). This routine is idempotent and safe to
    run on every boot. It only fills gaps: balances, positions and ledger rows
    are never touched.

    Returns the emails that were created and those that needed repair.
    """
    if not current_app.config.get("DEMO_MODE"):
        raise RuntimeError("Refusing to prepare demo accounts outside DEMO_MODE.")

    tenant = db.session.scalar(select(Tenant).where(Tenant.slug == "meridian-circle"))
    if tenant is None:
        # Fresh database: the full seed already creates every demo account.
        seed_demo()
        return {"created": [email for _n, email, _r, _s in DEMO_PEOPLE], "repaired": []}

    password = current_app.config["DEMO_PASSWORD"]
    created: list[str] = []
    repaired: list[str] = []
    users: dict[str, User] = {}

    for name, email, referred_by_email, seed_role in DEMO_PEOPLE:
        user = db.session.scalar(select(User).where(User.email == email))

        if user is None:
            role = (
                "admin"
                if seed_role == "admin"
                else "investor" if referred_by_email else "owner"
            )
            user = User(
                tenant_id=tenant.id,
                email=email,
                full_name=name,
                role=role,
                referral_code=_unique_referral_code(),
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            db.session.add(
                Wallet(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    available_balance_cents=0,
                    pending_withdrawal_cents=0,
                )
            )
            created.append(email)
        else:
            changed = False
            if seed_role == "admin" and user.role != "admin":
                user.role = "admin"
                changed = True
            if user.status != "active":
                user.status = "active"
                changed = True
            if user.tenant is not None and user.tenant.status != "active":
                user.tenant.status = "active"
                changed = True
            # Keep hosted demo credentials deterministic. Only re-hash when the
            # configured demo password no longer matches, so unchanged boots
            # do no unnecessary work.
            if not user.check_password(password):
                user.set_password(password)
                changed = True
            if changed:
                repaired.append(email)

            has_wallet = db.session.scalar(
                select(Wallet.id).where(Wallet.user_id == user.id)
            )
            if not has_wallet:
                db.session.add(
                    Wallet(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        available_balance_cents=0,
                        pending_withdrawal_cents=0,
                    )
                )
                if email not in repaired:
                    repaired.append(email)

        users[email] = user

        referrer = users.get(referred_by_email) if referred_by_email else None
        if referrer is not None:
            edge = db.session.scalar(
                select(Referral).where(Referral.referred_user_id == user.id)
            )
            if edge is None:
                db.session.add(
                    Referral(
                        tenant_id=tenant.id,
                        referrer_id=referrer.id,
                        referred_user_id=user.id,
                        code_used=referrer.referral_code,
                    )
                )

    db.session.commit()
    return {"created": created, "repaired": repaired}


def _backdate_transaction(transaction: Transaction, *, months_ago: int) -> None:
    timestamp = datetime.now(timezone.utc) - timedelta(days=months_ago * 30)
    transaction.created_at = timestamp
    transaction.settled_at = timestamp
    db.session.add(transaction)
    db.session.commit()


def _backdate_return_postings() -> None:
    """For demo realism, timestamp return credits on their scheduled date."""
    returns = db.session.scalars(
        select(Transaction).where(Transaction.kind == "return")
    ).all()
    for transaction in returns:
        scheduled = transaction.details.get("scheduled_on")
        if not scheduled:
            continue
        scheduled_date = datetime.strptime(scheduled, "%Y-%m-%d").date()
        timestamp = datetime.combine(scheduled_date, time(10, 0), tzinfo=timezone.utc)
        transaction.created_at = timestamp
        transaction.settled_at = timestamp
        if transaction.investment_id:
            investment = db.session.get(Investment, transaction.investment_id)
            if investment and investment.created_at:
                investment.updated_at = timestamp
    db.session.commit()
