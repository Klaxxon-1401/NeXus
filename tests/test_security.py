"""Security headers, CSRF, configuration and utility tests."""

from __future__ import annotations

from datetime import date
import re

import pytest

from app.config import ProductionConfig
from app.constants import PLANS, REFERRAL_RATES_BPS
from app.utils import (
    add_months,
    basis_points,
    build_area_chart,
    format_money,
    mask_wallet_address,
    normalize_referral_code,
    parse_money_to_cents,
    safe_redirect_target,
)

from .conftest import DEFAULT_PASSWORD, make_user


def test_security_headers_and_csp(client):
    response = client.get("/", base_url="https://localhost")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_csrf_rejects_unprotected_post(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": DEFAULT_PASSWORD},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code == 400
    assert b"form session expired" in response.data.lower()


def test_csrf_token_round_trip(app, client):
    """A rendered token must be accepted with its matching session cookie.

    This guards against configuration values that are valid for Flask-WTF's
    API but invalid for the installed itsdangerous release.
    """
    user = make_user(email="csrf@example.com", workspace_name="CSRF House")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        page = client.get("/login")
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"', page.data
        )
        assert match is not None
        token = match.group(1).decode()
        response = client.post(
            "/login",
            data={
                "email": user.email,
                "password": DEFAULT_PASSWORD,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_production_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")
    with pytest.raises(RuntimeError) as error:
        ProductionConfig.validate()
    message = str(error.value)
    assert "SECRET_KEY" in message
    assert "DATABASE_URL" in message
    assert "DEMO_MODE" in message
    assert "TRUST_PROXY_HEADERS" in message


def test_production_configuration_accepts_safe_settings(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db/sigma")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    ProductionConfig.validate()


def test_parse_money_to_cents_exact():
    assert parse_money_to_cents("1,250.50") == 125_050
    assert parse_money_to_cents("0.01") == 1
    assert parse_money_to_cents("100") == 10_000
    for invalid in ("", "0", "-5", "1.234", "abc", None, "1e5"):
        with pytest.raises(ValueError):
            parse_money_to_cents(invalid)


def test_basis_points_half_up():
    assert basis_points(10_000_00, 225) == 22_500
    assert basis_points(10_000_00, 125) == 12_500
    assert basis_points(1, 5000) == 1  # 0.5 rounds up
    assert basis_points(1, 4999) == 0
    assert basis_points(333, 75) == 2


def test_referral_schedule_is_five_levels():
    assert REFERRAL_RATES_BPS == (225, 125, 75, 50, 25)
    assert sum(REFERRAL_RATES_BPS) == 500


def test_add_months_clamps_to_valid_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 25), -12) == date(2025, 11, 25)


def test_safe_redirect_target_blocks_external_targets():
    assert safe_redirect_target("/wallet") == "/wallet"
    assert safe_redirect_target("//evil.example") is None
    assert safe_redirect_target("https://evil.example") is None
    assert safe_redirect_target("/\\evil") is None
    assert safe_redirect_target("/ok\r\nSet-Cookie: x") is None
    assert safe_redirect_target(None) is None


def test_formatting_and_masking():
    assert format_money(123_456) == "$1,234.56"
    assert format_money(-100) == "-$1.00"
    assert format_money(100, with_sign=True) == "+$1.00"
    assert mask_wallet_address("0x1234567890abcdef") == "0x1234…cdef"
    assert mask_wallet_address("short") == "short"
    assert normalize_referral_code(" abcd-2345 ") == "ABCD2345"


def test_chart_helpers():
    chart = build_area_chart([0, 100, 50])
    assert chart["max"] == 100
    assert len(chart["line"].split()) == 3
    assert chart["area"].startswith("16,204 16.0,204.0")
    assert build_area_chart([])["max"] == 1


def test_plan_constants_are_consistent():
    assert PLANS["foundation"].min_cents == 10_000
    assert PLANS["meridian"].min_cents == 100_000
    assert PLANS["summit"].min_cents == 500_000
    assert PLANS["meridian"].monthly_rate_percent == "1.25%"
