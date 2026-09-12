#!/usr/bin/env python3
"""Print Telegram backend health — run on EC2 after deploy.

  cd /opt/alpha-analyser/backend
  sudo .venv/bin/python check_telegram_setup.py   # sudo = same user as gunicorn (root)
"""
from __future__ import annotations

import os
import sys
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
