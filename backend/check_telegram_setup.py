#!/usr/bin/env python3
"""Print Telegram backend health — run on EC2 after deploy.

  cd /opt/alpha-analyser/backend
  sudo .venv/bin/python check_telegram_setup.py   # sudo = same user as gunicorn (root)
"""
from __future__ import annotations

import sys

from load_env import load_backend_env

ROOT = load_backend_env()

from telegram_service import telegram_health_report  # noqa: E402


def main() -> None:
    import getpass

    print("Alpha Analyser — Telegram backend check")
    print(f"Running as: {getpass.getuser()}")
    print(f"Working dir: {ROOT}\n")

    h = telegram_health_report()
    for c in h.get("checks", []):
        mark = "OK" if c["ok"] else "FAIL"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"  [{mark}] {c['name']}{detail}")

    print()
    if h.get("pollerError"):
        print(f"  Poller last error: {h['pollerError']}")
    if h.get("directAdd", {}).get("sessionPathWarning"):
        print(f"\n  WARNING: {h['directAdd']['sessionPathWarning']}")
    print(f"  Session path (this user): {h.get('directAdd', {}).get('sessionPath', '?')}")

    print()
    if h.get("ready"):
        print("  Bot channel: READY (signals + invite links)")
    else:
        print("  Bot channel: NOT READY — fix FAIL items above")

    if h.get("directAddReady"):
        print("  Direct add:  ACTIVE (@username added without invite link)")
    elif h["directAdd"].get("configured"):
        print("  Direct add:  CONFIGURED but session not ready")
    else:
        print("  Direct add:  OFF (optional — invite links still work)")

    print()
    if not h.get("ready"):
        sys.exit(1)


if __name__ == "__main__":
    main()
