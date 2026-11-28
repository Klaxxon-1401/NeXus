"""Domain constants shared by routes, services, templates and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    min_cents: int
    term_months: int
    monthly_rate_bps: int
    description: str
    accent: str
    risk: str = "Illustrative"

    @property
    def monthly_rate_percent(self) -> str:
        return f"{self.monthly_rate_bps / 100:.2f}%"

    @property
    def annual_rate_percent(self) -> str:
        # A simple annualisation for display only; not a promise of compounding.
        return f"{self.monthly_rate_bps * 12 / 100:.2f}%"


# The monthly rates are deliberately illustrative. They are stored in basis
# points (1 bps = 0.01%) so all calculations stay in integers.
PLANS: dict[str, Plan] = {
    "foundation": Plan(
        code="foundation",
        name="Foundation",
        min_cents=10_000,
        term_months=6,
        monthly_rate_bps=75,
        description="A shorter cycle for learning the mechanics of scheduled payouts.",
        accent="gold",
    ),
    "meridian": Plan(
        code="meridian",
        name="Meridian",
        min_cents=100_000,
        term_months=12,
        monthly_rate_bps=125,
        description="The balanced demo portfolio with a twelve-month distribution schedule.",
        accent="amber",
    ),
    "summit": Plan(
        code="summit",
        name="Summit",
        min_cents=500_000,
        term_months=24,
        monthly_rate_bps=175,
        description="A longer illustrated horizon for testing multi-year ledger projections.",
        accent="warm",
    ),
}

PLAN_CHOICES = [(plan.code, plan.name) for plan in PLANS.values()]


# --------------------------------------------------------------------------
# Referral commission schedule
# --------------------------------------------------------------------------
# Level 1 = the investor's direct referrer; level 2 = that person's referrer,
# and so on. Each entry is a percentage of the *investor's principal* expressed
# in basis points. The tree stops after five levels.
REFERRAL_RATES_BPS: tuple[int, ...] = (225, 125, 75, 50, 25)

REFERRAL_LEVELS = tuple(
    {
        "level": index + 1,
        "rate_bps": rate,
        "rate_percent": f"{rate / 100:.2f}%",
    }
    for index, rate in enumerate(REFERRAL_RATES_BPS)
)


# Payment networks used by the manual deposit/withdrawal flow.
NETWORKS: dict[str, str] = {
    "ethereum": "Ethereum (ERC-20)",
    "bitcoin": "Bitcoin",
    "solana": "Solana",
}
NETWORK_CHOICES = list(NETWORKS.items())


# Seeded in development by ``flask --app run.py init-db --seed``. Never ship
# these credentials or seed data into a production database.
DEMO_ACCOUNTS = (
    {"name": "Alex Morgan", "email": "alex@sigma.demo", "role": "Workspace owner"},
    {"name": "Maya Chen", "email": "maya@sigma.demo", "role": "Investor · Level 1"},
    {"name": "Priya Shah", "email": "priya@sigma.demo", "role": "Investor · Level 2"},
    {"name": "Admin User", "email": "admin@sigma.demo", "role": "admin"},
)


TRANSACTION_KIND_LABELS = {
    "deposit": "Deposit",
    "withdrawal": "Withdrawal",
    "withdrawal_refund": "Withdrawal refund",
    "investment": "Investment",
    "return": "Monthly return",
    "referral": "Referral commission",
}

TRANSACTION_STATUS_LABELS = {
    "pending": "Pending",
    "completed": "Completed",
    "rejected": "Rejected",
    "failed": "Failed",
}

INVESTMENT_STATUS_LABELS = {
    "active": "Active",
    "matured": "Matured",
    "cancelled": "Cancelled",
}
