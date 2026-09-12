"""Virtual signal storage + SL/TP tracking + market open/close detection."""
from __future__ import annotations

import json
import threading
from typing import Any

from datetime import datetime, timezone

from auth_db import _iso, _utc_now, get_conn
from mt5_upstream import candles_to_bars, fetch_candles, parse_time_to_unix

# Symbols that follow typical FX weekend halt (Fri ~22 UTC → Sun ~22 UTC).
_FOREX_WEEKEND_ROOTS = frozenset({
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "NZDUSD", "USDCHF", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY",
    "NATGAS", "USOIL", "UKOIL",
})

M5_SECONDS = 5 * 60
# One full M5 bar + small buffer — no new bar ⇒ session not printing.
STALE_M5_SECONDS = M5_SECONDS + 120

_TICKER_STOP = threading.Event()
_TICKER_THREAD: threading.Thread | None = None
_TICKER_INTERVAL_SEC = 45
_LAST_TICK_AT: str | None = None
_LAST_TICK_ERROR: str | None = None
_LAST_TICK_UPDATED = 0

_TRACKING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("invoked_at", "TEXT"),
    ("fill_status", "TEXT NOT NULL DEFAULT 'PENDING'"),
    ("filled_at", "TEXT"),
    ("filled_price", "REAL"),
    ("tp1_hit_at", "TEXT"),
    ("tp1_hit_price", "REAL"),
    ("tp2_hit_at", "TEXT"),
    ("tp2_hit_price", "REAL"),
    ("sl_hit_at", "TEXT"),
    ("sl_hit_price", "REAL"),
    ("invalidated_at", "TEXT"),
    ("invalidated_price", "REAL"),
    ("tracking_events", "TEXT"),
)


def init_signal_tables() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS virtual_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                action TEXT NOT NULL,
                order_type TEXT,
                strategy TEXT,
                confidence_tier TEXT,
                entry REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
                invalidation REAL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                outcome TEXT NOT NULL DEFAULT 'PENDING',
                status_detail TEXT,
                signal_json TEXT,
                tp1_hit INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                last_price REAL,
                invoked_at TEXT,
                fill_status TEXT NOT NULL DEFAULT 'PENDING',
                filled_at TEXT,
                filled_price REAL,
                tp1_hit_at TEXT,
                tp1_hit_price REAL,
                tp2_hit_at TEXT,
                tp2_hit_price REAL,
                sl_hit_at TEXT,
                sl_hit_price REAL,
                invalidated_at TEXT,
                invalidated_price REAL,
                tracking_events TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_vsig_user ON virtual_signals(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_vsig_sym ON virtual_signals(symbol, timeframe);
            """
        )
        _migrate_tracking_columns(conn)


def _migrate_tracking_columns(conn) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(virtual_signals)")}
    for name, col_type in _TRACKING_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE virtual_signals ADD COLUMN {name} {col_type}")
    conn.execute(
        """
        UPDATE virtual_signals
        SET invoked_at = COALESCE(invoked_at, created_at),
            fill_status = CASE
                WHEN fill_status IS NULL OR fill_status = '' THEN
                    CASE WHEN order_type = 'MARKET' THEN 'FILLED' ELSE 'PENDING' END
                ELSE fill_status
            END
        WHERE invoked_at IS NULL OR fill_status IS NULL OR fill_status = ''
        """
    )


def _symbol_root(symbol: str) -> str:
    return "".join(ch for ch in (symbol or "").upper() if ch.isalnum())[:12]


def _is_forex_weekend(now: datetime, symbol: str) -> bool:
    root = _symbol_root(symbol)
    if not any(root.startswith(r) or r.startswith(root[:6]) for r in _FOREX_WEEKEND_ROOTS):
        return False
    wd = now.weekday()
    if wd == 5:
        return True
    if wd == 6 and now.hour < 22:
        return True
    return False


def _bar_frozen(c: dict[str, Any]) -> bool:
    o = round(float(c.get("open", 0)), 5)
    h = round(float(c.get("high", 0)), 5)
    l = round(float(c.get("low", 0)), 5)
    cl = round(float(c.get("close", 0)), 5)
    return o == h == l == cl


def detect_market_status(
    m5_candles: list[dict[str, Any]],
    lookback: int = 3,
    *,
    symbol: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    CLOSED when:
    1) Last M5 candle is stale (no new bar for >7 min) — e.g. Saturday with Fri data
    2) Forex weekend window (Sat / Sun before open)
    3) Last N M5 closes identical (user rule)
    4) Last N bars frozen (O=H=L=C)
    else OPEN.
    """
    now = now or _utc_now()
    if len(m5_candles) < 1:
        return {"status": "UNKNOWN", "reason": "No M5 data"}

    last = m5_candles[-1]
    last_close = round(float(last.get("close", 0)), 5)
    last_ts = parse_time_to_unix(last.get("time"))

    if _is_forex_weekend(now, symbol):
        return {
            "status": "CLOSED",
            "reason": "Forex/metals weekend — session closed",
            "last_close": last_close,
            "lastCandleAt": last.get("time"),
        }

    if last_ts is not None:
        age_sec = int(now.timestamp()) - last_ts
        if age_sec > STALE_M5_SECONDS:
            mins = max(1, age_sec // 60)
            return {
                "status": "CLOSED",
                "reason": f"No new M5 candle for {mins}m (last {last.get('time')})",
                "last_close": last_close,
                "lastCandleAt": last.get("time"),
                "candleAgeSec": age_sec,
            }

    if len(m5_candles) < lookback:
        return {"status": "UNKNOWN", "reason": "Not enough M5 data", "last_close": last_close}

    tail = m5_candles[-lookback:]
    closes = [round(float(c.get("close", 0)), 5) for c in tail]
    if len(set(closes)) == 1:
        return {
            "status": "CLOSED",
            "reason": f"Last {lookback} M5 closes unchanged ({closes[-1]})",
            "last_close": closes[-1],
            "lastCandleAt": last.get("time"),
        }
    if all(_bar_frozen(c) for c in tail):
        return {
            "status": "CLOSED",
            "reason": f"Last {lookback} M5 bars flat (O=H=L=C @ {closes[-1]})",
            "last_close": closes[-1],
            "lastCandleAt": last.get("time"),
        }
    return {
        "status": "OPEN",
        "reason": f"M5 price moving ({closes[0]} → {closes[-1]})",
        "last_close": closes[-1],
        "lastCandleAt": last.get("time"),
    }


def fetch_m5_market_snapshot(symbol: str) -> dict[str, Any]:
    try:
        data = fetch_candles(symbol, "M5", 8)
        candles = data.get("candles") or []
        bars = candles_to_bars(candles)
        market = detect_market_status(candles if candles else [], 3, symbol=symbol)
        last_bar = bars[-1] if bars else None
        return {
            "market": market,
            "last_bar": last_bar,
            "bars": bars,
        }
    except Exception as exc:
        return {"market": {"status": "UNKNOWN", "reason": str(exc)}, "last_bar": None, "bars": []}


def _load_events(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _row_to_signal(row) -> dict[str, Any]:
    if row is None:
        return {}
    d = dict(row)
    if d.get("signal_json"):
        try:
            d["signal"] = json.loads(d["signal_json"])
        except json.JSONDecodeError:
            d["signal"] = None
    d["tp1_hit"] = bool(d.get("tp1_hit"))
    d["tracking_events_list"] = _load_events(d.get("tracking_events"))
    return d


def _signal_fingerprint(sig: dict[str, Any]) -> str:
    return "|".join([
        str(sig.get("action")),
        str(sig.get("orderType")),
        str(round(float(sig.get("entry") or 0), 3)),
        str(round(float(sig.get("sl") or 0), 3)),
    ])


def _append_event(conn, sig_id: int, event: str, *, price: float | None = None, note: str = "") -> list[dict[str, Any]]:
    row = conn.execute("SELECT tracking_events FROM virtual_signals WHERE id=?", (sig_id,)).fetchone()
    events = _load_events(row["tracking_events"] if row else None)
    entry: dict[str, Any] = {"event": event, "at": _iso(_utc_now())}
    if price is not None:
        entry["price"] = round(float(price), 5)
    if note:
        entry["note"] = note
    events.append(entry)
    conn.execute(
        "UPDATE virtual_signals SET tracking_events=?, updated_at=? WHERE id=?",
        (json.dumps(events), _iso(_utc_now()), sig_id),
    )
    return events


def _reload_signal(sig_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM virtual_signals WHERE id=?", (sig_id,)).fetchone()
    return _row_to_signal(row) if row else {}


def get_active_signal(user_id: int, symbol: str, timeframe: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM virtual_signals
            WHERE user_id=? AND symbol=? AND timeframe=? AND status='ACTIVE'
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, symbol, timeframe),
        ).fetchone()
    return _row_to_signal(row) if row else None


def list_signal_history(user_id: int, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    q = "SELECT * FROM virtual_signals WHERE user_id=?"
    params: list[Any] = [user_id]
    if symbol:
        q += " AND symbol=?"
        params.append(symbol)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_row_to_signal(r) for r in rows]


def _close_signal(
    conn,
    sig_id: int,
    status: str,
    outcome: str,
    detail: str,
    last_price: float | None,
    *,
    event: str | None = None,
    event_price: float | None = None,
) -> None:
    now = _iso(_utc_now())
    conn.execute(
        """
        UPDATE virtual_signals
        SET status=?, outcome=?, status_detail=?, closed_at=?, updated_at=?,
            last_price=COALESCE(?, last_price)
        WHERE id=?
        """,
        (status, outcome, detail, now, now, last_price, sig_id),
    )
    if event:
        _append_event(conn, sig_id, event, price=event_price, note=detail)


def _mark_filled(conn, sig_id: int, fill_price: float, order_type: str, action: str) -> None:
    now = _iso(_utc_now())
    note = f"{action} {order_type} filled"
    conn.execute(
        """
        UPDATE virtual_signals
        SET fill_status='FILLED', filled_at=?, filled_price=?, updated_at=?, last_price=?
        WHERE id=? AND fill_status != 'FILLED'
        """,
        (now, fill_price, now, fill_price, sig_id),
    )
    row = conn.execute("SELECT fill_status, filled_at FROM virtual_signals WHERE id=?", (sig_id,)).fetchone()
    if row and row["filled_at"] == now:
        _append_event(conn, sig_id, "FILLED", price=fill_price, note=note)


def _mark_tp1(conn, sig_id: int, tp1_price: float) -> None:
    now = _iso(_utc_now())
    conn.execute(
        """
        UPDATE virtual_signals
        SET tp1_hit=1, tp1_hit_at=COALESCE(tp1_hit_at, ?), tp1_hit_price=COALESCE(tp1_hit_price, ?),
            updated_at=?
        WHERE id=?
        """,
        (now, tp1_price, now, sig_id),
    )
    row = conn.execute("SELECT tp1_hit_at FROM virtual_signals WHERE id=?", (sig_id,)).fetchone()
    if row and row["tp1_hit_at"] == now:
        _append_event(conn, sig_id, "TP1_HIT", price=tp1_price, note="Take profit 1 reached")


def _mark_tp2(conn, sig_id: int, tp2_price: float, detail: str) -> None:
    now = _iso(_utc_now())
    conn.execute(
        """
        UPDATE virtual_signals SET tp2_hit_at=?, tp2_hit_price=?, updated_at=? WHERE id=?
        """,
        (now, tp2_price, now, sig_id),
    )
    _append_event(conn, sig_id, "TP2_HIT", price=tp2_price, note=detail)


def _mark_sl(conn, sig_id: int, sl_price: float, detail: str) -> None:
    now = _iso(_utc_now())
    conn.execute(
        """
        UPDATE virtual_signals SET sl_hit_at=?, sl_hit_price=?, updated_at=? WHERE id=?
        """,
        (now, sl_price, now, sig_id),
    )
    _append_event(conn, sig_id, "SL_HIT", price=sl_price, note=detail)


def _mark_invalidated(conn, sig_id: int, price: float | None, detail: str, event: str = "INVALIDATED") -> None:
    now = _iso(_utc_now())
    conn.execute(
        """
        UPDATE virtual_signals
        SET invalidated_at=?, invalidated_price=?, updated_at=?, last_price=COALESCE(?, last_price)
        WHERE id=?
        """,
        (now, price, now, price, sig_id),
    )
    _append_event(conn, sig_id, event, price=price, note=detail)


def _limit_filled(action: str, entry: float, bar: dict[str, Any]) -> bool:
    if action == "BUY":
        return float(bar["low"]) <= entry
    return float(bar["high"]) >= entry


def _is_filled(row: dict[str, Any]) -> bool:
    return (row.get("fill_status") or "").upper() == "FILLED" or bool(row.get("filled_at"))


def _tick_signal_row(row: dict[str, Any], bar: dict[str, Any]) -> dict[str, Any]:
    """Advance one signal against latest OHLC bar. Returns updated row dict."""
    sig_id = row["id"]
    action = row["action"]
    entry = float(row["entry"])
    sl = float(row["sl"])
    tp1 = float(row["tp1"])
    tp2 = float(row["tp2"]) if row.get("tp2") is not None else None
    inv = float(row["invalidation"]) if row.get("invalidation") is not None else sl
    order_type = (row.get("order_type") or "MARKET").upper()
    high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
    now = _iso(_utc_now())
    tp1_hit = bool(row.get("tp1_hit"))
    filled = _is_filled(row)

    with get_conn() as conn:
        if not filled:
            if order_type == "LIMIT":
                if _limit_filled(action, entry, bar):
                    _mark_filled(conn, sig_id, entry, order_type, action)
                    filled = True
                elif action == "BUY" and low <= inv:
                    _mark_invalidated(conn, sig_id, close, "Invalidated before fill (support broken)")
                    _close_signal(
                        conn, sig_id, "REMOVED", "REMOVED",
                        "Invalidated before fill (support broken)", close,
                        event="CLOSED", event_price=close,
                    )
                    return _reload_signal(sig_id)
                elif action == "SELL" and high >= inv:
                    _mark_invalidated(conn, sig_id, close, "Invalidated before fill (resistance broken)")
                    _close_signal(
                        conn, sig_id, "REMOVED", "REMOVED",
                        "Invalidated before fill (resistance broken)", close,
                        event="CLOSED", event_price=close,
                    )
                    return _reload_signal(sig_id)
                else:
                    conn.execute(
                        "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                        (close, now, sig_id),
                    )
                    return _reload_signal(sig_id)
            else:
                _mark_filled(conn, sig_id, entry, order_type, action)
                filled = True

        if not filled:
            return _reload_signal(sig_id)

        if action == "BUY":
            if low <= sl:
                _mark_sl(conn, sig_id, sl, "Stop loss hit" if not tp1_hit else "TP1 hit then SL / trailing stop")
                if tp1_hit:
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "TP1 hit then SL / trailing stop", close, event="CLOSED", event_price=close)
                else:
                    _close_signal(conn, sig_id, "LOSS", "LOSS", "Stop loss hit", close, event="CLOSED", event_price=close)
            elif tp2 is not None and high >= tp2:
                if not tp1_hit:
                    _mark_tp1(conn, sig_id, tp1)
                _mark_tp2(conn, sig_id, tp2, "Take profit 2 hit")
                _close_signal(conn, sig_id, "WIN_TP2", "WIN", "Take profit 2 hit", close, event="CLOSED", event_price=close)
            elif high >= tp1:
                if tp2 is None:
                    _mark_tp1(conn, sig_id, tp1)
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "Take profit 1 hit", close, event="CLOSED", event_price=close)
                elif not tp1_hit:
                    _mark_tp1(conn, sig_id, tp1)
                    conn.execute(
                        "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                        (close, now, sig_id),
                    )
            else:
                conn.execute(
                    "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                    (close, now, sig_id),
                )
        else:
            if high >= sl:
                _mark_sl(conn, sig_id, sl, "Stop loss hit" if not tp1_hit else "TP1 hit then SL / trailing stop")
                if tp1_hit:
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "TP1 hit then SL / trailing stop", close, event="CLOSED", event_price=close)
                else:
                    _close_signal(conn, sig_id, "LOSS", "LOSS", "Stop loss hit", close, event="CLOSED", event_price=close)
            elif tp2 is not None and low <= tp2:
                if not tp1_hit:
                    _mark_tp1(conn, sig_id, tp1)
                _mark_tp2(conn, sig_id, tp2, "Take profit 2 hit")
                _close_signal(conn, sig_id, "WIN_TP2", "WIN", "Take profit 2 hit", close, event="CLOSED", event_price=close)
            elif low <= tp1:
                if tp2 is None:
                    _mark_tp1(conn, sig_id, tp1)
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "Take profit 1 hit", close, event="CLOSED", event_price=close)
                elif not tp1_hit:
                    _mark_tp1(conn, sig_id, tp1)
                    conn.execute(
                        "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                        (close, now, sig_id),
                    )
            else:
                conn.execute(
                    "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                    (close, now, sig_id),
                )

    return _reload_signal(sig_id)


def upsert_virtual_signal(
    user_id: int,
    symbol: str,
    timeframe: str,
    trade_signal: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not trade_signal or not trade_signal.get("action"):
        return get_active_signal(user_id, symbol, timeframe)

    fp = _signal_fingerprint(trade_signal)
    active = get_active_signal(user_id, symbol, timeframe)

    if active:
        old_fp = _signal_fingerprint({
            "action": active.get("action"),
            "orderType": active.get("order_type"),
            "entry": active.get("entry"),
            "sl": active.get("sl"),
        })
        if fp == old_fp:
            return active
        with get_conn() as conn:
            _mark_invalidated(
                conn, active["id"], trade_signal.get("currentPrice"),
                "Superseded by new signal", event="SUPERSEDED",
            )
            _close_signal(
                conn,
                active["id"],
                "REMOVED",
                "REMOVED",
                "Superseded by new signal",
                trade_signal.get("currentPrice"),
                event="CLOSED",
                event_price=trade_signal.get("currentPrice"),
            )

    now = _iso(_utc_now())
    inv = trade_signal.get("sl")
    order_type = (trade_signal.get("orderType") or "MARKET").upper()
    entry = float(trade_signal.get("entry") or 0)
    fill_price = trade_signal.get("currentPrice") or entry
    is_market = order_type == "MARKET"

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO virtual_signals (
                user_id, symbol, timeframe, action, order_type, strategy, confidence_tier,
                entry, sl, tp1, tp2, invalidation, status, outcome, status_detail,
                signal_json, created_at, updated_at, last_price,
                invoked_at, fill_status, filled_at, filled_price, tracking_events
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'PENDING', ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                symbol,
                timeframe,
                trade_signal["action"],
                order_type,
                trade_signal.get("strategy"),
                trade_signal.get("confidenceTier"),
                trade_signal.get("entry"),
                trade_signal.get("sl"),
                trade_signal.get("tp1"),
                trade_signal.get("tp2"),
                inv,
                trade_signal.get("entryNote"),
                json.dumps(trade_signal),
                now,
                now,
                trade_signal.get("currentPrice"),
                now,
                "FILLED" if is_market else "PENDING",
                now if is_market else None,
                fill_price if is_market else None,
                json.dumps([{
                    "event": "INVOKED",
                    "at": now,
                    "price": round(float(entry), 5),
                    "note": f"{trade_signal['action']} {order_type} signal created",
                }]),
            ),
        )
        sig_id = cur.lastrowid
        if is_market:
            _append_event(
                conn, sig_id, "FILLED",
                price=float(fill_price),
                note=f"{trade_signal['action']} MARKET executed",
            )

    row = _reload_signal(sig_id)
    try:
        from telegram_service import post_signal_to_channel  # noqa: WPS433

        post_signal_to_channel(trade_signal, format_signal_id(sig_id, symbol))
    except Exception:
        pass
    return row


def process_user_symbol_signals(
    user_id: int,
    symbol: str,
    timeframe: str,
    trade_signal: dict[str, Any] | None,
    m5_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snap = m5_snapshot or fetch_m5_market_snapshot(symbol)
    market = snap.get("market") or {"status": "UNKNOWN"}
    bar = snap.get("last_bar")

    active = upsert_virtual_signal(user_id, symbol, timeframe, trade_signal)
    if active and active.get("status") == "ACTIVE" and bar:
        active = _tick_signal_row(active, bar)

    return {
        "market": market,
        "active_signal": active if active and active.get("status") == "ACTIVE" else None,
        "last_price": bar.get("close") if bar else None,
    }


def tick_all_active_signals() -> None:
    """Background: update all ACTIVE signals using latest M5 bar."""
    global _LAST_TICK_AT, _LAST_TICK_ERROR, _LAST_TICK_UPDATED
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM virtual_signals WHERE status='ACTIVE'"
            ).fetchall()
        updated = 0
        for row in rows:
            sym = row["symbol"]
            snap = fetch_m5_market_snapshot(sym)
            bar = snap.get("last_bar")
            if not bar:
                continue
            with get_conn() as conn:
                actives = conn.execute(
                    "SELECT * FROM virtual_signals WHERE status='ACTIVE' AND symbol=?",
                    (sym,),
                ).fetchall()
            for r in actives:
                _tick_signal_row(_row_to_signal(r), bar)
                updated += 1
        _LAST_TICK_AT = _iso(_utc_now())
        _LAST_TICK_ERROR = None
        _LAST_TICK_UPDATED = updated
    except Exception as exc:
        _LAST_TICK_ERROR = str(exc)
        raise


def _tracking_phase(row: dict[str, Any]) -> str:
    if row.get("status") != "ACTIVE":
        return row.get("status") or "CLOSED"
    if (row.get("fill_status") or "").upper() != "FILLED" and not row.get("filled_at"):
        return "PENDING_FILL"
    if row.get("tp1_hit") or row.get("tp1_hit_at"):
        return "RUNNER_TP2_OR_BE"
    return "MONITORING_SL_TP"


def _admin_signal_row(row: dict[str, Any], email: str = "") -> dict[str, Any]:
    pub = public_signal_row(row) or {}
    pub["userEmail"] = email
    pub["phase"] = _tracking_phase(row)
    pub["updatedAt"] = row.get("updated_at")
    return pub


def get_admin_tracking_dashboard(recent_closed_limit: int = 25) -> dict[str, Any]:
    """Live view of virtual tracking engine for admin panel."""
    recent_closed_limit = max(5, min(recent_closed_limit, 100))
    with get_conn() as conn:
        active_rows = conn.execute(
            """
            SELECT vs.*, u.email AS user_email
            FROM virtual_signals vs
            JOIN users u ON u.id = vs.user_id
            WHERE vs.status = 'ACTIVE'
            ORDER BY vs.updated_at DESC
            """
        ).fetchall()
        closed_rows = conn.execute(
            """
            SELECT vs.*, u.email AS user_email
            FROM virtual_signals vs
            JOIN users u ON u.id = vs.user_id
            WHERE vs.status != 'ACTIVE'
            ORDER BY vs.closed_at DESC, vs.id DESC
            LIMIT ?
            """,
            (recent_closed_limit,),
        ).fetchall()
        stats = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active_total,
                SUM(CASE WHEN status = 'ACTIVE' AND fill_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_fill,
                SUM(CASE WHEN status = 'ACTIVE' AND tp1_hit = 1 THEN 1 ELSE 0 END) AS runner_count,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN outcome = 'REMOVED' THEN 1 ELSE 0 END) AS removed
            FROM virtual_signals
            """
        ).fetchone()

    active = [
        _admin_signal_row(_row_to_signal(r), r["user_email"])
        for r in active_rows
    ]
    recent_closed = [
        _admin_signal_row(_row_to_signal(r), r["user_email"])
        for r in closed_rows
    ]
    symbols = sorted({s["symbol"] for s in active if s.get("symbol")})

    return {
        "engine": {
            "running": bool(_TICKER_THREAD and _TICKER_THREAD.is_alive()),
            "intervalSec": _TICKER_INTERVAL_SEC,
            "lastTickAt": _LAST_TICK_AT,
            "lastTickUpdated": _LAST_TICK_UPDATED,
            "lastTickError": _LAST_TICK_ERROR,
        },
        "stats": {
            "active": int(stats["active_total"] or 0),
            "pendingFill": int(stats["pending_fill"] or 0),
            "runnerPhase": int(stats["runner_count"] or 0),
            "wins": int(stats["wins"] or 0),
            "losses": int(stats["losses"] or 0),
            "removed": int(stats["removed"] or 0),
        },
        "symbolsMonitored": symbols,
        "activeSignals": active,
        "recentClosed": recent_closed,
        "generatedAt": _iso(_utc_now()),
    }


def start_signal_ticker(interval_sec: int = 45) -> None:
    global _TICKER_THREAD, _TICKER_INTERVAL_SEC
    _TICKER_INTERVAL_SEC = interval_sec
    if _TICKER_THREAD and _TICKER_THREAD.is_alive():
        return

    def _loop() -> None:
        while not _TICKER_STOP.is_set():
            try:
                tick_all_active_signals()
            except Exception:
                pass
            _TICKER_STOP.wait(interval_sec)

    _TICKER_THREAD = threading.Thread(target=_loop, name="signal-ticker", daemon=True)
    _TICKER_THREAD.start()


def format_signal_id(row_id: int | None, symbol: str = "") -> str | None:
    """Human-readable signal reference, e.g. SIG-XAUUSD-000042."""
    if row_id is None:
        return None
    sym = "".join(ch for ch in (symbol or "UNK").upper() if ch.isalnum())[:12] or "UNK"
    return f"SIG-{sym}-{int(row_id):06d}"


def parse_signal_id_ref(ref: str) -> int | None:
    """Accept numeric id or SIG-SYMBOL-000042."""
    s = (ref or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s.upper().startswith("SIG-"):
        tail = s.rsplit("-", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return None


def get_signal_by_id(user_id: int, signal_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM virtual_signals WHERE id=? AND user_id=?",
            (signal_id, user_id),
        ).fetchone()
    return _row_to_signal(row) if row else None


def build_tracking_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Ready-made lifecycle tracking — computed on backend only."""
    events = row.get("tracking_events_list") or _load_events(row.get("tracking_events"))
    return {
        "invokedAt": row.get("invoked_at") or row.get("created_at"),
        "invokedPrice": row.get("entry"),
        "fillStatus": row.get("fill_status") or "PENDING",
        "filledAt": row.get("filled_at"),
        "filledPrice": row.get("filled_price"),
        "tp1HitAt": row.get("tp1_hit_at"),
        "tp1HitPrice": row.get("tp1_hit_price"),
        "tp2HitAt": row.get("tp2_hit_at"),
        "tp2HitPrice": row.get("tp2_hit_price"),
        "slHitAt": row.get("sl_hit_at"),
        "slHitPrice": row.get("sl_hit_price"),
        "invalidatedAt": row.get("invalidated_at"),
        "invalidatedPrice": row.get("invalidated_price"),
        "closedAt": row.get("closed_at"),
        "timeline": events,
    }


def public_signal_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    sid = row.get("id")
    symbol = row.get("symbol") or ""
    return {
        "id": sid,
        "signalId": format_signal_id(sid, symbol),
        "symbol": symbol,
        "timeframe": row.get("timeframe"),
        "action": row.get("action"),
        "orderType": row.get("order_type"),
        "strategy": row.get("strategy"),
        "confidenceTier": row.get("confidence_tier"),
        "entry": row.get("entry"),
        "sl": row.get("sl"),
        "tp1": row.get("tp1"),
        "tp2": row.get("tp2"),
        "status": row.get("status"),
        "outcome": row.get("outcome"),
        "statusDetail": row.get("status_detail"),
        "tp1Hit": row.get("tp1_hit"),
        "lastPrice": row.get("last_price"),
        "createdAt": row.get("created_at"),
        "closedAt": row.get("closed_at"),
        "tracking": build_tracking_payload(row),
    }
