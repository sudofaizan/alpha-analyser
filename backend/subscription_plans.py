"""Subscription plans, referral codes, and mock checkout (real payments later)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from auth_db import _iso, _parse_iso, _utc_now, get_conn, get_user_by_id

LIFETIME_EXPIRY = datetime(2099, 12, 31, tzinfo=timezone.utc)

PLANS: dict[str, dict[str, Any]] = {
    "daily": {
        "id": "daily",
        "label": "1 Day",
        "price_usd": 5.0,
        "currency": "USDT",
        "days": 1,
    },
    "weekly": {
        "id": "weekly",
        "label": "1 Week",
        "price_usd": 15.0,
        "currency": "USDT",
        "days": 7,
    },
    "monthly": {
        "id": "monthly",
        "label": "1 Month",
        "price_usd": 40.0,
        "currency": "USDT",
        "days": 30,
    },
    "lifetime": {
        "id": "lifetime",
        "label": "Lifetime",
        "price_usd": 499.0,
        "currency": "USDT",
        "days": None,
    },
}

REFERRAL_CODES: dict[str, dict[str, Any]] = {
    "ALPHAFX100": {
        "discount_percent": 100,
        "allowed_plans": frozenset({"daily", "weekly"}),
        "label": "100% off day & week plans",
    },
}


def normalize_referral(code: str | None) -> str:
    return (code or "").strip().upper()


def list_plans_public() -> list[dict[str, Any]]:
    out = []
    for p in PLANS.values():
        out.append({
            "id": p["id"],
            "label": p["label"],
            "price_usd": p["price_usd"],
            "currency": p["currency"],
            "days": p["days"],
        })
    return out


def quote_subscription(plan_id: str, referral_code: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    plan = PLANS.get(plan_id)
    if not plan:
        return None, "invalid plan"

    original = float(plan["price_usd"])
    amount = original
    referral = normalize_referral(referral_code)
    referral_applied = None
    discount_percent = 0

    if referral:
        ref = REFERRAL_CODES.get(referral)
        if not ref:
            return None, "invalid referral code"
        if plan_id not in ref["allowed_plans"]:
            return None, f"Referral {referral} only applies to day and week plans"
        discount_percent = int(ref["discount_percent"])
        amount = round(original * (1 - discount_percent / 100), 2)
        referral_applied = referral

    return {
        "plan_id": plan_id,
        "plan_label": plan["label"],
        "currency": plan["currency"],
        "original_price_usd": original,
        "price_usd": amount,
        "discount_percent": discount_percent,
        "referral_code": referral_applied,
        "free": amount <= 0,
    }, None


def expiry_for_plan(plan_id: str, *, base: datetime | None = None) -> datetime:
    plan = PLANS[plan_id]
    if plan_id == "lifetime" or plan.get("days") is None:
        return LIFETIME_EXPIRY
    start = base or _utc_now()
    return start + timedelta(days=int(plan["days"]))


def extend_subscription_expiry(user_id: int, plan_id: str) -> datetime:
    """Stack onto active subscription or start from now."""
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("user not found")
    if plan_id == "lifetime":
        return LIFETIME_EXPIRY
    now = _utc_now()
    current = _parse_iso(user.get("subscription_expires_at"))
    base = now
    if current and current > now:
        base = current
    return expiry_for_plan(plan_id, base=base)


def record_mock_payment(
    user_id: int,
    plan_id: str,
    amount_usd: float,
    referral_code: str | None,
) -> None:
    now = _iso(_utc_now())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO payments (user_id, plan_id, amount_usd, referral_code, status, mock, created_at)
            VALUES (?, ?, ?, ?, 'completed', 1, ?)
            """,
            (user_id, plan_id, amount_usd, referral_code, now),
        )


def activate_subscription(
    user_id: int,
    plan_id: str,
    referral_code: str | None = None,
    *,
    mock_pay: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    quote, err = quote_subscription(plan_id, referral_code)
    if err or not quote:
        return None, err or "invalid plan"

    if not mock_pay:
        return None, "payment gateway not configured yet"

    try:
        exp = extend_subscription_expiry(user_id, plan_id)
    except ValueError as exc:
        return None, str(exc)

    now = _iso(_utc_now())
    ref = quote.get("referral_code")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET email_allowed=1, subscription_expires_at=?, subscription_plan=?,
                referral_code_used=COALESCE(?, referral_code_used), updated_at=?
            WHERE id=?
            """,
            (_iso(exp), plan_id, ref, now, user_id),
        )

    record_mock_payment(user_id, plan_id, float(quote["price_usd"]), ref)
    return get_user_by_id(user_id), None
