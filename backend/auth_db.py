"""SQLite auth + subscription store — analyser.db (easy local backup)."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ANALYSER_DB_PATH", str(ROOT / "analyser.db")))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                email_allowed INTEGER NOT NULL DEFAULT 0,
                subscription_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            """
        )
    from signal_tracker import init_signal_tables  # noqa: WPS433

    init_signal_tables()
    _migrate_telegram_columns()


_TELEGRAM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("telegram_username", "TEXT"),
    ("telegram_user_id", "INTEGER"),
    ("telegram_linked_at", "TEXT"),
    ("telegram_channel_joined", "INTEGER NOT NULL DEFAULT 0"),
    ("telegram_link_code", "TEXT"),
)


def _migrate_telegram_columns() -> None:
    with get_conn() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        for name, col_type in _TELEGRAM_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {col_type}")


def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = row.keys()
    return {
        "id": row["id"],
        "email": row["email"],
        "is_admin": bool(row["is_admin"]),
        "email_allowed": bool(row["email_allowed"]),
        "subscription_expires_at": row["subscription_expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "telegram_username": row["telegram_username"] if "telegram_username" in keys else None,
        "telegram_user_id": row["telegram_user_id"] if "telegram_user_id" in keys else None,
        "telegram_linked_at": row["telegram_linked_at"] if "telegram_linked_at" in keys else None,
        "telegram_channel_joined": bool(row["telegram_channel_joined"]) if "telegram_channel_joined" in keys else False,
        "telegram_link_code": row["telegram_link_code"] if "telegram_link_code" in keys else None,
    }


def access_status(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {"has_access": False, "reason": "unauthenticated"}
    if user.get("is_admin"):
        return {"has_access": True, "reason": None}
    if not user.get("email_allowed"):
        return {"has_access": False, "reason": "not_allowed"}
    exp_raw = user.get("subscription_expires_at")
    if not exp_raw:
        return {"has_access": False, "reason": "no_subscription"}
    exp = _parse_iso(exp_raw)
    if exp is None:
        return {"has_access": False, "reason": "no_subscription"}
    if exp <= _utc_now():
        return {"has_access": False, "reason": "expired"}
    return {"has_access": True, "reason": None}


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    status = access_status(user)
    try:
        from telegram_service import telegram_public_status  # noqa: WPS433

        tg = telegram_public_status(user)
    except Exception:
        tg = {"enabled": False, "status": "none", "username": user.get("telegram_username")}
    return {
        "id": user["id"],
        "email": user["email"],
        "is_admin": user["is_admin"],
        "email_allowed": user["email_allowed"],
        "subscription_expires_at": user["subscription_expires_at"],
        "has_access": status["has_access"],
        "access_reason": status["reason"],
        "telegram": tg,
    }


def count_users() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) if row else 0


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return _row_to_user(row)


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row)


def create_user(
    email: str,
    password: str,
    *,
    is_admin: bool = False,
    email_allowed: bool = False,
    subscription_expires_at: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    email = email.strip().lower()
    if not email or "@" not in email:
        return None, "invalid email"
    if len(password) < 6:
        return None, "password must be at least 6 characters"
    now = _iso(_utc_now())
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO users (email, password_hash, is_admin, email_allowed,
                                   subscription_expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    email,
                    generate_password_hash(password),
                    1 if is_admin else 0,
                    1 if email_allowed else 0,
                    subscription_expires_at,
                    now,
                    now,
                ),
            )
    except sqlite3.IntegrityError:
        return None, "email already registered"
    return get_user_by_email(email), None


def verify_password(user: dict[str, Any], password: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row:
        return False
    return check_password_hash(row["password_hash"], password)


def list_users() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY is_admin DESC, email ASC"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        u = _row_to_user(row)
        if u:
            out.append(public_user(u) or u)
    return out


def update_user_subscription(
    user_id: int,
    *,
    email_allowed: bool | None = None,
    subscription_expires_at: str | None | object = ...,
) -> tuple[dict[str, Any] | None, str | None]:
    user = get_user_by_id(user_id)
    if not user:
        return None, "user not found"

    fields: list[str] = []
    values: list[Any] = []

    if email_allowed is not None:
        fields.append("email_allowed = ?")
        values.append(1 if email_allowed else 0)
    if subscription_expires_at is not ...:
        if subscription_expires_at is None:
            fields.append("subscription_expires_at = NULL")
        else:
            exp = _parse_iso(str(subscription_expires_at))
            if exp is None:
                return None, "invalid subscription expiry date"
            fields.append("subscription_expires_at = ?")
            values.append(_iso(exp))

    if not fields:
        return public_user(user), None

    fields.append("updated_at = ?")
    values.append(_iso(_utc_now()))
    values.append(user_id)

    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)

    updated = get_user_by_id(user_id)
    return public_user(updated), None


def delete_user(user_id: int) -> tuple[bool, str | None]:
    user = get_user_by_id(user_id)
    if not user:
        return False, "user not found"
    if user.get("is_admin"):
        return False, "cannot delete admin user"
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True, None


def ensure_admin_bootstrap() -> None:
    """Create first admin from env when DB is empty."""
    if count_users() > 0:
        return
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not email or not password:
        print("auth_db: no users — set ADMIN_EMAIL and ADMIN_PASSWORD to bootstrap admin")
        return
    far = _iso(datetime(2099, 12, 31, tzinfo=timezone.utc))
    user, err = create_user(
        email,
        password,
        is_admin=True,
        email_allowed=True,
        subscription_expires_at=far,
    )
    if user:
        print(f"auth_db: bootstrapped admin {email}")
    elif err:
        print(f"auth_db: admin bootstrap failed: {err}")
