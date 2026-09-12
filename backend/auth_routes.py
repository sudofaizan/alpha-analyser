"""Auth, subscription, and admin API routes."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable

import jwt
from flask import Blueprint, jsonify, request

from auth_db import (
    access_status,
    create_user,
    delete_user,
    get_user_by_email,
    get_user_by_id,
    list_users,
    public_user,
    update_user_subscription,
    verify_password,
)
from signal_tracker import get_admin_tracking_dashboard

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

JWT_ALG = "HS256"


def _jwt_secret() -> str:
    return os.environ.get("FLASK_SECRET_KEY", "change-me-set-FLASK_SECRET_KEY")


def _jwt_days() -> int:
    return int(os.environ.get("AUTH_TOKEN_DAYS", "30"))


def _issue_token(user: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "is_admin": user["is_admin"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=_jwt_days())).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def _decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None


def _bearer_user() -> tuple[dict[str, Any] | None, tuple | None]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, (jsonify({"ok": False, "error": "authorization required"}), 401)
    payload = _decode_token(auth[7:].strip())
    if not payload:
        return None, (jsonify({"ok": False, "error": "invalid or expired token"}), 401)
    user = get_user_by_id(int(payload["sub"]))
    if not user:
        return None, (jsonify({"ok": False, "error": "user not found"}), 401)
    return user, None


def login_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, err = _bearer_user()
        if err:
            return err
        return view(user, *args, **kwargs)

    return wrapped


def admin_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, err = _bearer_user()
        if err:
            return err
        if not user.get("is_admin"):
            return jsonify({"ok": False, "error": "admin only"}), 403
        return view(user, *args, **kwargs)

    return wrapped


def subscription_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, err = _bearer_user()
        if err:
            return err
        status = access_status(user)
        if not status["has_access"]:
            return jsonify({
                "ok": False,
                "error": "subscription inactive",
                "access_reason": status["reason"],
            }), 403
        return view(user, *args, **kwargs)

    return wrapped


@auth_bp.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip()
    password = str(data.get("password", ""))
    user, err = create_user(email, password)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    token = _issue_token(user)
    return jsonify({"ok": True, "token": token, "user": public_user(user)})


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    user = get_user_by_email(email)
    if not user or not verify_password(user, password):
        return jsonify({"ok": False, "error": "invalid email or password"}), 401
    token = _issue_token(user)
    return jsonify({"ok": True, "token": token, "user": public_user(user)})


@auth_bp.route("/me", methods=["GET"])
@login_required
def me(user):
    return jsonify({"ok": True, "user": public_user(user)})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    return jsonify({"ok": True})


@admin_bp.route("/users", methods=["GET"])
@admin_required
def admin_list_users(_admin):
    return jsonify({"ok": True, "users": list_users()})


@admin_bp.route("/users", methods=["POST"])
@admin_required
def admin_create_user(_admin):
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")) or "changeme123"
    email_allowed = bool(data.get("email_allowed", True))
    subscription_expires_at = data.get("subscription_expires_at")
    existing = get_user_by_email(email)
    if existing:
        user, err = update_user_subscription(
            existing["id"],
            email_allowed=email_allowed,
            subscription_expires_at=subscription_expires_at,
        )
        if err:
            return jsonify({"ok": False, "error": err}), 400
        return jsonify({"ok": True, "user": user, "updated": True})
    user, err = create_user(
        email,
        password,
        email_allowed=email_allowed,
        subscription_expires_at=subscription_expires_at,
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True, "user": public_user(user), "created": True})


@admin_bp.route("/users/<int:user_id>", methods=["PATCH"])
@admin_required
def admin_update_user(_admin, user_id: int):
    data = request.get_json(silent=True) or {}
    kwargs: dict[str, Any] = {}
    if "email_allowed" in data:
        kwargs["email_allowed"] = bool(data["email_allowed"])
    if "subscription_expires_at" in data:
        kwargs["subscription_expires_at"] = data["subscription_expires_at"]
    user, err = update_user_subscription(user_id, **kwargs)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    from auth_db import access_status, get_user_by_id
    from telegram_service import remove_user_from_channel

    refreshed = get_user_by_id(user_id)
    if refreshed and not access_status(refreshed)["has_access"]:
        remove_user_from_channel(user_id, reason="subscription updated")
    return jsonify({"ok": True, "user": user})


@admin_bp.route("/users/<int:user_id>/telegram/remove", methods=["POST"])
@admin_required
def admin_remove_telegram(_admin, user_id: int):
    from telegram_service import clear_telegram_link

    result = remove_user_from_channel(user_id, reason="admin removed")
    if result.get("ok"):
        clear_telegram_link(user_id)
    return jsonify(result), (200 if result.get("ok") else 400)


@admin_bp.route("/users/<int:user_id>", methods=["DELETE"])
@admin_required
def admin_delete_user(_admin, user_id: int):
    from telegram_service import remove_user_from_channel

    remove_user_from_channel(user_id, reason="user deleted")
    ok, err = delete_user(user_id)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@admin_bp.route("/tracking", methods=["GET"])
@admin_required
def admin_tracking(_admin):
    limit = max(5, min(int(request.args.get("recent", 25)), 100))
    return jsonify({"ok": True, **get_admin_tracking_dashboard(limit)})
