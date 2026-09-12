"""Telegram Bot API — channel invites, member removal, signal posts (backend only)."""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
_POLLER_LOCK_FD = None
_LOCK_PATH = Path(__file__).resolve().parent / ".telegram_poller.lock"


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
                   telegram_channel_joined, telegram_link_code, telegram_invite_link,
                   subscription_expires_at, email_allowed, is_admin
            FROM users WHERE id=?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def telegram_public_status(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"enabled": telegram_enabled(), "status": "none"}
    user_id = row.get("id")
    if user_id:
        fresh = _user_telegram_row(int(user_id)) or row
        if fresh.get("telegram_user_id"):
            try:
                sync_membership(int(user_id))
                fresh = _user_telegram_row(int(user_id)) or fresh
            except Exception:
                pass
        row = fresh
    username = row.get("telegram_username")
    tg_id = row.get("telegram_user_id")
    joined = bool(row.get("telegram_channel_joined"))
    code = row.get("telegram_link_code")
    if not telegram_enabled():
        return {
            "enabled": False,
            "status": "disabled",
            "username": username,
            "pollerError": _LAST_POLL_ERROR,
        }
    invite_link = row.get("telegram_invite_link")
    if joined:
        st = "in_channel"
    elif invite_link and username:
        st = "invite_sent"
    elif username:
        st = "awaiting_invite"
    else:
        st = "none"
    direct = _direct_add_status()
    out = {
        "enabled": True,
        "status": st,
        "username": username,
        "linked": bool(tg_id),
        "inChannel": joined,
        "linkedAt": row.get("telegram_linked_at"),
        "botUsername": bot_username(),
        "pollerError": _LAST_POLL_ERROR,
        "directAddAvailable": direct.get("configured", False),
        "directAddReady": direct.get("authorized", False),
    }
    if invite_link and not joined:
        out["channelInviteLink"] = invite_link
    return out


def _direct_add_status() -> dict[str, Any]:
    try:
        from telegram_user_client import user_client_status

        return user_client_status()
    except Exception:
        return {"configured": False, "authorized": False}


def telegram_health_report() -> dict[str, Any]:
    """Server-side Telegram diagnostics (no secrets). Safe for admin API + CLI."""
    from telegram_user_client import session_path, user_client_configured

    sp = session_path()
    session_file = Path(str(sp) + ".session")
    sf_exists = session_file.is_file()
    sf_readable = os.access(session_file, os.R_OK) if sf_exists else False

    report: dict[str, Any] = {
        "ready": False,
        "bot": {
            "configured": telegram_enabled(),
            "tokenSet": bool(bot_token()),
            "channelIdSet": bool(channel_id()),
            "botUsername": bot_username(),
        },
        "directAdd": {
            "configured": user_client_configured(),
            "sessionPath": str(session_file),
            "sessionExists": sf_exists,
            "sessionReadable": sf_readable,
        },
        "pollerError": _LAST_POLL_ERROR,
        "checks": [],
    }

    def add_check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})

    if not report["bot"]["configured"]:
        add_check("bot env", False, "Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID in .env")
    else:
        add_check("bot env", True, "Token and channel id present")
        try:
            me = _api("getMe")
            add_check("bot token valid", True, f"@{me.get('username')} (id {me.get('id')})")
            report["bot"]["alive"] = True
            report["bot"]["id"] = me.get("id")
            bot_id = me.get("id")
        except RuntimeError as exc:
            add_check("bot token valid", False, str(exc))
            bot_id = None

        if bot_id:
            try:
                chat = _api("getChat", {"chat_id": channel_id()})
                add_check(
                    "channel reachable",
                    True,
                    f"{chat.get('title')} ({chat.get('type')})",
                )
            except RuntimeError as exc:
                add_check("channel reachable", False, str(exc))

            try:
                member = _api("getChatMember", {
                    "chat_id": channel_id(),
                    "user_id": bot_id,
                })
                status = member.get("status", "?")
                can_invite = member.get("can_invite_users", status in ("administrator", "creator"))
                add_check(
                    "bot is channel admin",
                    status in ("administrator", "creator"),
                    f"status={status}, can_invite={can_invite}",
                )
            except RuntimeError as exc:
                add_check("bot is channel admin", False, str(exc))

    raw_session = _cfg("TELEGRAM_USER_SESSION", ".telegram_user")
    if raw_session.startswith("~"):
        report["directAdd"]["sessionPathWarning"] = (
            f"TELEGRAM_USER_SESSION={raw_session} expands per user — "
            f"gunicorn (root) uses {Path(raw_session).expanduser()}, not ec2-user's home. "
            "Use absolute path: TELEGRAM_USER_SESSION=/home/ec2-user/telegram_user"
        )

    da = report["directAdd"]
    if not da["configured"]:
        add_check("direct add (user API)", False, "Optional — set TELEGRAM_USER_API_ID/HASH")
    else:
        add_check("direct add env", True, "API id/hash set")
        if report["directAdd"].get("sessionPathWarning"):
            add_check("session path (no ~)", False, report["directAdd"]["sessionPathWarning"])
        if not da["sessionExists"]:
            add_check(
                "user session logged in",
                False,
                "Run: sudo .venv/bin/python setup_telegram_user.py",
            )
        elif not da["sessionReadable"]:
            add_check(
                "user session readable",
                False,
                f"chmod/chown so gunicorn (root) can read {session_file}",
            )
        else:
            st = _direct_add_status()
            da.update(st)
            if st.get("authorized"):
                add_check(
                    "user session logged in",
                    True,
                    f"@{st.get('username')} (id {st.get('userId')})",
                )
            else:
                add_check(
                    "user session logged in",
                    False,
                    st.get("error") or "Run sudo .venv/bin/python setup_telegram_user.py",
                )

    failed = [c for c in report["checks"] if not c["ok"]]
    required_failed = [
        c for c in failed
        if c["name"] not in ("direct add (user API)", "direct add env", "user session logged in", "user session readable")
    ]
    report["ready"] = not required_failed
    report["directAddReady"] = bool(da.get("authorized"))
    return report


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


def find_pending_user_by_tg_username(tg_username: str | None) -> dict[str, Any] | None:
    """Match /start from bot when user opened bot directly (no deep-link code)."""
    uname = normalize_username(tg_username or "")
    if not uname:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM users
            WHERE telegram_username=? AND telegram_user_id IS NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (uname,),
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
    now = _iso(_utc_now())
    with get_conn() as conn:
        if joined:
            conn.execute(
                """
                UPDATE users
                SET telegram_channel_joined=1, telegram_invite_link=NULL, updated_at=?
                WHERE id=?
                """,
                (now, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET telegram_channel_joined=0, updated_at=? WHERE id=?",
                (now, user_id),
            )


def save_invite_link(user_id: int, link: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET telegram_invite_link=?, updated_at=? WHERE id=?",
            (link, _iso(_utc_now()), user_id),
        )


def clear_telegram_link(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET telegram_username=NULL, telegram_user_id=NULL, telegram_linked_at=NULL,
                telegram_channel_joined=0, telegram_link_code=NULL, telegram_invite_link=NULL,
                updated_at=?
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


def _telegram_not_in_chat(exc: RuntimeError) -> bool:
    """User not in channel yet — normal before they accept an invite."""
    s = str(exc).lower()
    return any(
        phrase in s
        for phrase in (
            "user not found",
            "member not found",
            "not a member",
            "participant_id_invalid",
            "chat not found",
        )
    )


def is_member(tg_user_id: int) -> bool:
    try:
        result = _api("getChatMember", {
            "chat_id": channel_id(),
            "user_id": tg_user_id,
        })
        return result.get("status") in ("member", "administrator", "creator")
    except RuntimeError as exc:
        if _telegram_not_in_chat(exc):
            return False
        raise


def remove_from_channel(tg_user_id: int) -> None:
    """Ban then unban so user is removed but can rejoin with a new invite later."""
    cid = channel_id()
    try:
        _api("banChatMember", {"chat_id": cid, "user_id": tg_user_id})
    except RuntimeError as exc:
        if not _telegram_not_in_chat(exc):
            raise
    try:
        _api("unbanChatMember", {"chat_id": cid, "user_id": tg_user_id, "only_if_banned": True})
    except RuntimeError:
        pass


def _try_direct_add(user_id: int, username: str) -> dict[str, Any] | None:
    """Use admin Telegram account to add @username directly. None → use invite link."""
    try:
        from telegram_user_client import DirectInviteError, direct_invite_to_channel, user_client_configured
    except ImportError:
        return None
    if not user_client_configured():
        return None
    try:
        tg_uid = direct_invite_to_channel(username)
        link_telegram_account(user_id, tg_uid, username)
        mark_channel_joined(user_id, True)
        return {
            "ok": True,
            "status": "in_channel",
            "message": f"Added @{username} to the signal channel.",
            "directAdd": True,
        }
    except DirectInviteError as exc:
        if exc.fallback_invite:
            return None
        return {"ok": False, "error": str(exc)}
    except RuntimeError as exc:
        msg = str(exc)
        if "not logged in" in msg.lower() or "session" in msg.lower():
            return None
        return {"ok": False, "error": msg}


def add_user_to_channel(user_id: int) -> dict[str, Any]:
    """
    Add subscriber to the signal channel.
    Prefers direct add via admin Telegram account (MTProto) when configured;
    otherwise issues a single-use invite link (no bot /start required).
    """
    if not telegram_enabled():
        return {"ok": False, "error": "Telegram not configured on server"}

    ok, err = _can_access_channel(user_id)
    if not ok:
        return {"ok": False, "error": err}

    row = _user_telegram_row(user_id)
    if not row or not row.get("telegram_username"):
        return {"ok": False, "error": "Set your Telegram username first"}

    username = row["telegram_username"]
    if not row.get("telegram_link_code"):
        _, err2 = save_telegram_username(user_id, username)
        if err2:
            return {"ok": False, "error": err2}
        row = _user_telegram_row(user_id)

    tg_uid = row.get("telegram_user_id")
    if tg_uid:
        try:
            if is_member(int(tg_uid)):
                mark_channel_joined(user_id, True)
                return {
                    "ok": True,
                    "status": "in_channel",
                    "message": "You are already in the signal channel.",
                }
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}

    direct = _try_direct_add(user_id, username)
    if direct is not None:
        return direct

    try:
        invite = create_personal_invite(user_id)
        save_invite_link(user_id, invite)
    except RuntimeError as exc:
        msg = str(exc)
        if "not enough rights" in msg.lower() or "administrator" in msg.lower():
            msg = "Bot must be channel admin with invite permission"
        return {"ok": False, "error": msg}

    if tg_uid:
        try:
            send_dm(
                int(tg_uid),
                (
                    "<b>AlphaANALYSER</b> — private signal channel\n\n"
                    f"Tap to join (single-use link):\n{invite}\n\n"
                    "Link expires in 48 hours."
                ),
            )
        except RuntimeError:
            pass

    sync_membership(user_id)
    return {
        "ok": True,
        "status": "invite_sent",
        "message": "Direct add unavailable — tap the channel link below to join.",
        "channelInviteLink": invite,
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
            removed = False
            try:
                from telegram_user_client import direct_remove_from_channel, user_client_status

                st = user_client_status()
                if st.get("authorized"):
                    direct_remove_from_channel(int(tg_uid))
                    removed = True
            except Exception:
                pass
            if not removed and is_member(int(tg_uid)):
                remove_from_channel(int(tg_uid))
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
    mark_channel_joined(user_id, False)
    return {"ok": True, "message": f"Removed from channel ({reason})"}


def process_bot_start(tg_user_id: int, tg_username: str | None, start_arg: str) -> str:
    """Handle /start link_CODE from Telegram polling."""
    arg = (start_arg or "").strip()
    user = None
    if arg.startswith("link_"):
        user = get_user_by_link_code(arg[5:])
        if not user:
            return "Invalid or expired link. Click Add to signal channel in Configure again."
    else:
        user = find_pending_user_by_tg_username(tg_username)
        if not user:
            return (
                f"Welcome to AlphaANALYSER.\n\n"
                f"1) Open Configure on analyser.alphafx.org\n"
                f"2) Enter your Telegram username (@{tg_username or '?'})\n"
                f"3) Click Add to signal channel\n"
                f"4) Open the bot link shown and tap Start"
            )

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
    try:
        _api("deleteWebhook", {"drop_pending_updates": False})
    except Exception as exc:
        _LAST_POLL_ERROR = f"deleteWebhook: {exc}"
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
    global _POLLER_THREAD, _POLLER_LOCK_FD
    if not telegram_enabled():
        print("telegram: disabled (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHANNEL_ID)")
        return
    if _POLLER_THREAD and _POLLER_THREAD.is_alive():
        return
    # Only one poller across gunicorn workers (getUpdates allows one consumer).
    try:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _POLLER_LOCK_FD = open(_LOCK_PATH, "w")
        fcntl.flock(_POLLER_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("telegram: poller already running in another worker")
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
