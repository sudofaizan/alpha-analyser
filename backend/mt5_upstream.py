"""Fetch candles + raw analysis from MT5 Windows VPS (internal only — not exposed to browser)."""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

HTF_MAP = {"M1": "M15", "M5": "H1", "M15": "H4", "M30": "H4", "H1": "D1", "H4": "W1"}


def _base_url() -> str:
    url = os.environ.get("MT5_VPS_URL", "http://13.42.76.172:8080").rstrip("/")
    if not url.startswith("http"):
        url = f"http://{url}"
    return url


def _api_key() -> str:
    return os.environ.get("MT5_API_KEY", "alphafx")


def _get(path: str, params: dict[str, str | int]) -> dict[str, Any]:
    q = urllib.parse.urlencode(params)
    url = f"{_base_url()}{path}?{q}"
    req = urllib.request.Request(
        url,
        headers={"X-API-Key": _api_key(), "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            import json

            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"MT5 VPS HTTP {exc.code}: {body}") from exc


def fetch_candles(symbol: str, timeframe: str, count: int) -> dict[str, Any]:
    return _get("/getCandles", {"symbol": symbol, "timeframe": timeframe, "count": count})


def fetch_analysis(symbol: str, timeframe: str, count: int) -> dict[str, Any]:
    return _get("/getAnalysis", {"symbol": symbol, "timeframe": timeframe, "count": count})


def parse_time_to_unix(t: Any) -> int | None:
    if t is None:
        return None
    if isinstance(t, (int, float)) and t > 0:
        return int(t // 1000) if t > 1e12 else int(t)
    s = str(t).strip()
    if not s:
        return None
    if not s.endswith("Z") and "+" not in s:
        s = s + "Z"
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def candles_to_bars(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    seen: set[int] = set()
    for c in candles:
        t = parse_time_to_unix(c.get("time"))
        if t is None or t in seen:
            continue
        seen.add(t)
        bars.append({
            "time": t,
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": int(c.get("tick_volume") or c.get("volume") or 0),
        })
    bars.sort(key=lambda b: b["time"])
    return bars
