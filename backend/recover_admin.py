#!/usr/bin/env python3
"""Recover admin login from .env — run on EC2: cd /opt/alpha-analyser/backend && python3 recover_admin.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

env = ROOT / ".env"
if env.is_file():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from auth_db import count_users, ensure_admin_from_env, get_user_by_email, init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    print(f"Users in DB: {count_users()}")
    ensure_admin_from_env(sync_password=True)
    admin = get_user_by_email(email) if email else None
    if admin and admin.get("is_admin"):
        print(f"OK — admin ready: {email}")
        sys.exit(0)
    print("FAILED — set ADMIN_EMAIL and ADMIN_PASSWORD (6+ chars) in .env")
    sys.exit(1)
