#!/usr/bin/env python3
"""One-time login for Telegram user-account direct adds.

1. Get API id/hash from https://my.telegram.org/apps
2. Set TELEGRAM_USER_API_ID, TELEGRAM_USER_API_HASH in backend/.env or telegram.env
3. Run on EC2: cd /opt/alpha-analyser/backend && sudo .venv/bin/python setup_telegram_user.py

Your account must be admin of the signal channel (TELEGRAM_CHANNEL_ID).
"""
from __future__ import annotations

import asyncio
import os
import sys

from load_env import load_backend_env, require_telegram_user_env

load_backend_env()

from telegram_user_client import ensure_session_path, session_path, user_client_configured  # noqa: E402


async def main() -> None:
    require_telegram_user_env()
    if not user_client_configured():
        sys.exit(1)

    from telethon import TelegramClient

    api_id = int(os.environ["TELEGRAM_USER_API_ID"])
    api_hash = os.environ["TELEGRAM_USER_API_HASH"]
    import getpass

    run_as = getpass.getuser()
    if run_as != "root":
        print(f"Warning: running as '{run_as}' but gunicorn runs as root.")
        print("After login, re-run with: sudo .venv/bin/python setup_telegram_user.py")
        print("or chmod/chown the session file so root can read it.\n")

    ensure_session_path()
    path = str(session_path())
    print(f"Session file: {path}.session")
    print("You will receive an OTP in Telegram. Use the phone number of your channel admin account.\n")

    client = TelegramClient(path, api_id, api_hash)
    await client.start()
    me = await client.get_me()
    print(f"\nLogged in as @{me.username or me.id} (id {me.id})")
    print("Restart the API: sudo systemctl restart alpha-analyser-api")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
