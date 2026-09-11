"""
Alpha Analyser — server-side overlay + next-move engine.
Logic stays on VPS; frontend only draws the render spec (no reasoning exposed).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PA_OVERLAY_BARS = 280
HTF_MAP = {"M1": "M15", "M5": "H1", "M15": "H4", "M30": "H4", "H1": "D1", "H4": "W1"}


def _iso_to_unix(iso: str | None) -> int | None:
    if not iso:
        return None
    s = str(iso).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif "+" not in s and "T" in s:
        s = s + "+00:00"
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def swing_lookback(tf: str) -> int:
    return {"M1": 8, "M5": 6, "M15": 5, "M30": 4, "H1": 4, "H4": 3, "D1": 3}.get(tf.upper(), 5)


def find_swings(bars: list[dict[str, Any]], lookback: int = 3) -> dict[str, list]:
    highs, lows = [], []
    n = len(bars)
    for i in range(lookback, n - lookback):
        h, lo = bars[i]["high"], bars[i]["low"]
        if all(bars[i - j]["high"] < h and bars[i + j]["high"] < h for j in range(1, lookback + 1)):
            highs.append({"index": i, "time": bars[i]["time"], "price": h})
        if all(bars[i - j]["low"] > lo and bars[i + j]["low"] > lo for j in range(1, lookback + 1)):
            lows.append({"index": i, "time": bars[i]["time"], "price": lo})
    return {"highs": highs, "lows": lows}


def classify_structure(highs: list, lows: list) -> dict[str, str]:
    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "NEUTRAL", "label": "—"}
    h1, h2 = highs[-2]["price"], highs[-1]["price"]
    l1, l2 = lows[-2]["price"], lows[-1]["price"]
    hh, hl = h2 > h1, l2 > l1
    lh, ll = h2 < h1, l2 < l1
    if hh and hl:
        return {"trend": "UP", "label": "HH + HL"}
    if lh and ll:
        return {"trend": "DOWN", "label": "LH + LL"}
    if hh and ll:
        return {"trend": "NEUTRAL", "label": "HH + LL (range/expansion)"}
    if lh and hl:
        return {"trend": "NEUTRAL", "label": "LH + HL (compression)"}
    return {"trend": "NEUTRAL", "label": "Mixed"}


def dedupe_breaks(breaks: list, min_gap: int = 12) -> list:
    out = []
    for b in breaks:
        if not out or b["index"] - out[-1]["index"] >= min_gap or b.get("type") == "ChoCH":
            out.append(b)
    return out


def pullback_markers(swings: dict) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    lows, highs = swings["lows"], swings["highs"]
    if len(lows) >= 2:
        prev, cur = lows[-2], lows[-1]
        markers.append({
            "time": cur["time"],
            "position": "belowBar",
            "shape": "circle",
            "color": "#38bdf8",
            "text": "HL" if cur["price"] > prev["price"] else "LL",
        })
    if len(highs) >= 2:
        prev, cur = highs[-2], highs[-1]
        last_l_time = lows[-1]["time"] if lows else 0
        if cur["time"] > last_l_time:
            markers.append({
                "time": cur["time"],
                "position": "aboveBar",
                "shape": "circle",
                "color": "#94a3b8",
                "text": "HH" if cur["price"] > prev["price"] else "LH",
            })
    return markers


def bos_markers(breaks: list, max_n: int = 5) -> list[dict[str, Any]]:
    return [{
        "time": b["time"],
        "position": "aboveBar" if b["direction"] == "BULLISH" else "belowBar",
        "shape": "square",
        "color": "#fbbf24" if b.get("type") == "ChoCH" else "#e2e8f0",
        "text": b.get("type", "BOS"),
    } for b in breaks[-max_n:]]


def detect_confirmations(
    bars: list[dict[str, Any]],
    swings: dict,
    lookback_bars: int = 35,
    max_markers: int = 2,
) -> list[dict[str, Any]]:
    """R3 — last pin/engulf candles near recent swing highs/lows (matches v1)."""
    if len(bars) < 2:
        return []
    markers: list[dict[str, Any]] = []
    start = max(0, len(bars) - lookback_bars)
    near_pct = 0.0012
    key_levels = swings["highs"][-2:] + swings["lows"][-2:]
    if not key_levels:
        return []

    for i in range(len(bars) - 1, start, -1):
        c, p = bars[i], bars[i - 1]
        body = abs(c["close"] - c["open"])
        upper_wick = c["high"] - max(c["open"], c["close"])
        lower_wick = min(c["open"], c["close"]) - c["low"]
        bull_engulf = (
            c["close"] > c["open"] and p["close"] < p["open"]
            and c["close"] >= p["open"] and c["open"] <= p["close"]
        )
        bear_engulf = (
            c["close"] < c["open"] and p["close"] > p["open"]
            and c["close"] <= p["open"] and c["open"] >= p["close"]
        )
        bull_pin = lower_wick > body * 2.5 and lower_wick > upper_wick * 2 and c["close"] > c["open"]
        bear_pin = upper_wick > body * 2.5 and upper_wick > lower_wick * 2 and c["close"] < c["open"]

        near_level = any(abs(s["price"] - c["close"]) / s["price"] < near_pct for s in key_levels)
        if not near_level:
            continue

        if bull_engulf or bull_pin:
            markers.append({
                "time": c["time"],
                "position": "belowBar",
                "shape": "arrowUp",
                "color": "#a78bfa",
                "text": "Engulf" if bull_engulf else "Pin",
            })
        elif bear_engulf or bear_pin:
            markers.append({
                "time": c["time"],
                "position": "aboveBar",
                "shape": "arrowDown",
                "color": "#a78bfa",
                "text": "Engulf" if bear_engulf else "Pin",
            })
        if len(markers) >= max_markers:
            break

    markers.reverse()
    return markers


def rsi_wilder(closes: list[float], period: int = 14) -> list[float]:
    n = len(closes)
    out = [float("nan")] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)

    def _rsi(ag: float, al: float) -> float:
        if al == 0 and ag == 0:
            return 50.0
        if al == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def _pivot_swing_high(bars: list[dict[str, Any]], i: int, left: int, right: int) -> bool:
    h = bars[i]["high"]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if j < 0 or j >= len(bars):
            return False
        if bars[j]["high"] >= h:
            return False
    return True


def _pivot_swing_low(bars: list[dict[str, Any]], i: int, left: int, right: int) -> bool:
    lo = bars[i]["low"]
    for j in range(i - left, i + right + 1):
        if j == i:
            continue
        if j < 0 or j >= len(bars):
            return False
        if bars[j]["low"] <= lo:
            return False
    return True


def detect_rsi_divergence(
    bars: list[dict[str, Any]],
    *,
    rsi_period: int = 14,
    swing_left: int = 2,
    swing_right: int = 2,
    min_pivot_gap: int = 5,
    max_signals: int = 12,
    overlay_bars: int = PA_OVERLAY_BARS,
) -> list[dict[str, Any]]:
    """
    TradingView-style RSI divergence on price pivots:
      Bull: price lower low + RSI higher low
      Bear: price higher high + RSI lower high
    """
    if len(bars) < rsi_period + swing_left + swing_right + 10:
        return []

    t_min = bars[-overlay_bars]["time"] if len(bars) > overlay_bars else bars[0]["time"]
    closes = [b["close"] for b in bars]
    rsi = rsi_wilder(closes, rsi_period)
    events: list[dict[str, Any]] = []
    piv_low: list[tuple[int, float, float]] = []
    piv_high: list[tuple[int, float, float]] = []

    start = max(swing_left, rsi_period) + swing_right
    for k in range(start + swing_right, len(bars) - 1):
        i = k - swing_right
        ri = rsi[i]
        if ri != ri:
            continue

        if _pivot_swing_low(bars, i, swing_left, swing_right):
            piv_low.append((i, bars[i]["low"], ri))
            if len(piv_low) >= 2:
                i1, l1, r1 = piv_low[-2]
                i2, l2, r2 = piv_low[-1]
                if i2 - i1 >= min_pivot_gap and l2 < l1 - 1e-12 and r2 > r1 + 1e-12:
                    events.append({
                        "side": "bull",
                        "signal_time": bars[k]["time"],
                        "p1_time": bars[i1]["time"],
                        "p1_price": l1,
                        "p1_rsi": round(r1, 1),
                        "p2_time": bars[i2]["time"],
                        "p2_price": l2,
                        "p2_rsi": round(r2, 1),
                    })

        if _pivot_swing_high(bars, i, swing_left, swing_right):
            piv_high.append((i, bars[i]["high"], ri))
            if len(piv_high) >= 2:
                i1, h1, r1 = piv_high[-2]
                i2, h2, r2 = piv_high[-1]
                if i2 - i1 >= min_pivot_gap and h2 > h1 + 1e-12 and r2 < r1 - 1e-12:
                    events.append({
                        "side": "bear",
                        "signal_time": bars[k]["time"],
                        "p1_time": bars[i1]["time"],
                        "p1_price": h1,
                        "p1_rsi": round(r1, 1),
                        "p2_time": bars[i2]["time"],
                        "p2_price": h2,
                        "p2_rsi": round(r2, 1),
                    })

    events = [e for e in events if e["p2_time"] >= t_min]
    return events[-max_signals:]


def build_rsi_div_layer(events: list[dict[str, Any]]) -> dict[str, list]:
    layer = _empty_layer()
    for e in events:
        bull = e["side"] == "bull"
        col = "#22c55e" if bull else "#ef4444"
        _add_polyline(
            layer,
            [
                {"time": e["p1_time"], "price": e["p1_price"]},
                {"time": e["p2_time"], "price": e["p2_price"]},
            ],
            col,
            2,
            2,
        )
        rsi_txt = f"{e['p1_rsi']}→{e['p2_rsi']}"
        layer["markers"].append({
            "time": e["signal_time"],
            "position": "belowBar" if bull else "aboveBar",
            "shape": "arrowUp" if bull else "arrowDown",
            "color": col,
            "text": f"{'Bull' if bull else 'Bear'} {rsi_txt}",
        })
    return layer


def liquidity_sweep_markers(bars: list, swings: dict, max_n: int = 5) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    start = max(0, len(bars) - 150)
    for i in range(start, len(bars)):
        c = bars[i]
        for sl in swings["lows"][-8:]:
            if c["low"] < sl["price"] and c["close"] > sl["price"]:
                markers.append({"time": c["time"], "position": "belowBar", "shape": "circle", "color": "#64748b", "text": "×"})
        for sh in swings["highs"][-8:]:
            if c["high"] > sh["price"] and c["close"] < sh["price"]:
                markers.append({"time": c["time"], "position": "aboveBar", "shape": "circle", "color": "#64748b", "text": "×"})
    seen: set[int] = set()
    uniq: list[dict[str, Any]] = []
    for m in markers:
        if m["time"] not in seen:
            seen.add(m["time"])
            uniq.append(m)
    return uniq[-max_n:]


def find_structure_breaks(bars: list, swings: dict) -> list:
    breaks = []
    n = len(bars)
    for sh in swings["highs"]:
        idx = sh.get("index") or 0
        for i in range(idx + 1, min(idx + 50, n)):
            if bars[i]["close"] > sh["price"]:
                breaks.append({
                    "index": i, "time": bars[i]["time"], "level": sh["price"],
                    "direction": "BULLISH", "swingTime": sh["time"],
                })
                break
    for sl in swings["lows"]:
        idx = sl.get("index") or 0
        for i in range(idx + 1, min(idx + 50, n)):
            if bars[i]["close"] < sl["price"]:
                breaks.append({
                    "index": i, "time": bars[i]["time"], "level": sl["price"],
                    "direction": "BEARISH", "swingTime": sl["time"],
                })
                break
    breaks.sort(key=lambda x: x["index"])
    prev = None
    for b in breaks:
        b["type"] = "ChoCH" if prev and b["direction"] != prev else "BOS"
        prev = b["direction"]
    return dedupe_breaks(breaks)


def build_zigzag(highs: list, lows: list, max_points: int = 14) -> list:
    all_pts = sorted(
        [{**h, "kind": "H"} for h in highs] + [{**l, "kind": "L"} for l in lows],
        key=lambda p: p["time"],
    )
    zig: list = []
    for p in all_pts:
        if not zig:
            zig.append(p)
            continue
        last = zig[-1]
        if last["kind"] == p["kind"]:
            if p["kind"] == "H" and p["price"] > last["price"]:
                zig[-1] = p
            elif p["kind"] == "L" and p["price"] < last["price"]:
                zig[-1] = p
        else:
            zig.append(p)
    return zig[-max_points:]


def calc_atr(bars: list, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 1.0
    total = 0.0
    for i in range(len(bars) - period, len(bars)):
        c, p = bars[i], bars[i - 1]
        total += max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
    return total / period


def bar_duration_sec(bars: list) -> int:
    if len(bars) < 2:
        return 300
    d = bars[-1]["time"] - bars[-2]["time"]
    return int(d) if d > 0 else 300


def future_time(t: int, bars_ahead: int, bar_sec: int) -> int:
    return t + bars_ahead * bar_sec


def htf_bias(analysis: dict | None) -> dict[str, str]:
    h = (analysis or {}).get("overall_trend") or "NEUTRAL"
    if "STRONG_UP" in h:
        return {"text": "STRONG UP", "bias": "UP"}
    if "STRONG_DOWN" in h:
        return {"text": "STRONG DOWN", "bias": "DOWN"}
    if h == "UP":
        return {"text": "UP", "bias": "UP"}
    if h == "DOWN":
        return {"text": "DOWN", "bias": "DOWN"}
    return {"text": "NEUTRAL", "bias": "NEUTRAL"}


def path_direction_label(path: list, price: float) -> str:
    if len(path) < 2:
        return "—"
    if len(path) >= 3:
        leg1 = "DOWN" if path[1]["price"] < price else "UP" if path[1]["price"] > price else "FLAT"
        leg2 = "DOWN" if path[2]["price"] < path[1]["price"] else "UP" if path[2]["price"] > path[1]["price"] else "FLAT"
        if leg1 not in ("FLAT", leg2) and leg2 != "FLAT" and leg1 != leg2:
            return f"{leg1} → {leg2}"
    end = path[-1]["price"]
    return "UP" if end > price else "DOWN" if end < price else "FLAT"


def compute_next_move_public(
    bars: list,
    swings: dict,
    structure: dict,
    analysis: dict | None,
    order_blocks: list | None,
) -> dict[str, Any] | None:
    """Next-move projection — public fields only (no branch/reasoning)."""
    if not swings["highs"] or not swings["lows"]:
        return None
    price = bars[-1]["close"]
    last_bar = bars[-1]
    bar_sec = bar_duration_sec(bars)
    last_h = swings["highs"][-1]
    last_l = swings["lows"][-1]
    leg = last_h["price"] - last_l["price"]
    if leg <= 0:
        return None
    pos = (price - last_l["price"]) / leg
    atr = float((analysis or {}).get("chart", {}).get("atr") or calc_atr(bars))
    htf = htf_bias(analysis)
    fib50 = last_l["price"] + leg * 0.5
    fib618 = last_l["price"] + leg * 0.618
    ext_down = last_l["price"] - leg
    ext_up = last_h["price"] + leg
    fib50_up = last_h["price"] - leg * 0.5
    fib618_up = last_h["price"] - leg * 0.618

    def boost(base: int) -> int:
        c = base
        if htf["bias"] == structure["trend"] and structure["trend"] != "NEUTRAL":
            c += 12
        if htf["bias"] not in ("NEUTRAL", structure["trend"]) and structure["trend"] != "NEUTRAL":
            c -= 10
        return max(35, min(88, c))

    trend = structure["trend"]
    scenario = dir_ = wave = None
    t1 = t2 = invalid = None
    path: list[dict] = []
    fib_levels: list[dict] = []

    if trend == "DOWN":
        if pos < 0.4:
            scenario, dir_, wave = "Bounce to resistance (Wave B)", "UP", "Wave B up → Wave C down (1:1)"
            t1, t2, invalid = last_h["price"], ext_down, last_l["price"] - atr * 0.5
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 10, bar_sec), "price": t1},
                {"time": future_time(last_bar["time"], 22, bar_sec), "price": t2},
            ]
            fib_levels = [{"price": fib50, "label": "50%"}, {"price": fib618, "label": "61.8%"}, {"price": ext_down, "label": "1:1"}]
            confidence = boost(52)
        elif pos > 0.6:
            scenario, dir_, wave = "Reject resistance · continuation down", "DOWN", "Wave C extension to 1:1 target"
            t1, t2, invalid = last_l["price"], ext_down, last_h["price"] + atr * 0.35
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 8, bar_sec), "price": t1},
                {"time": future_time(last_bar["time"], 18, bar_sec), "price": t2},
            ]
            fib_levels = [{"price": ext_down, "label": "1:1"}, {"price": last_l["price"], "label": "Sup"}]
            confidence = boost(65)
        else:
            scenario, dir_, wave = "Pullback to resistance, then sell", "DOWN", "Retest Res → measured move down"
            t1, t2, invalid = last_h["price"], ext_down, last_h["price"] + atr * 0.4
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 7, bar_sec), "price": t1},
                {"time": future_time(last_bar["time"], 17, bar_sec), "price": t2},
            ]
            fib_levels = [{"price": fib618, "label": "61.8%"}, {"price": ext_down, "label": "1:1"}]
            confidence = boost(58)
    elif trend == "UP":
        if pos > 0.6:
            scenario, dir_, wave = "Pullback to support (Wave B)", "DOWN", "Wave B down → Wave C up (1:1)"
            t1, t2, invalid = last_l["price"], ext_up, last_h["price"] + atr * 0.5
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 10, bar_sec), "price": t1},
                {"time": future_time(last_bar["time"], 22, bar_sec), "price": t2},
            ]
            fib_levels = [{"price": fib50_up, "label": "50%"}, {"price": fib618_up, "label": "61.8%"}, {"price": ext_up, "label": "1:1"}]
            confidence = boost(52)
        elif pos < 0.4:
            scenario, dir_, wave = "Bounce support · continuation up", "UP", "Wave C extension to 1:1 target"
            t1, t2, invalid = last_h["price"], ext_up, last_l["price"] - atr * 0.35
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 8, bar_sec), "price": t1},
                {"time": future_time(last_bar["time"], 18, bar_sec), "price": t2},
            ]
            fib_levels = [{"price": ext_up, "label": "1:1"}, {"price": last_h["price"], "label": "Res"}]
            confidence = boost(65)
        else:
            scenario, dir_, wave = "Pullback to support, then buy", "UP", "Retest Sup → measured move up"
            t1, t2, invalid = last_l["price"], ext_up, last_l["price"] - atr * 0.4
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 7, bar_sec), "price": t1},
                {"time": future_time(last_bar["time"], 17, bar_sec), "price": t2},
            ]
            fib_levels = [{"price": fib618_up, "label": "61.8%"}, {"price": ext_up, "label": "1:1"}]
            confidence = boost(58)
    else:
        if pos < 0.5:
            scenario, dir_, wave = "Range · bounce toward resistance", "UP", "Range low → mid/fib target"
            t1, t2, invalid = last_h["price"], fib618, last_l["price"] - atr * 0.4
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 12, bar_sec), "price": t1},
            ]
            fib_levels = [{"price": fib50, "label": "50%"}, {"price": fib618, "label": "61.8%"}]
            confidence = boost(48)
        else:
            scenario, dir_, wave = "Range · fade toward support", "DOWN", "Range high → mid/fib target"
            t1, t2, invalid = last_l["price"], fib50, last_h["price"] + atr * 0.4
            path = [
                {"time": last_bar["time"], "price": price},
                {"time": future_time(last_bar["time"], 12, bar_sec), "price": t1},
            ]
            fib_levels = [{"price": fib50_up, "label": "50%"}, {"price": fib618_up, "label": "61.8%"}]
            confidence = boost(48)

    path_label = path_direction_label(path, price)
    end_price = path[-1]["price"]
    net_dir = "DOWN" if end_price < price else "UP" if end_price > price else dir_

    return {
        "scenario": scenario,
        "dir": net_dir,
        "pathLabel": path_label,
        "confidence": confidence,
        "t1": t1,
        "t2": t2,
        "invalid": invalid,
        "waveLabel": wave,
        "htf": htf["text"],
        "posInRange": round(pos, 4),
        "path": path,
        "fibLevels": fib_levels[:3],
    }


def _add_line(spec: dict, t0: int, t1: int, price: float, color: str, width: int = 1, style: int = 0) -> None:
    if t0 >= t1:
        return
    spec["lines"].append({"t0": t0, "t1": t1, "price": price, "color": color, "width": width, "style": style})


def _add_polyline(
    spec: dict,
    points: list[dict[str, Any]],
    color: str,
    width: int = 2,
    style: int = 0,
) -> None:
    pts = [
        {"time": int(p["time"]), "price": float(p["price"])}
        for p in points
        if p.get("time") is not None and p.get("price") is not None
    ]
    if len(pts) < 2:
        return
    spec.setdefault("polylines", []).append({"points": pts, "color": color, "width": width, "style": style})


def _add_area(spec: dict, t0: int, t1: int, low: float, high: float, fill: str, border: str) -> None:
    if high <= low or t1 <= t0:
        return
    spec["areas"].append({"t0": t0, "t1": t1, "low": low, "high": high, "fill": fill, "border": border})


def _add_baseline_box(spec: dict, t0: int, t1: int, bottom: float, top: float, bullish: bool, mitigated: bool = False) -> None:
    if top <= bottom or t0 >= t1:
        return
    spec["baselines"].append({
        "t0": t0, "t1": t1, "bottom": bottom, "top": top,
        "bullish": bullish, "mitigated": mitigated,
    })


def _add_price_line(spec: dict, price: float, color: str, title: str, width: int = 1, style: int = 0) -> None:
    spec["price_lines"].append({"price": price, "color": color, "title": title, "width": width, "style": style})


def _smc_ts(bars: list, idx: int) -> int | None:
    if idx < 0 or idx >= len(bars):
        return None
    return bars[idx]["time"]


def _empty_layer() -> dict[str, list]:
    return {"lines": [], "polylines": [], "areas": [], "baselines": [], "price_lines": [], "markers": [], "glow_lines": []}


def _day_key_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def compute_previous_day_levels(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Previous completed UTC day high/low vs current session day."""
    if len(bars) < 2:
        return None
    by_day: dict[str, list[dict[str, Any]]] = {}
    for b in bars:
        by_day.setdefault(_day_key_utc(b["time"]), []).append(b)
    days = sorted(by_day.keys())
    current_day = _day_key_utc(bars[-1]["time"])
    if current_day not in days:
        return None
    idx = days.index(current_day)
    if idx < 1:
        return None
    prev_day = days[idx - 1]
    prev_bars = by_day[prev_day]
    cur_bars = by_day[current_day]
    return {
        "pdh": max(b["high"] for b in prev_bars),
        "pdl": min(b["low"] for b in prev_bars),
        "t0": cur_bars[0]["time"],
        "t1": bars[-1]["time"],
        "prev_day": prev_day,
    }


def build_pd_levels_layer(bars: list[dict[str, Any]]) -> dict[str, Any]:
    layer = _empty_layer()
    pd = compute_previous_day_levels(bars)
    if not pd:
        return layer
    for price, title in ((pd["pdh"], "PDH"), (pd["pdl"], "PDL")):
        layer["glow_lines"].append({
            "t0": pd["t0"],
            "t1": pd["t1"],
            "price": price,
            "title": title,
        })
        _add_price_line(layer, price, "#facc15", title, 1, 2)
    layer["meta"] = {"pdh": pd["pdh"], "pdl": pd["pdl"], "prev_day": pd["prev_day"]}
    return layer


def build_render_spec(
    bars: list[dict[str, Any]],
    analysis: dict[str, Any],
    htf_bars: list[dict[str, Any]] | None,
    chart_tf: str,
) -> dict[str, Any]:
    """Build drawable overlay spec + summary + next_move (public)."""
    r1, r2, r3_l, rsi_div_l, pd_l, smc_l, beluga_l, nm_l = (
        _empty_layer(), _empty_layer(), _empty_layer(), _empty_layer(), _empty_layer(),
        _empty_layer(), _empty_layer(), _empty_layer(),
    )
    if not bars:
        return {
            "summary": {},
            "next_move": None,
            "layers": {
                "r1": r1, "r2": r2, "r3": r3_l, "rsi_div": rsi_div_l, "pd_levels": pd_l,
                "smc": smc_l, "beluga": beluga_l, "next_move": nm_l,
            },
        }

    pa_bars = bars[-PA_OVERLAY_BARS:] if len(bars) > PA_OVERLAY_BARS else bars
    lb = swing_lookback(chart_tf)
    swings = find_swings(pa_bars, lb)
    breaks = find_structure_breaks(pa_bars, swings)
    structure = classify_structure(swings["highs"], swings["lows"])
    if breaks:
        b = breaks[-1]
        structure["label"] = f"{b['type']} · {b['direction'].lower()}"

    obs = list(analysis.get("valid_order_blocks") or analysis.get("order_blocks") or [])
    price = bars[-1]["close"]
    t_end = bars[-1]["time"]
    atr = calc_atr(pa_bars)

    pool = [ob for ob in obs if ob.get("high") is not None and ob.get("low") is not None]
    pool.sort(key=lambda o: abs((o["high"] + o["low"]) / 2 - price))

    # R1 zigzag + BOS + HTF
    start_t = pa_bars[max(0, int(len(pa_bars) * 0.45))]["time"]
    zig = [p for p in build_zigzag(swings["highs"], swings["lows"], 14) if p["time"] >= start_t]
    if len(zig) >= 2:
        _add_polyline(r1, zig, "rgba(248,250,252,0.92)", 2, 0)
    r1["markers"].extend(pullback_markers(swings))
    r1["markers"].extend(bos_markers(breaks))
    r1["markers"].extend(liquidity_sweep_markers(pa_bars, swings))
    for b in breaks[-5:]:
        t0 = max(b.get("swingTime") or 0, pa_bars[max(0, len(pa_bars) - 120)]["time"])
        if t0 < b["time"]:
            _add_line(r1, t0, b["time"], b["level"], "rgba(100,116,139,0.55)", 1, 2)
    htf_tf = HTF_MAP.get(chart_tf.upper())
    if htf_bars and htf_tf:
        htf_lb = swing_lookback(htf_tf)
        htf_sw = find_swings(htf_bars, htf_lb)
        htf_atr = calc_atr(htf_bars)
        if htf_sw["lows"]:
            hl = htf_sw["lows"][-1]
            t0 = max(hl["time"], bars[0]["time"])
            _add_area(r1, t0, t_end, hl["price"] - htf_atr * 0.15, hl["price"] + htf_atr * 0.2,
                      "rgba(56,189,248,0.12)", "rgba(56,189,248,0.5)")
            _add_price_line(r1, hl["price"], "rgba(56,189,248,0.75)", f"{htf_tf} wave low", 2, 2)
            r1["markers"].append({"time": hl["time"], "position": "belowBar", "shape": "circle", "color": "#38bdf8", "text": f"{htf_tf} HL"})
        if htf_sw["highs"]:
            hh = htf_sw["highs"][-1]
            _add_price_line(r1, hh["price"], "rgba(239,68,68,0.45)", f"{htf_tf} wave high", 1, 2)

    # R2 supply/demand + OB zones
    if swings["highs"]:
        lh = swings["highs"][-1]
        pad = atr * 0.4
        _add_area(r2, lh["time"], t_end, lh["price"] - pad, lh["price"] + pad * 0.6,
                  "rgba(239,68,68,0.28)", "rgba(239,68,68,0.65)")
    if swings["lows"]:
        ll = swings["lows"][-1]
        pad = atr * 0.4
        _add_area(r2, ll["time"], t_end, ll["price"] - pad * 0.6, ll["price"] + pad,
                  "rgba(148,163,184,0.25)", "rgba(148,163,184,0.55)")
    for ob in pool[:3]:
        t0 = _iso_to_unix(ob.get("time")) or pa_bars[max(0, len(pa_bars) - 80)]["time"]
        bull = str(ob.get("type", "")).upper().startswith("BULL")
        fill = "rgba(148,163,184,0.32)" if bull else "rgba(239,68,68,0.3)"
        border = "rgba(148,163,184,0.7)" if bull else "rgba(239,68,68,0.75)"
        _add_area(r2, t0, t_end, ob["low"], ob["high"], fill, border)
        _add_price_line(r2, ob["low"] if bull else ob["high"], border, "OB")

    smc = analysis.get("smc") or {}
    for f in [x for x in (smc.get("fvgs") or []) if not x.get("mitigated")][-3:]:
        t0, t1 = _iso_to_unix(f.get("time_start")), _iso_to_unix(f.get("time_end"))
        if t0 and t1 and f.get("top", 0) > f.get("bottom", 0):
            _add_baseline_box(smc_l, t0, t1, f["bottom"], f["top"], f.get("type") == "bullish", False)
    for st in (smc.get("structures") or [])[-4:]:
        t0, t1 = _iso_to_unix(st.get("time_start")), _iso_to_unix(st.get("time_end"))
        if t0 and t1 and st.get("level") is not None:
            col = "#fbbf24" if st.get("label") == "CHoCH" else "#e2e8f0"
            _add_line(smc_l, t0, t1, st["level"], col, 2 if st.get("label") == "CHoCH" else 1, 0)
            smc_l["markers"].append({
                "time": t1,
                "position": "belowBar" if st.get("kind") == "bullish" else "aboveBar",
                "shape": "arrowUp" if st.get("kind") == "bullish" else "arrowDown",
                "color": col,
                "text": st.get("label", "MS"),
            })
    cur = smc.get("current") or {}
    if cur.get("structure_high") is not None:
        th0, th1 = _iso_to_unix(cur.get("high_start")), _iso_to_unix(cur.get("high_end"))
        if th0 and th1:
            _add_line(smc_l, th0, th1, float(cur["structure_high"]), "#3b82f6", 2, 2)
    if cur.get("structure_low") is not None:
        tl0, tl1 = _iso_to_unix(cur.get("low_start")), _iso_to_unix(cur.get("low_end"))
        if tl0 and tl1:
            _add_line(smc_l, tl0, tl1, float(cur["structure_low"]), "#3b82f6", 2, 2)

    for ind in smc.get("inducements") or []:
        t = ind["time"] if isinstance(ind.get("time"), int) else _iso_to_unix(ind.get("time"))
        if t:
            smc_l["markers"].append({"time": t, "position": "inBar", "shape": "circle", "color": "#22d3ee", "text": "⚡"})

    beluga = analysis.get("beluga_smc") or {}
    for f in [x for x in (beluga.get("fvgs") or []) if not x.get("mitigated")][:5]:
        t0, t1 = _iso_to_unix(f.get("time_start")), _iso_to_unix(f.get("time_end"))
        if t0 and t1:
            _add_baseline_box(beluga_l, t0, t1, f["bottom"], f["top"], f.get("type") == "bullish", False)
    for ob in [x for x in (beluga.get("order_blocks") or []) if not x.get("mitigated")][:5]:
        t0, t1 = _iso_to_unix(ob.get("time_start")), _iso_to_unix(ob.get("time_end"))
        if t0 and t1:
            _add_baseline_box(beluga_l, t0, t1, ob["bottom"], ob["top"], ob.get("type") == "bullish", False)

    next_move = compute_next_move_public(bars, swings, structure, analysis, obs)
    if next_move:
        path = next_move["path"]
        end, start = path[-1], path[0]
        bull = end["price"] >= start["price"]
        col = "rgba(34,197,94,.8)" if bull else "rgba(239,68,68,.8)"
        _add_polyline(nm_l, path, col, 2, 2)
        t_col = "rgba(34,197,94,.7)" if bull else "rgba(239,68,68,.7)"
        _add_price_line(nm_l, next_move["t1"], t_col, "T1", 2, 0)
        if next_move.get("t2") is not None and abs(next_move["t2"] - next_move["t1"]) > 1e-9:
            _add_price_line(nm_l, next_move["t2"], t_col, "T2", 1, 2)
        _add_price_line(nm_l, next_move["invalid"], "rgba(239,68,68,.55)", "Inv", 1, 0)
        for f in (next_move.get("fibLevels") or [])[:3]:
            _add_price_line(nm_l, f["price"], "rgba(167,139,250,.4)", f.get("label", "fib"), 1, 3)
        nm_l["markers"].append({
            "time": end["time"],
            "position": "belowBar" if end["price"] >= start["price"] else "aboveBar",
            "shape": "arrowUp" if end["price"] >= start["price"] else "arrowDown",
            "color": col,
            "text": next_move.get("pathLabel") or next_move.get("dir", ""),
        })
        if len(path) >= 3:
            mid = path[1]
            nm_l["markers"].append({
                "time": mid["time"],
                "position": "aboveBar" if mid["price"] < start["price"] else "belowBar",
                "shape": "circle",
                "color": "rgba(251,191,36,0.95)",
                "text": "T1",
            })

    r3_markers = detect_confirmations(bars, swings)
    r3_l["markers"].extend(r3_markers)

    rsi_div_events = detect_rsi_divergence(bars)
    rsi_div_l = build_rsi_div_layer(rsi_div_events)

    pd_l = build_pd_levels_layer(bars)
    pd_meta = pd_l.get("meta") or {}

    summary = {
        "structure": structure.get("trend", "NEUTRAL"),
        "label": structure.get("label", "—"),
        "swing_highs": len(swings["highs"]),
        "swing_lows": len(swings["lows"]),
        "order_blocks": len(pool[:5]),
        "confirmations": len(r3_markers),
        "rsi_divergences": len(rsi_div_events),
        "pdh": pd_meta.get("pdh"),
        "pdl": pd_meta.get("pdl"),
        "pd_day": pd_meta.get("prev_day"),
    }

    public_next = None
    if next_move:
        public_next = {k: v for k, v in next_move.items() if k not in ("path", "fibLevels")}

    return {
        "summary": summary,
        "next_move": public_next,
        "layers": {
            "r1": r1, "r2": r2, "r3": r3_l, "rsi_div": rsi_div_l, "pd_levels": pd_l,
            "smc": smc_l, "beluga": beluga_l, "next_move": nm_l,
        },
    }
