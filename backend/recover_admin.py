#!/usr/bin/env python3
"""Recover admin login from .env.

On EC2 (use venv Python — NOT sudo python3):
  cd /opt/alpha-analyser/backend && ./recover_admin.sh
  # or: .venv/bin/python3 recover_admin.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python3"

# Re-exec with project venv (sudo python3 uses system Python without Flask/werkzeug).
if VENV_PYTHON.is_file():
    try:
        if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
            os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    except OSError:
        pass
elif "werkzeug" not in sys.modules:
    print("ERROR: No .venv found. Run from deployed backend:")
    print(f"  cd {ROOT}")
    print("  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
    print("  .venv/bin/python3 recover_admin.py")
    sys.exit(1)

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

from auth_db import DB_PATH, count_users, ensure_admin_from_env, get_user_by_email, init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    print(f"Backend dir: {ROOT}")
    print(f"Auth DB:     {DB_PATH}")
    print(f"Env file:    {env if env.is_file() else '(missing — set ADMIN_EMAIL in environment)'}")
    print(f"Users in DB: {count_users()}")
    print(f"Python:      {sys.executable}")
    ensure_admin_from_env(sync_password=True)
    admin = get_user_by_email(email) if email else None
    if admin and admin.get("is_admin"):
        print(f"OK — admin ready: {email}")
        sys.exit(0)
    print("FAILED — set ADMIN_EMAIL and ADMIN_PASSWORD (6+ chars) in .env")
    sys.exit(1)
