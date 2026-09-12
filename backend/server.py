#!/usr/bin/env python3
"""Alpha Analyser API — chart analysis + render bundles for EC2 deployment."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

from alpha_analyser_engine import HTF_MAP, build_render_spec  # noqa: E402
from auth_db import ensure_admin_bootstrap, init_db  # noqa: E402
from auth_routes import auth_bp, admin_bp, subscription_required  # noqa: E402
from telegram_routes import telegram_bp  # noqa: E402
from telegram_service import start_subscription_guard, start_telegram_poller  # noqa: E402
from mt5_upstream import candles_to_bars, fetch_analysis, fetch_candles  # noqa: E402
from signal_tracker import (  # noqa: E402
    fetch_m5_market_snapshot,
    get_signal_by_id,
    list_signal_history,
    parse_signal_id_ref,
    process_user_symbol_signals,
    public_signal_row,
    start_signal_ticker,
)

app = Flask(__name__)
CORS(app, supports_credentials=True)

API_KEY = os.environ.get("ANALYSER_API_KEY", "alphafx")
PORT = int(os.environ.get("PORT", "8090"))

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(telegram_bp)


def require_key() -> tuple[dict | None, tuple | None]:
    key = request.headers.get("X-API-Key") or request.args.get("api_key", "")
    if API_KEY and key != API_KEY:
        return None, (jsonify({"ok": False, "error": "unauthorized"}), 401)
    return {}, None


def build_chart_bundle(symbol: str, chart_tf: str, count: int) -> dict[str, Any]:
    chart_tf = chart_tf.upper()
    c_data = fetch_candles(symbol, chart_tf, count)
    if not c_data.get("candles"):
        return {"ok": False, "error": c_data.get("error") or "no candles from MT5 VPS"}

    a_data = fetch_analysis(symbol, chart_tf, count)
    if not a_data.get("ok"):
        return {"ok": False, "error": a_data.get("error") or "analysis failed on MT5 VPS"}

    bars = candles_to_bars(c_data["candles"])
    if not bars:
        return {"ok": False, "error": "no valid bars after parse"}

    htf_bars: list[dict[str, Any]] = []
    htf_tf = HTF_MAP.get(chart_tf)
    if htf_tf:
        htf_count = min(300, max(80, count // 5))
        try:
            h_data = fetch_candles(symbol, htf_tf, htf_count)
            if h_data.get("candles"):
                htf_bars = candles_to_bars(h_data["candles"])
        except Exception:
            htf_bars = []

    render = build_render_spec(bars, a_data, htf_bars, chart_tf)
    chart_meta = a_data.get("chart") or {}
    ind_n = (a_data.get("smc") or {}).get("inducement_count") or chart_meta.get("smc_inducement_count")
    sym = c_data.get("symbol") or symbol

    return {
        "ok": True,
        "symbol": sym,
        "timeframe": chart_tf,
        "count": len(c_data["candles"]),
        "candles": c_data["candles"],
        "render": render,
        "meta": {
            "smc_fvg_count": chart_meta.get("smc_fvg_count"),
            "smc_last_break": chart_meta.get("smc_last_break"),
            "smc_inducement_count": ind_n,
            "beluga_ob_count": chart_meta.get("beluga_ob_count"),
            "overall_trend": a_data.get("overall_trend"),
            "source": "ec2",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    }


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "alpha-analyser",
        "mt5_vps": os.environ.get("MT5_VPS_URL", "http://13.42.76.172:8080"),
        "auth": True,
    })


@app.route("/getChartBundle")
@subscription_required
def get_chart_bundle(user):
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    chart_tf = request.args.get("timeframe", "M5").upper()
    count = max(50, min(int(request.args.get("count", 200)), 5000))
    try:
        result = build_chart_bundle(symbol, chart_tf, count)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    if result.get("ok"):
        m5_snap = fetch_m5_market_snapshot(result.get("symbol") or symbol)
        trade_sig = (result.get("render") or {}).get("trade_signal")
        tracking = process_user_symbol_signals(
            user["id"],
            result.get("symbol") or symbol,
            chart_tf,
            trade_sig,
            m5_snap,
        )
        result["meta"] = result.get("meta") or {}
        result["meta"]["market_status"] = tracking["market"].get("status", "UNKNOWN")
        result["meta"]["market_status_reason"] = tracking["market"].get("reason")
        result["meta"]["market_last_close"] = tracking["market"].get("last_close")
        result["meta"]["market_last_candle_at"] = tracking["market"].get("lastCandleAt")
        result["signal_tracking"] = {
            "active": public_signal_row(tracking.get("active_signal")),
            "lastPrice": tracking.get("last_price"),
        }
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/signals/history")
@subscription_required
def signal_history(user):
    symbol = request.args.get("symbol", "").strip() or None
    limit = max(1, min(int(request.args.get("limit", 50)), 200))
    rows = list_signal_history(user["id"], symbol, limit)
    return jsonify({
        "ok": True,
        "signals": [public_signal_row(r) for r in rows],
    })


@app.route("/api/signals/<signal_ref>")
@subscription_required
def signal_by_ref(user, signal_ref):
    sid = parse_signal_id_ref(signal_ref)
    if sid is None:
        return jsonify({"ok": False, "error": "invalid signal id"}), 400
    row = get_signal_by_id(user["id"], sid)
    if not row:
        return jsonify({"ok": False, "error": "signal not found"}), 404
    return jsonify({"ok": True, "signal": public_signal_row(row)})


@app.route("/api/market-status")
@subscription_required
def market_status(_user):
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    snap = fetch_m5_market_snapshot(symbol)
    market = snap.get("market") or {}
    return jsonify({
        "ok": True,
        "symbol": symbol,
        "status": market.get("status", "UNKNOWN"),
        "reason": market.get("reason"),
        "lastClose": market.get("last_close"),
        "lastPrice": (snap.get("last_bar") or {}).get("close"),
    })


init_db()
ensure_admin_bootstrap()
start_signal_ticker(interval_sec=45)
start_telegram_poller()
start_subscription_guard(interval_sec=120)

if __name__ == "__main__":
    print(f"Alpha Analyser API on :{PORT}")
    print(f"  MT5 upstream: {os.environ.get('MT5_VPS_URL', 'http://13.42.76.172:8080')}")
    print(f"  Auth DB: {os.environ.get('ANALYSER_DB_PATH', ROOT / 'analyser.db')}")
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
