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

from alpha_analyser_engine import HTF_MAP, build_render_spec  # noqa: E402
from mt5_upstream import candles_to_bars, fetch_analysis, fetch_candles  # noqa: E402

app = Flask(__name__)
CORS(app)

API_KEY = os.environ.get("ANALYSER_API_KEY", "alphafx")
PORT = int(os.environ.get("PORT", "8090"))


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
    })


@app.route("/getChartBundle")
def get_chart_bundle():
    _, err = require_key()
    if err:
        return err
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol required"}), 400
    chart_tf = request.args.get("timeframe", "M5").upper()
    count = max(50, min(int(request.args.get("count", 200)), 5000))
    try:
        result = build_chart_bundle(symbol, chart_tf, count)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify(result), (200 if result.get("ok") else 400)


if __name__ == "__main__":
    _load_dotenv()
    print(f"Alpha Analyser API on :{PORT}")
    print(f"  MT5 upstream: {os.environ.get('MT5_VPS_URL', 'http://13.42.76.172:8080')}")
    app.run(host="0.0.0.0", port=PORT, debug=os.environ.get("FLASK_DEBUG") == "1")
