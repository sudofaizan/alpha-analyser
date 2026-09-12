// @ts-nocheck
/** Build MT5-style order suggestion from next-move projection. */

function fmtPrice(p) {
  if (p == null || Number.isNaN(+p)) return "—";
  const n = Math.abs(+p);
  return (+p).toFixed(n >= 100 ? 2 : n >= 10 ? 3 : 5);
}

function isPullbackScenario(scenario) {
  return /pullback|retest|bounce to|fade toward/i.test(String(scenario || ""));
}

/**
 * @param {object|null} nm - render.next_move from API
 * @param {number|null} lastPrice - last candle close
 * @param {string} symbol
 */
export function buildTradeSignal(nm, lastPrice, symbol) {
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

  if (isPullbackScenario(nm.scenario) && Number.isFinite(tp1)) {
    const distPct = Math.abs(entry - tp1) / Math.max(entry, 1e-9);
    if (distPct > 0.0008) {
      orderType = "LIMIT";
      entry = tp1;
      entryNote = action === "BUY"
        ? "Buy limit at support retest (T1)"
        : "Sell limit at resistance retest (T1)";
    }
  }

  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp1 - entry);
  const rr = risk > 1e-9 ? reward / risk : null;

  return {
    symbol: symbol || "—",
    action,
    orderType,
    entry,
    entryFmt: fmtPrice(entry),
    sl,
    slFmt: fmtPrice(sl),
    tp1,
    tp1Fmt: fmtPrice(tp1),
    tp2: tp2 != null && Number.isFinite(tp2) ? tp2 : null,
    tp2Fmt: tp2 != null && Number.isFinite(tp2) ? fmtPrice(tp2) : "—",
    riskPts: risk,
    rewardPts: reward,
    rr: rr != null ? rr.toFixed(2) : "—",
    confidence: nm.confidence,
    scenario: nm.scenario || "—",
    wave: nm.waveLabel || "—",
    htf: nm.htf || "—",
    entryNote,
    generatedAt: Date.now(),
  };
}

export function signalSummaryText(sig) {
  if (!sig) return "";
  const lines = [
    `AlphaANALYSER · ${sig.symbol}`,
    `${sig.action} · ${sig.orderType}`,
    `Entry: ${sig.entryFmt}`,
    `SL: ${sig.slFmt}`,
    `TP1: ${sig.tp1Fmt}`,
  ];
  if (sig.tp2Fmt && sig.tp2Fmt !== "—") lines.push(`TP2: ${sig.tp2Fmt}`);
  lines.push(`R:R 1:${sig.rr}`, `Confidence: ~${sig.confidence}%`, sig.scenario);
  return lines.join("\n");
}
