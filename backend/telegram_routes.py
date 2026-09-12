"""Telegram linking API — all Bot calls server-side."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from auth_routes import admin_required, login_required, subscription_required
from telegram_service import (
    add_user_to_channel,
    remove_user_from_channel,
    save_telegram_username,
    telegram_enabled,
    telegram_public_status,
)

telegram_bp = Blueprint("telegram", __name__, url_prefix="/api/telegram")


@telegram_bp.route("/status", methods=["GET"])
@login_required
def telegram_status(user):
    from telegram_service import _user_telegram_row

    row = _user_telegram_row(user["id"]) or user
    return jsonify({
        "ok": True,
        "telegram": telegram_public_status(row),
        "configured": telegram_enabled(),
    })


@telegram_bp.route("/connect", methods=["POST"])
@subscription_required
def telegram_connect(user):
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    if not username:
        username = user.get("telegram_username") or ""
    if username:
        _, err = save_telegram_username(user["id"], username)
        if err:
            return jsonify({"ok": False, "error": err}), 400
    result = add_user_to_channel(user["id"])
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@telegram_bp.route("/disconnect", methods=["POST"])
@login_required
def telegram_disconnect(user):
    result = remove_user_from_channel(user["id"], reason="user disconnect")
    if result.get("ok"):
        from telegram_service import clear_telegram_link

        clear_telegram_link(user["id"])
    return jsonify(result), (200 if result.get("ok") else 400)
