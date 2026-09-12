#!/usr/bin/env python3
"""List channels/groups visible to your logged-in Telegram account — find TELEGRAM_CHANNEL_ID.

  cd /opt/alpha-analyser/backend
  sudo .venv/bin/python list_telegram_channels.py

Use the id starting with -100... for your private signal channel in .env:
  TELEGRAM_CHANNEL_ID=-1001234567890
"""
from __future__ import annotations

import asyncio
import os
import sys

from load_env import load_backend_env, require_telegram_user_env

load_backend_env()
require_telegram_user_env()

from telegram_user_client import _client  # noqa: E402


async def main() -> None:
    configured = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    env_src = "/opt/alpha-analyser/backend/.env" if os.path.isfile("/opt/alpha-analyser/backend/.env") else "backend/.env"
    print(f"Using env from {env_src}")
    print(f"Session: {os.environ.get('TELEGRAM_USER_SESSION', '.telegram_user')}\n")

    if configured and not str(configured).startswith("-100"):
        print(f"WARNING: TELEGRAM_CHANNEL_ID={configured} does not look like a channel id.")
        print("Channel ids are negative and usually start with -100\n")

    client = await _client()
    try:
        print(f"{'ID':<20} {'TYPE':<10} TITLE")
        print("-" * 60)
        async for d in client.iter_dialogs():
            if not (d.is_channel or d.is_group):
                continue
            kind = "channel" if d.is_channel else "group"
            title = (d.title or "").replace("\n", " ")[:40]
            mark = " ← current .env" if configured and str(d.id) == str(configured) else ""
            print(f"{d.id:<20} {kind:<10} {title}{mark}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
