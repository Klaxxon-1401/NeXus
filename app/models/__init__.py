"""Import models once so SQLAlchemy and Alembic see the full metadata graph."""

from .base import Base, utcnow
from .investment import Investment
from .referral import Referral, ReferralCommission
from .tenant import Tenant
from .transaction import Transaction
from .user import User
from .wallet import Wallet

__all__ = [
    "Base",
    "Investment",
    "Referral",
    "ReferralCommission",
    "Tenant",
    "Transaction",
    "User",
    "Wallet",
    "utcnow",
]
