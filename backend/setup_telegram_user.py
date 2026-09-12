#!/usr/bin/env python3
"""One-time login for Telegram user-account direct adds.

1. Get API id/hash from https://my.telegram.org/apps
2. Set TELEGRAM_USER_API_ID, TELEGRAM_USER_API_HASH in backend/.env or telegram.env
3. Run: cd backend && source .venv/bin/activate && python setup_telegram_user.py

Your account must be admin of the signal channel (TELEGRAM_CHANNEL_ID).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Load .env / telegram.env like the API does
for name in (".env", "telegram.env"):
    p = Path(__file__).resolve().parent / name
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from telegram_user_client import ensure_session_path, session_path, user_client_configured  # noqa: E402


async def main() -> None:
    if not user_client_configured():
        print("Set TELEGRAM_USER_API_ID and TELEGRAM_USER_API_HASH in backend/.env first.")
        print("Create an app at https://my.telegram.org/apps")
        sys.exit(1)

    from telethon import TelegramClient

    api_id = int(os.environ["TELEGRAM_USER_API_ID"])
    api_hash = os.environ["TELEGRAM_USER_API_HASH"]
    import getpass

    run_as = getpass.getuser()
    if run_as != "root":
        print(f"Warning: running as '{run_as}' but gunicorn runs as root.")
        print("After login, either re-run with: sudo python setup_telegram_user.py")
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
