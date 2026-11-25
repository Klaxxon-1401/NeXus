"""Blueprint-independent utilities: money, time, formatting and charts."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

_CURRENCY_RE = re.compile(r"^\d{1,9}(?:\.\d{1,2})?$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REFERRAL_RE = re.compile(r"^[A-Z0-9]{4,16}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_today() -> date:
    return utcnow().date()


def clean_text(value: str, *, max_length: int | None = None) -> str:
    """Trim and collapse whitespace, removing control characters."""
    cleaned = _CONTROL_RE.sub("", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if max_length is not None:
        cleaned = cleaned[:max_length]
    return cleaned


def normalize_email(value: str) -> str:
    return clean_text(value or "").casefold()


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(value or "")) and len(value) <= 255


def normalize_referral_code(value: str) -> str:
    return clean_text(value or "").replace("-", "").upper()


def is_valid_referral_code(value: str) -> bool:
    return bool(_REFERRAL_RE.fullmatch(value or ""))


def parse_money_to_cents(value: object) -> int:
    """Parse a user-supplied decimal amount without float arithmetic.

    Accepts ``1,250.50`` or ``1250.5`` and rejects more than two decimal
    places. The result is exact integer cents.
    """
    if value is None:
        raise ValueError("Enter an amount.")
    raw = str(value).strip().replace(",", "")
    if not raw:
        raise ValueError("Enter an amount.")
    if not _CURRENCY_RE.fullmatch(raw):
        raise ValueError("Enter a valid amount with up to two decimal places.")
    try:
        decimal_amount = Decimal(raw)
    except InvalidOperation as exc:  # pragma: no cover - regex guards this
        raise ValueError("Enter a valid amount.") from exc
    cents = (decimal_amount * 100).to_integral_value()
    if cents <= 0:
        raise ValueError("Amount must be greater than zero.")
    return int(cents)


def basis_points(amount_cents: int, bps: int) -> int:
    """Return ``amount * bps / 10,000`` rounded half-up to whole cents."""
    if amount_cents < 0 or bps < 0:
        raise ValueError("Amounts and rates cannot be negative.")
    return (amount_cents * bps + 5_000) // 10_000


def add_months(value: date, months: int) -> date:
    """Add calendar months, clamping to the last valid day of the target month."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def format_money(cents: int | None, *, with_sign: bool = False) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ("+" if with_sign and cents > 0 else "")
    absolute = abs(int(cents))
    return f"{sign}${absolute // 100:,}.{absolute % 100:02d}"


def format_percent_from_bps(bps: int) -> str:
    return f"{bps / 100:.2f}%"


def format_date(value: date | datetime | None, fmt: str = "%b %d, %Y") -> str:
    if value is None:
        return "—"
    return value.strftime(fmt)


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%b %d, %Y · %H:%M UTC")


def mask_wallet_address(address: str | None) -> str:
    if not address:
        return "—"
    if len(address) <= 12:
        return address
    return f"{address[:6]}…{address[-4:]}"


def slugify(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:70] or "workspace"


def safe_redirect_target(target: str | None) -> str | None:
    """Allow only local absolute paths to prevent open redirects."""
    if not target:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target or "\r" in target or "\n" in target:
        return None
    return target


# ---------------------------------------------------------------------------
# Lightweight, dependency-free SVG chart helpers
# ---------------------------------------------------------------------------


def build_area_chart(
    values: Sequence[int],
    *,
    width: int = 720,
    height: int = 220,
    padding: int = 16,
) -> dict[str, str | int]:
    """Build SVG polyline/polygon points for a simple area chart.

    The chart is decorative; every value is also available in the adjacent
    schedule and accessible summary so it is not the only source of meaning.
    """
    points_values = list(values) if values else [0]
    if len(points_values) == 1:
        points_values = points_values * 2
    maximum = max(points_values) or 1
    usable_width = width - padding * 2
    usable_height = height - padding * 2
    step = usable_width / (len(points_values) - 1)

    coords: list[tuple[float, float]] = []
    for index, value in enumerate(points_values):
        x = padding + index * step
        y = height - padding - (max(0, value) / maximum) * usable_height
        coords.append((round(x, 2), round(y, 2)))

    line_points = " ".join(f"{x},{y}" for x, y in coords)
    area_points = (
        f"{padding},{height - padding} "
        + line_points
        + f" {padding + usable_width},{height - padding}"
    )
    return {
        "line": line_points,
        "area": area_points,
        "width": width,
        "height": height,
        "baseline": height - padding,
        "max": maximum,
    }


def chunks(iterable: Iterable, size: int) -> Iterable[list]:
    bucket: list = []
    for item in iterable:
        bucket.append(item)
        if len(bucket) == size:
            yield bucket
            bucket = []
    if bucket:
        yield bucket
