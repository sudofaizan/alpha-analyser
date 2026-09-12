// @ts-nocheck
/** Trade signal display — prefers server-side smart signal from API. */

function fmtPrice(p) {
  if (p == null || Number.isNaN(+p)) return "—";
  const n = Math.abs(+p);
  return (+p).toFixed(n >= 100 ? 2 : n >= 10 ? 3 : 5);
}

function isPullbackScenario(scenario) {
  return /pullback|retest|bounce to|fade toward/i.test(String(scenario || ""));
}

/** Client fallback when API has no trade_signal (older backend). */
export function buildTradeSignalFallback(nm, lastPrice, symbol) {
  if (!nm || !nm.dir || lastPrice == null || !Number.isFinite(+lastPrice)) return null;

  const action = nm.dir === "UP" ? "BUY" : nm.dir === "DOWN" ? "SELL" : null;
  if (!action) return null;

  const sl = +nm.invalid;
  const tp1 = +nm.t1;
  const tp2 = nm.t2 != null ? +nm.t2 : null;
  if (!Number.isFinite(sl) || !Number.isFinite(tp1)) return null;

  let orderType = "MARKET";
  let entry = +lastPrice;
  let entryNote = "Enter at market (current price)";
  let strategy = "UNCONFIRMED_MARKET";
  let confidenceTier = "MEDIUM";

  if (isPullbackScenario(nm.scenario) && Number.isFinite(tp1)) {
    const distPct = Math.abs(entry - tp1) / Math.max(entry, 1e-9);
    if (distPct > 0.0008) {
      orderType = "LIMIT";
      entry = tp1;
      strategy = "UNCONFIRMED_LIMIT";
      confidenceTier = "LOW";
      entryNote = action === "BUY"
        ? "Buy limit at support retest — no confirmation data"
        : "Sell limit at resistance retest — no confirmation data";
    }
  }

  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp1 - entry);
  const rr = risk > 1e-9 ? (reward / risk).toFixed(2) : "—";

  return normalizeSignal({
    symbol: symbol || "—",
    action,
    orderType,
    strategy,
    confidenceTier,
    entry,
    entryFmt: fmtPrice(entry),
    sl,
    slFmt: fmtPrice(sl),
    tp1,
    tp1Fmt: fmtPrice(tp1),
    tp2: tp2 != null && Number.isFinite(tp2) ? tp2 : null,
    tp2Fmt: tp2 != null && Number.isFinite(tp2) ? fmtPrice(tp2) : "—",
    rr,
    confidence: nm.confidence,
    scenario: nm.scenario || "—",
    entryNote,
    confirmations: [],
    waitFor: null,
    currentPrice: lastPrice,
  });
}

function normalizeSignal(sig) {
  if (!sig) return null;
  return {
    ...sig,
    entryFmt: sig.entryFmt || fmtPrice(sig.entry),
    slFmt: sig.slFmt || fmtPrice(sig.sl),
    tp1Fmt: sig.tp1Fmt || fmtPrice(sig.tp1),
    tp2Fmt: sig.tp2Fmt || (sig.tp2 != null ? fmtPrice(sig.tp2) : "—"),
    confirmations: sig.confirmations || [],
  };
}

/** @param apiSignal render.trade_signal from server */
export function resolveTradeSignal(apiSignal, nm, lastPrice, symbol) {
  if (apiSignal?.action) {
    return normalizeSignal({ ...apiSignal, symbol: apiSignal.symbol || symbol });
  }
  return buildTradeSignalFallback(nm, lastPrice, symbol);
}

export function tierLabel(tier) {
  if (tier === "HIGH") return "High confidence";
  if (tier === "LOW") return "Low confidence";
  return "Medium confidence";
}

export function strategyLabel(strategy) {
  const map = {
    CONFIRMED_PULLBACK: "Confirmed → limit on pullback",
    CONFIRMED_MARKET: "Confirmed → market now",
    UNCONFIRMED_LIMIT: "Limit without confirmation",
    UNCONFIRMED_MARKET: "Direct market (unconfirmed)",
  };
  return map[strategy] || strategy || "—";
}

export function signalSummaryText(sig) {
  if (!sig) return "";
  const lines = [
    `AlphaANALYSER · ${sig.symbol}`,
  ];
  if (sig.signalId) lines.push(`Signal ID: ${sig.signalId}`);
  lines.push(
    `${sig.action} · ${sig.orderType} · ${tierLabel(sig.confidenceTier)}`,
    `Strategy: ${strategyLabel(sig.strategy)}`,
  );
  if (sig.confirmations?.length) {
    lines.push(`Confirmations: ${sig.confirmations.join(", ")}`);
  }
  if (sig.waitFor) lines.push(sig.waitFor);
  lines.push(
    `Entry: ${sig.entryFmt}`,
    `SL: ${sig.slFmt}`,
    `TP1: ${sig.tp1Fmt}`,
  );
  if (sig.tp2Fmt && sig.tp2Fmt !== "—") lines.push(`TP2: ${sig.tp2Fmt}`);
  lines.push(
    `R:R 1:${sig.rr || "—"}`,
    `Confidence: ~${sig.confidence}%`,
    sig.scenario,
    sig.entryNote,
  );
  return lines.join("\n");
}
