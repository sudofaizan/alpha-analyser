"""Virtual signal storage + SL/TP tracking + market open/close detection."""
from __future__ import annotations

import json
import threading
from typing import Any

from auth_db import _iso, _utc_now, get_conn
from mt5_upstream import candles_to_bars, fetch_candles

_TICKER_STOP = threading.Event()
_TICKER_THREAD: threading.Thread | None = None


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
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_vsig_user ON virtual_signals(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_vsig_sym ON virtual_signals(symbol, timeframe);
            """
        )


def detect_market_status(m5_candles: list[dict[str, Any]], lookback: int = 3) -> dict[str, Any]:
    """CLOSED if last N M5 closes are identical; else OPEN."""
    if len(m5_candles) < lookback:
        return {"status": "UNKNOWN", "reason": "Not enough M5 data"}
    tail = m5_candles[-lookback:]
    closes = [round(float(c.get("close", 0)), 5) for c in tail]
    if len(set(closes)) == 1:
        return {
            "status": "CLOSED",
            "reason": f"Last {lookback} M5 closes unchanged ({closes[-1]})",
            "last_close": closes[-1],
        }
    return {
        "status": "OPEN",
        "reason": f"M5 price moving ({closes[0]} → {closes[-1]})",
        "last_close": closes[-1],
    }


def fetch_m5_market_snapshot(symbol: str) -> dict[str, Any]:
    try:
        data = fetch_candles(symbol, "M5", 8)
        candles = data.get("candles") or []
        bars = candles_to_bars(candles)
        market = detect_market_status(candles if candles else [], 3)
        last_bar = bars[-1] if bars else None
        return {
            "market": market,
            "last_bar": last_bar,
            "bars": bars,
        }
    except Exception as exc:
        return {"market": {"status": "UNKNOWN", "reason": str(exc)}, "last_bar": None, "bars": []}


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
    return d


def _signal_fingerprint(sig: dict[str, Any]) -> str:
    return "|".join([
        str(sig.get("action")),
        str(sig.get("orderType")),
        str(round(float(sig.get("entry") or 0), 3)),
        str(round(float(sig.get("sl") or 0), 3)),
    ])


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


def _close_signal(conn, sig_id: int, status: str, outcome: str, detail: str, last_price: float | None) -> None:
    now = _iso(_utc_now())
    conn.execute(
        """
        UPDATE virtual_signals
        SET status=?, outcome=?, status_detail=?, closed_at=?, updated_at=?, last_price=COALESCE(?, last_price)
        WHERE id=?
        """,
        (status, outcome, detail, now, now, last_price, sig_id),
    )


def _limit_filled(action: str, entry: float, bar: dict[str, Any]) -> bool:
    if action == "BUY":
        return float(bar["low"]) <= entry
    return float(bar["high"]) >= entry


def _tick_signal_row(row: dict[str, Any], bar: dict[str, Any]) -> dict[str, Any]:
    """Advance one signal against latest OHLC bar. Returns updated row dict."""
    sig_id = row["id"]
    action = row["action"]
    entry = float(row["entry"])
    sl = float(row["sl"])
    tp1 = float(row["tp1"])
    tp2 = float(row["tp2"]) if row.get("tp2") is not None else None
    inv = float(row["invalidation"]) if row.get("invalidation") is not None else sl
    order_type = row.get("order_type") or "MARKET"
    high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
    now = _iso(_utc_now())
    tp1_hit = bool(row.get("tp1_hit"))

    with get_conn() as conn:
        if order_type == "LIMIT":
            if not _limit_filled(action, entry, bar):
                if action == "BUY" and low <= inv:
                    _close_signal(conn, sig_id, "REMOVED", "REMOVED", "Invalidated before fill (support broken)", close)
                    return _row_to_signal(conn.execute("SELECT * FROM virtual_signals WHERE id=?", (sig_id,)).fetchone())
                if action == "SELL" and high >= inv:
                    _close_signal(conn, sig_id, "REMOVED", "REMOVED", "Invalidated before fill (resistance broken)", close)
                    return _row_to_signal(conn.execute("SELECT * FROM virtual_signals WHERE id=?", (sig_id,)).fetchone())
                conn.execute(
                    "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                    (close, now, sig_id),
                )
                return _row_to_signal(conn.execute("SELECT * FROM virtual_signals WHERE id=?", (sig_id,)).fetchone())

        if action == "BUY":
            if low <= sl:
                if tp1_hit:
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "TP1 hit then SL / trailing stop", close)
                else:
                    _close_signal(conn, sig_id, "LOSS", "LOSS", "Stop loss hit", close)
            elif tp2 is not None and high >= tp2:
                _close_signal(conn, sig_id, "WIN_TP2", "WIN", "Take profit 2 hit", close)
            elif high >= tp1:
                if tp2 is None:
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "Take profit 1 hit", close)
                else:
                    conn.execute(
                        "UPDATE virtual_signals SET tp1_hit=1, last_price=?, updated_at=? WHERE id=?",
                        (close, now, sig_id),
                    )
            else:
                conn.execute(
                    "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                    (close, now, sig_id),
                )
        else:
            if high >= sl:
                if tp1_hit:
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "TP1 hit then SL / trailing stop", close)
                else:
                    _close_signal(conn, sig_id, "LOSS", "LOSS", "Stop loss hit", close)
            elif tp2 is not None and low <= tp2:
                _close_signal(conn, sig_id, "WIN_TP2", "WIN", "Take profit 2 hit", close)
            elif low <= tp1:
                if tp2 is None:
                    _close_signal(conn, sig_id, "WIN_TP1", "WIN", "Take profit 1 hit", close)
                else:
                    conn.execute(
                        "UPDATE virtual_signals SET tp1_hit=1, last_price=?, updated_at=? WHERE id=?",
                        (close, now, sig_id),
                    )
            else:
                conn.execute(
                    "UPDATE virtual_signals SET last_price=?, updated_at=? WHERE id=?",
                    (close, now, sig_id),
                )

    with get_conn() as conn:
        updated = conn.execute("SELECT * FROM virtual_signals WHERE id=?", (sig_id,)).fetchone()
    return _row_to_signal(updated) if updated else row


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
            _close_signal(
                conn,
                active["id"],
                "REMOVED",
                "REMOVED",
                "Superseded by new signal",
                trade_signal.get("currentPrice"),
            )

    now = _iso(_utc_now())
    inv = trade_signal.get("sl")
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO virtual_signals (
                user_id, symbol, timeframe, action, order_type, strategy, confidence_tier,
                entry, sl, tp1, tp2, invalidation, status, outcome, status_detail,
                signal_json, created_at, updated_at, last_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'PENDING', ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                symbol,
                timeframe,
                trade_signal["action"],
                trade_signal.get("orderType"),
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
            ),
        )
        sig_id = cur.lastrowid
        row = conn.execute("SELECT * FROM virtual_signals WHERE id=?", (sig_id,)).fetchone()
    return _row_to_signal(row)


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
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM virtual_signals WHERE status='ACTIVE'"
        ).fetchall()
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


def start_signal_ticker(interval_sec: int = 45) -> None:
    global _TICKER_THREAD
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
    }
