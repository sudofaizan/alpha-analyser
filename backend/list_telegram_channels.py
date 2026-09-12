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
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for name in (".env", "telegram.env"):
    p = ROOT / name
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from telegram_user_client import _client  # noqa: E402


async def main() -> None:
    configured = os.environ.get("TELEGRAM_CHANNEL_ID", "")
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
    asyncio.run(main())
