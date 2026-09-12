"""Telegram user-account client (MTProto) — direct channel add/remove by @username.

Requires a one-time login: python setup_telegram_user.py
Your account must be admin of the signal channel with permission to add members.
"""
from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

_CLIENT_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None

_BACKEND_DIR = Path(__file__).resolve().parent


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def user_client_configured() -> bool:
    return bool(_cfg("TELEGRAM_USER_API_ID") and _cfg("TELEGRAM_USER_API_HASH"))


def session_path() -> Path:
    raw = _cfg("TELEGRAM_USER_SESSION", ".telegram_user")
    p = Path(raw)
    return p if p.is_absolute() else _BACKEND_DIR / raw


def _channel_id_int() -> int:
    raw = _cfg("TELEGRAM_CHANNEL_ID")
    if not raw:
        raise RuntimeError("TELEGRAM_CHANNEL_ID not configured")
    return int(raw)


def _run(coro: Any) -> Any:
    global _LOOP
    with _CLIENT_LOCK:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _LOOP = loop
        return loop.run_until_complete(coro)


class DirectInviteError(Exception):
    def __init__(self, message: str, *, fallback_invite: bool = False):
        super().__init__(message)
        self.fallback_invite = fallback_invite


async def _client():
    from telethon import TelegramClient

    api_id = int(_cfg("TELEGRAM_USER_API_ID"))
    api_hash = _cfg("TELEGRAM_USER_API_HASH")
    path = str(session_path())
    client = TelegramClient(path, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telegram user session not logged in. On the server run: "
            "cd backend && source .venv/bin/activate && python setup_telegram_user.py"
        )
    return client


async def _direct_invite_async(username: str) -> int:
    from telethon.errors import (
        ChatAdminRequiredError,
        FloodWaitError,
        UserAlreadyParticipantError,
        UserNotMutualContactError,
        UserPrivacyRestrictedError,
        UsernameInvalidError,
        UsernameNotOccupiedError,
    )
    from telethon.tl.functions.channels import InviteToChannelRequest

    uname = (username or "").strip().lstrip("@")
    if len(uname) < 3:
        raise DirectInviteError("Invalid Telegram username")

    client = await _client()
    try:
        user = await client.get_entity(uname)
        channel = await client.get_entity(_channel_id_int())
        try:
            await client(InviteToChannelRequest(channel, [user]))
        except UserAlreadyParticipantError:
            pass
        return int(user.id)
    except UsernameNotOccupiedError:
        raise DirectInviteError(f"Telegram username @{uname} not found") from None
    except UsernameInvalidError:
        raise DirectInviteError(f"Invalid Telegram username @{uname}") from None
    except UserPrivacyRestrictedError:
        raise DirectInviteError(
            "User privacy blocks direct add — use invite link instead",
            fallback_invite=True,
        ) from None
    except UserNotMutualContactError:
        raise DirectInviteError(
            "Cannot add non-contact directly — use invite link instead",
            fallback_invite=True,
        ) from None
    except ChatAdminRequiredError:
        raise DirectInviteError(
            "Your Telegram account needs channel admin + add members permission"
        ) from None
    except FloodWaitError as exc:
        raise DirectInviteError(f"Telegram rate limit — retry in {exc.seconds}s") from exc
    finally:
        await client.disconnect()


async def _direct_remove_async(tg_user_id: int) -> None:
    from telethon.errors import ChatAdminRequiredError, UserNotParticipantError
    from telethon.tl.functions.channels import EditBannedRequest
    from telethon.tl.types import ChatBannedRights

    client = await _client()
    try:
        channel = await client.get_entity(_channel_id_int())
        user = await client.get_entity(int(tg_user_id))
        rights = ChatBannedRights(until_date=None, view_messages=True)
        try:
            await client(EditBannedRequest(channel, user, rights))
        except UserNotParticipantError:
            return
        # Unban so they can rejoin later
        await client(
            EditBannedRequest(
                channel,
                user,
                ChatBannedRights(until_date=None, view_messages=False),
            )
        )
    except ChatAdminRequiredError:
        raise DirectInviteError("Your Telegram account needs channel admin permission") from None
    finally:
        await client.disconnect()


async def _session_status_async() -> dict[str, Any]:
    if not user_client_configured():
        return {"configured": False, "authorized": False}
    if not Path(str(session_path()) + ".session").exists():
        return {"configured": True, "authorized": False, "sessionPath": str(session_path())}
    client = await _client()
    try:
        me = await client.get_me()
        return {
            "configured": True,
            "authorized": True,
            "username": me.username,
            "userId": me.id,
            "sessionPath": str(session_path()),
        }
    finally:
        await client.disconnect()


def direct_invite_to_channel(username: str) -> int:
    """Invite @username to the channel. Returns Telegram user id."""
    return int(_run(_direct_invite_async(username)))


def direct_remove_from_channel(tg_user_id: int) -> None:
    _run(_direct_remove_async(tg_user_id))


def user_client_status() -> dict[str, Any]:
    if not user_client_configured():
        return {"configured": False, "authorized": False}
    try:
        return _run(_session_status_async())
    except Exception as exc:
        return {"configured": True, "authorized": False, "error": str(exc)}
