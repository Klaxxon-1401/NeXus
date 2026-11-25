"""Application factory for Sigma Investments."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from flask import Flask, g, redirect, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .cli import register_cli
from .config import BASE_DIR, CONFIG_MAP, ProductionConfig
from .constants import (
    NETWORK_CHOICES,
    NETWORKS,
    PLANS,
    REFERRAL_LEVELS,
    TRANSACTION_KIND_LABELS,
    TRANSACTION_STATUS_LABELS,
)
from .extensions import csrf, db, limiter
from .routes import ALL_BLUEPRINTS
from .security import add_security_headers, load_logged_in_user
from .utils import (
    format_date,
    format_datetime,
    format_money,
    format_percent_from_bps,
    mask_wallet_address,
)


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    selected = config_name or os.getenv("APP_ENV", "development").lower()
    config_class = CONFIG_MAP.get(selected)
    if config_class is None:
        raise RuntimeError(f"Unknown APP_ENV: {selected}")
    if selected == "production":
        ProductionConfig.validate()
    app.config.from_object(config_class)
    app.config["ENV_NAME"] = selected

    os.makedirs(app.instance_path, exist_ok=True)

    if app.config["TRUST_PROXY_HEADERS"]:
        # Only enable this behind a proxy that overwrites these headers.
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1
        )

    _configure_logging(app)
    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    register_cli(app)
    _register_hooks(app)
    _register_template_helpers(app)
    _register_error_handlers(app)

    if app.config["SECRET_KEY"] == "dev-only-change-this-secret-key":
        app.logger.warning(
            "Using the development SECRET_KEY. Set SECRET_KEY in .env before deploying."
        )

    return app


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.config["DEBUG"] else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def enforce_https():
        if (
            app.config["FORCE_HTTPS"]
            and not app.config["TESTING"]
            and not request.is_secure
        ):
            secure_url = request.url.replace("http://", "https://", 1)
            return redirect(secure_url, code=308)
        return None

    app.before_request(load_logged_in_user)

    @app.after_request
    def security_headers(response):
        return add_security_headers(response)


def _register_template_helpers(app: Flask) -> None:
    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["date"] = format_date
    app.jinja_env.filters["datetime"] = format_datetime
    app.jinja_env.filters["percent_bps"] = format_percent_from_bps
    app.jinja_env.filters["mask_address"] = mask_wallet_address

    @app.context_processor
    def inject_globals():
        return {
            "current_user": g.get("user"),
            "current_tenant": g.get("tenant"),
            "wallet": g.get("wallet"),
            "plans": PLANS,
            "referral_levels": REFERRAL_LEVELS,
            "networks": NETWORKS,
            "network_choices": NETWORK_CHOICES,
            "transaction_kind_labels": TRANSACTION_KIND_LABELS,
            "transaction_status_labels": TRANSACTION_STATUS_LABELS,
            "demo_mode": app.config["DEMO_MODE"],
            "app_name": app.config["APP_NAME"],
            "support_email": app.config["SUPPORT_EMAIL"],
            "current_year": datetime.now(timezone.utc).year,
            "today": datetime.now(timezone.utc).date(),
        }


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def handle_csrf(error):  # pragma: no cover - exercised by integration tests
        app.logger.warning("csrf_rejected path=%s", request.path)
        return (
            render_template(
                "errors/csrf.html",
                message="Your form session expired. Refresh the page and try again.",
            ),
            400,
        )

    @app.errorhandler(400)
    def bad_request(error):
        return (
            render_template(
                "errors/error.html",
                code=400,
                title="That request did not look right",
                message="Check the form and try again. Nothing was changed.",
            ),
            400,
        )

    @app.errorhandler(403)
    def forbidden(error):
        return (
            render_template(
                "errors/error.html",
                code=403,
                title="This area is off limits",
                message="You do not have access to that page.",
            ),
            403,
        )

    @app.errorhandler(404)
    def not_found(error):
        return (
            render_template(
                "errors/error.html",
                code=404,
                title="We could not find that page",
                message="The link may be old, or the record may belong to another workspace.",
            ),
            404,
        )

    @app.errorhandler(429)
    def rate_limited(error):
        return (
            render_template(
                "errors/error.html",
                code=429,
                title="Take a breath",
                message="Too many attempts in a short period. Try again in a minute.",
            ),
            429,
        )

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        app.logger.exception("unhandled_error path=%s", request.path)
        return (
            render_template(
                "errors/error.html",
                code=500,
                title="Something went wrong",
                message="The request could not be completed. No money moved.",
            ),
            500,
        )
