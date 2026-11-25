"""Blueprint registration."""

from .activity import activity_bp
from .auth import auth_bp
from .dashboard import dashboard_bp
from .portfolio import portfolio_bp
from .public import public_bp
from .referrals import referrals_bp
from .wallet import wallet_bp

ALL_BLUEPRINTS = (
    public_bp,
    auth_bp,
    dashboard_bp,
    portfolio_bp,
    wallet_bp,
    referrals_bp,
    activity_bp,
)

__all__ = ["ALL_BLUEPRINTS"]
