"""Load backend .env — prefers /opt/alpha-analyser (live EC2) over script directory."""
from __future__ import annotations

import os
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
_LIVE = Path("/opt/alpha-analyser/backend")


def load_backend_env() -> Path:
    for d in (_LIVE, _BACKEND):
        if not d.is_dir():
            continue
        for name in (".env", "telegram.env"):
            p = d / name
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k:
                    os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    return _BACKEND


def require_telegram_user_env() -> None:
    missing = [
        k
        for k in ("TELEGRAM_USER_API_ID", "TELEGRAM_USER_API_HASH")
        if not os.environ.get(k, "").strip()
    ]
    if missing:
        raise SystemExit(
            "Missing " + ", ".join(missing) + " in .env\n\n"
            "Live config is at /opt/alpha-analyser/backend/.env — run:\n"
            "  cd /opt/alpha-analyser/backend\n"
            "  sudo .venv/bin/python list_telegram_channels.py"
        )
