"""Telegram Bot API — channel invites, member removal, signal posts (backend only)."""
from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from auth_db import (
    _iso,
    _parse_iso,
    _utc_now,
    access_status,
    get_conn,
    get_user_by_id,
)

_POLLER_STOP = threading.Event()
_POLLER_THREAD: threading.Thread | None = None
_LAST_POLL_ERROR: str | None = None


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def bot_token() -> str:
    return _cfg("TELEGRAM_BOT_TOKEN")


def channel_id() -> str:
    return _cfg("TELEGRAM_CHANNEL_ID")


def bot_username() -> str:
    return _cfg("TELEGRAM_BOT_USERNAME", "AlphaFXSignalsBot").lstrip("@")


def telegram_enabled() -> bool:
    return bool(bot_token() and channel_id())


def normalize_username(value: str) -> str:
    u = (value or "").strip().lstrip("@").lower()
    return u


def _api(method: str, payload: dict[str, Any] | None = None, *, timeout: int = 30) -> dict[str, Any]:
    token = bot_token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Telegram HTTP {exc.code}: {raw}") from exc
    if not data.get("ok"):
        desc = data.get("description") or "Telegram API error"
        raise RuntimeError(desc)
    return data.get("result") or {}


def bot_deep_link(start_arg: str) -> str:
    return f"https://t.me/{bot_username()}?start={urllib.parse.quote(start_arg)}"


def _user_telegram_row(user_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, email, telegram_username, telegram_user_id, telegram_linked_at,
                   telegram_channel_joined, telegram_link_code, subscription_expires_at,
                   email_allowed, is_admin
            FROM users WHERE id=?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def telegram_public_status(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"enabled": telegram_enabled(), "status": "none"}
    username = row.get("telegram_username")
    tg_id = row.get("telegram_user_id")
    joined = bool(row.get("telegram_channel_joined"))
    if not telegram_enabled():
        return {
            "enabled": False,
            "status": "disabled",
            "username": username,
        }
    if joined:
        st = "in_channel"
    elif tg_id:
        st = "linked"
    elif username:
        st = "pending_bot"
    else:
        st = "none"
    return {
        "enabled": True,
        "status": st,
        "username": username,
        "linked": bool(tg_id),
        "inChannel": joined,
        "linkedAt": row.get("telegram_linked_at"),
        "botUsername": bot_username(),
    }


def save_telegram_username(user_id: int, username: str) -> tuple[dict[str, Any] | None, str | None]:
    uname = normalize_username(username)
    if not uname or len(uname) < 3:
        return None, "Enter a valid Telegram username (without @)"
    code = secrets.token_urlsafe(16)
    now = _iso(_utc_now())
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET telegram_username=?, telegram_link_code=?, updated_at=?,
                telegram_channel_joined=0
            WHERE id=?
            """,
            (uname, code, now, user_id),
        )
    row = _user_telegram_row(user_id)
    return row, None


def get_user_by_link_code(code: str) -> dict[str, Any] | None:
    if not code:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_link_code=?",
            (code.strip(),),
        ).fetchone()
    return dict(row) if row else None


def link_telegram_account(user_id: int, tg_user_id: int, tg_username: str | None) -> None:
    now = _iso(_utc_now())
    uname = normalize_username(tg_username or "")
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET telegram_user_id=?, telegram_linked_at=?, updated_at=?,
                telegram_username=COALESCE(NULLIF(telegram_username, ''), ?)
            WHERE id=?
            """,
            (tg_user_id, now, now, uname or None, user_id),
        )


def mark_channel_joined(user_id: int, joined: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET telegram_channel_joined=?, updated_at=? WHERE id=?",
            (1 if joined else 0, _iso(_utc_now()), user_id),
        )


def clear_telegram_link(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET telegram_username=NULL, telegram_user_id=NULL, telegram_linked_at=NULL,
                telegram_channel_joined=0, telegram_link_code=NULL, updated_at=?
            WHERE id=?
            """,
            (_iso(_utc_now()), user_id),
        )


def _can_access_channel(user_id: int) -> tuple[bool, str | None]:
    user = get_user_by_id(user_id)
    if not user:
        return False, "user not found"
    st = access_status(user)
    if not st["has_access"]:
        return False, "subscription inactive"
    return True, None


def send_dm(tg_user_id: int, text: str, *, disable_preview: bool = True) -> None:
    _api("sendMessage", {
        "chat_id": tg_user_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    })


def create_personal_invite(user_id: int) -> str:
    """Single-use invite link for the private signal channel."""
    exp = int((_utc_now() + timedelta(hours=48)).timestamp())
    result = _api("createChatInviteLink", {
        "chat_id": channel_id(),
        "name": f"user_{user_id}",
        "member_limit": 1,
        "expire_date": exp,
        "creates_join_request": False,
    })
    link = result.get("invite_link")
    if not link:
        raise RuntimeError("Failed to create invite link")
    return link


def is_member(tg_user_id: int) -> bool:
    try:
        result = _api("getChatMember", {
            "chat_id": channel_id(),
            "user_id": tg_user_id,
        })
        return result.get("status") in ("member", "administrator", "creator")
    except RuntimeError as exc:
        if "user not found" in str(exc).lower() or "not a member" in str(exc).lower():
            return False
        raise


def remove_from_channel(tg_user_id: int) -> None:
    """Ban then unban so user is removed but can rejoin with a new invite later."""
    cid = channel_id()
    _api("banChatMember", {"chat_id": cid, "user_id": tg_user_id})
    try:
        _api("unbanChatMember", {"chat_id": cid, "user_id": tg_user_id, "only_if_banned": True})
    except RuntimeError:
        pass


def add_user_to_channel(user_id: int) -> dict[str, Any]:
    """
    Save username (if needed), link via bot /start, send channel invite.
    Telegram requires user to press Start on the bot before DM invite works.
    """
    if not telegram_enabled():
        return {"ok": False, "error": "Telegram not configured on server"}

    ok, err = _can_access_channel(user_id)
    if not ok:
        return {"ok": False, "error": err}

    row = _user_telegram_row(user_id)
    if not row or not row.get("telegram_username"):
        return {"ok": False, "error": "Set your Telegram username first"}

    code = row.get("telegram_link_code")
    if not code:
        _, err2 = save_telegram_username(user_id, row["telegram_username"])
        if err2:
            return {"ok": False, "error": err2}
        row = _user_telegram_row(user_id)
        code = row.get("telegram_link_code")

    tg_uid = row.get("telegram_user_id")
    if not tg_uid:
        link = bot_deep_link(f"link_{code}")
        return {
            "ok": True,
            "status": "pending_bot",
            "message": (
                f"Open @{bot_username()} in Telegram and tap Start. "
                "We will send your private channel invite automatically."
            ),
            "botLink": link,
            "botUsername": bot_username(),
        }

    if is_member(int(tg_uid)):
        mark_channel_joined(user_id, True)
        return {
            "ok": True,
            "status": "in_channel",
            "message": "You are already in the signal channel.",
        }

    invite = create_personal_invite(user_id)
    send_dm(
        int(tg_uid),
        (
            "<b>AlphaANALYSER</b> — private signal channel\n\n"
            f"Tap to join (single-use link):\n{invite}\n\n"
            "Link expires in 48 hours."
        ),
    )
    sync_membership(user_id)
    return {
        "ok": True,
        "status": "invite_sent",
        "message": "Invite link sent to your Telegram. Tap it to join the channel.",
    }


def sync_membership(user_id: int) -> bool:
    row = _user_telegram_row(user_id)
    if not row or not row.get("telegram_user_id"):
        return False
    try:
        joined = is_member(int(row["telegram_user_id"]))
    except RuntimeError:
        joined = False
    mark_channel_joined(user_id, joined)
    return joined


def remove_user_from_channel(user_id: int, *, reason: str = "removed") -> dict[str, Any]:
    if not telegram_enabled():
        return {"ok": False, "error": "Telegram not configured"}
    row = _user_telegram_row(user_id)
    if not row:
        return {"ok": False, "error": "user not found"}
    tg_uid = row.get("telegram_user_id")
    if tg_uid:
        try:
            if is_member(int(tg_uid)):
                remove_from_channel(int(tg_uid))
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
    mark_channel_joined(user_id, False)
    return {"ok": True, "message": f"Removed from channel ({reason})"}


def process_bot_start(tg_user_id: int, tg_username: str | None, start_arg: str) -> str:
    """Handle /start link_CODE from Telegram polling."""
    arg = (start_arg or "").strip()
    if not arg.startswith("link_"):
        return (
            "Welcome to AlphaANALYSER signals.\n"
            "Link your account from the dashboard Configure → Telegram section."
        )
    code = arg[5:]
    user = get_user_by_link_code(code)
    if not user:
        return "Invalid or expired link. Re-save your username in Configure and try again."

    link_telegram_account(int(user["id"]), tg_user_id, tg_username)
    ok, err = _can_access_channel(int(user["id"]))
    if not ok:
        return f"Account linked, but subscription is inactive ({err}). Renew to receive channel access."

    try:
        result = add_user_to_channel(int(user["id"]))
        if result.get("ok"):
            return result.get("message") or "Linked! Check your invite message."
        return result.get("error") or "Could not add to channel"
    except RuntimeError as exc:
        return f"Linked, but invite failed: {exc}"


def purge_expired_channel_members() -> int:
    """Remove users whose subscription ended from the Telegram channel."""
    if not telegram_enabled():
        return 0
    now = _utc_now()
    removed = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, email, telegram_user_id, subscription_expires_at, email_allowed, is_admin
            FROM users
            WHERE telegram_channel_joined = 1 AND telegram_user_id IS NOT NULL
            """
        ).fetchall()
    for row in rows:
        user = {
            "id": row["id"],
            "email": row["email"],
            "is_admin": bool(row["is_admin"]),
            "email_allowed": bool(row["email_allowed"]),
            "subscription_expires_at": row["subscription_expires_at"],
        }
        if access_status(user)["has_access"]:
            continue
        try:
            remove_user_from_channel(int(row["id"]), reason="subscription expired")
            removed += 1
        except Exception:
            pass
    return removed


def format_signal_message(trade_signal: dict[str, Any], signal_id: str | None = None) -> str:
    action = trade_signal.get("action", "?")
    sym = trade_signal.get("symbol", "?")
    tier = trade_signal.get("confidenceTier", "")
    lines = [
        f"<b>AlphaANALYSER · {sym}</b>",
    ]
    if signal_id:
        lines.append(f"<code>{signal_id}</code>")
    lines.extend([
        f"<b>{action}</b> · {trade_signal.get('orderType', 'MARKET')} · {tier}",
        f"Entry: <code>{trade_signal.get('entryFmt') or trade_signal.get('entry')}</code>",
        f"SL: <code>{trade_signal.get('slFmt') or trade_signal.get('sl')}</code>",
        f"TP1: <code>{trade_signal.get('tp1Fmt') or trade_signal.get('tp1')}</code>",
    ])
    tp2 = trade_signal.get("tp2Fmt") or trade_signal.get("tp2")
    if tp2:
        lines.append(f"TP2: <code>{tp2}</code>")
    plan = trade_signal.get("tradePlan") or {}
    if plan.get("summary"):
        lines.append(f"\n<i>{plan['summary']}</i>")
    if trade_signal.get("scenario"):
        lines.append(f"\n{trade_signal['scenario']}")
    return "\n".join(lines)


def post_signal_to_channel(trade_signal: dict[str, Any], signal_id: str | None = None) -> bool:
    if not telegram_enabled() or not trade_signal.get("action"):
        return False
    try:
        _api("sendMessage", {
            "chat_id": channel_id(),
            "text": format_signal_message(trade_signal, signal_id),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        return True
    except RuntimeError:
        return False


def _handle_update(update: dict[str, Any]) -> None:
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    if not text.startswith("/start"):
        return
    from_user = msg.get("from") or {}
    tg_uid = from_user.get("id")
    if not tg_uid:
        return
    parts = text.split(maxsplit=1)
    start_arg = parts[1] if len(parts) > 1 else ""
    reply = process_bot_start(int(tg_uid), from_user.get("username"), start_arg)
    try:
        send_dm(int(tg_uid), reply)
    except RuntimeError:
        pass


def _poll_loop() -> None:
    global _LAST_POLL_ERROR
    if not telegram_enabled():
        return
    offset = 0
    while not _POLLER_STOP.is_set():
        try:
            result = _api("getUpdates", {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message"],
            }, timeout=40)
            if isinstance(result, list):
                for upd in result:
                    offset = max(offset, int(upd.get("update_id", 0)) + 1)
                    try:
                        _handle_update(upd)
                    except Exception:
                        pass
            _LAST_POLL_ERROR = None
        except Exception as exc:
            _LAST_POLL_ERROR = str(exc)
            _POLLER_STOP.wait(10)


def start_telegram_poller() -> None:
    global _POLLER_THREAD
    if not telegram_enabled():
        print("telegram: disabled (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID)")
        return
    if _POLLER_THREAD and _POLLER_THREAD.is_alive():
        return
    _POLLER_THREAD = threading.Thread(target=_poll_loop, name="telegram-poller", daemon=True)
    _POLLER_THREAD.start()
    print(f"telegram: poller started · channel {channel_id()} · @{bot_username()}")


def start_subscription_guard(interval_sec: int = 120) -> None:
    def _loop() -> None:
        while not _POLLER_STOP.is_set():
            try:
                n = purge_expired_channel_members()
                if n:
                    print(f"telegram: removed {n} expired member(s) from channel")
            except Exception:
                pass
            _POLLER_STOP.wait(interval_sec)

    threading.Thread(target=_loop, name="telegram-guard", daemon=True).start()
