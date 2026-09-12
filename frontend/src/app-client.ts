// @ts-nocheck
/** Alpha Analyser v2 — server-side analysis, client render only. */
import {
  accessMessage,
  authHeaders,
  clearSession,
  fetchMe,
  formatUserDisplay,
  getStoredUser,
  subscriptionDaysLeft,
  subscriptionLabel,
} from "./auth";
import {
  clearOverlays,
  createOverlayState,
  renderServerBundle,
} from "./renderer";
import {
  displaySignalId,
  fetchSignalHistory,
  formatTrackingSummary,
  marketStatusClass,
  marketStatusLabel,
  renderHistoryTable,
  renderTrackingTimelineHtml,
} from "./signal-history";
import {
  connectTelegram,
  disconnectTelegram,
  fetchTelegramStatus,
  telegramStatusLabel,
} from "./telegram";
import {
  resolveTradeSignal,
  signalSummaryText,
  strategyLabel,
  tierLabel,
} from "./trade-signal";

const LightweightCharts = window.LightweightCharts;
const $ = (id) => document.getElementById(id);

const SETTINGS_KEY = "alpha_analyser_v2_settings";
const LEGACY_SETTINGS_KEY = "alpha_analyser_settings";
const CHART_VIEW_KEY = "alpha_analyser_chart_view";
const DEFAULT_VISIBLE_BARS = 72;
const VIEWPORT_RIGHT_PAD = 5;
const AUTO_REFRESH_MS = 5000;
function defaultAnalyserApi() {
  if (import.meta.env.VITE_ANALYSER_API) return import.meta.env.VITE_ANALYSER_API;
  if (import.meta.env.PROD && typeof window !== "undefined") return "";
  return "localhost:8090";
}

function isMt5VpsUrl(url) {
  const u = String(url || "").toLowerCase();
  return u.includes(":8080") || u.includes("13.42.") || u.includes("getcandles");
}

function isInsecureApiOnHttpsPage(url) {
  if (typeof window === "undefined" || window.location.protocol !== "https:") return false;
  const s = String(url || "").trim().toLowerCase();
  if (!s) return false;
  if (s.startsWith("http://")) return true;
  // bare IP or host without scheme → baseUrl() would use http://
  if (/^[\d.]+(?::\d+)?$/.test(s)) return true;
  if (/^[\w.-]+:\d+$/.test(s) && !s.startsWith("https:")) return true;
  return false;
}

function normalizeAnalyserApi(url) {
  const s = String(url || "").trim().replace(/\/$/, "");
  if (!s || isMt5VpsUrl(s) || isInsecureApiOnHttpsPage(s)) return defaultAnalyserApi();
  return s;
}

/** Empty string = same-origin (required when page is HTTPS). */
function baseUrl() {
  const normalized = normalizeAnalyserApi($("apiServer").value);
  if (!normalized) return "";
  let s = normalized;
  if (!/^https?:\/\//i.test(s)) {
    const proto = window.location.protocol === "https:" ? "https:" : "http:";
    s = `${proto}//${s}`;
  }
  if (window.location.protocol === "https:" && s.startsWith("http://")) return "";
  return s.replace(/\/$/, "");
}

function apiUrl(path) {
  const base = baseUrl();
  return base ? `${base}${path}` : path;
}

let chart = null;
let candleSeries = null;
let candleMarkers = null;
let overlayState = null;
let lastCandles = [];
let lastRender = null;
let lastContextKey = "";
let autoRefreshTimer = null;
let loadInProgress = false;
let viewportApplying = false;
let chartViewSaveTimer = null;
let sessionRepaintHooked = false;
let lastTradeSignal = null;
let lastActiveVirtualSignal = null;
let lastMarketStatus = null;

function chartContextKey() {
  return [
    $("dataSource").value,
    $("symbol").value.trim().toUpperCase(),
    $("timeframe").value,
    $("barCount").value,
    $("apiServer").value.trim(),
  ].join("|");
}

function loadSavedChartView() {
  try {
    const all = JSON.parse(localStorage.getItem(CHART_VIEW_KEY) || "{}");
    return all[chartContextKey()] || null;
  } catch (_) { return null; }
}

function saveChartView() {
  if (!chart || !lastCandles.length || loadInProgress || viewportApplying) return;
  const ts = chart.timeScale();
  const timeRange = ts.getVisibleRange?.();
  const logical = ts.getVisibleLogicalRange();
  if (!logical || !Number.isFinite(logical.from) || !Number.isFinite(logical.to)) return;
  const n = lastCandles.length;
  let all = {};
  try { all = JSON.parse(localStorage.getItem(CHART_VIEW_KEY) || "{}"); } catch (_) {}
  all[chartContextKey()] = {
    logical: { from: logical.from, to: logical.to },
    fromTime: timeRange?.from ?? lastCandles[Math.max(0, Math.min(n - 1, Math.floor(logical.from)))]?.time,
    toTime: timeRange?.to ?? lastCandles[Math.max(0, Math.min(n - 1, Math.floor(logical.to)))]?.time,
    barCount: n,
    anchorRight: logical.to >= n - 3,
    savedAt: Date.now(),
  };
  localStorage.setItem(CHART_VIEW_KEY, JSON.stringify(all));
}

function scheduleSaveChartView() {
  clearTimeout(chartViewSaveTimer);
  chartViewSaveTimer = setTimeout(saveChartView, 400);
}

function clampLogicalRange(from, to, barCount, minWidth = 14) {
  if (!Number.isFinite(from) || !Number.isFinite(to) || barCount < 1) {
    return defaultChartViewport(barCount);
  }
  const width = Math.max(minWidth, to - from);
  let f = from;
  let t = to;
  if (t > barCount + VIEWPORT_RIGHT_PAD) {
    t = barCount + VIEWPORT_RIGHT_PAD;
    f = t - width;
  }
  if (f < 0) {
    f = 0;
    t = Math.min(barCount + VIEWPORT_RIGHT_PAD, f + width);
  }
  if (t - f < minWidth) t = f + minWidth;
  return { from: f, to: t };
}

function defaultChartViewport(barCount) {
  const visible = Math.min(DEFAULT_VISIBLE_BARS, Math.max(24, barCount - 1));
  return {
    from: Math.max(0, barCount - visible),
    to: barCount - 1 + VIEWPORT_RIGHT_PAD,
  };
}

function clearSavedChartView() {
  try {
    const all = JSON.parse(localStorage.getItem(CHART_VIEW_KEY) || "{}");
    delete all[chartContextKey()];
    localStorage.setItem(CHART_VIEW_KEY, JSON.stringify(all));
  } catch (_) { /* ignore */ }
}

function savedTimeRangeValid(fromT, toT, firstT, lastT) {
  if (!Number.isFinite(fromT) || !Number.isFinite(toT)) return false;
  if (toT < firstT || fromT > lastT) return false;
  if (toT - fromT < 60) return false;
  return true;
}

function isRightPinned(barCount) {
  if (!chart) return true;
  try {
    const range = chart.timeScale().getVisibleLogicalRange();
    if (!range) return true;
    return range.to >= barCount - 3;
  } catch (_) {
    return true;
  }
}

/** Snapshot visible range before setData — time range preferred (stable when user panned). */
function captureViewport() {
  if (!chart) return null;
  try {
    const ts = chart.timeScale();
    const timeRange = ts.getVisibleRange?.();
    if (timeRange && Number.isFinite(timeRange.from) && Number.isFinite(timeRange.to)) {
      return { mode: "time", from: timeRange.from, to: timeRange.to };
    }
    const logical = ts.getVisibleLogicalRange();
    if (logical && Number.isFinite(logical.from) && Number.isFinite(logical.to)) {
      return { mode: "logical", from: logical.from, to: logical.to };
    }
  } catch (_) { /* ignore */ }
  return null;
}

function restoreViewportDeferred(snapshot, rowCount) {
  if (!chart || !snapshot || !rowCount) return;
  viewportApplying = true;
  const apply = () => {
    try {
      const ts = chart.timeScale();
      if (snapshot.mode === "time") {
        ts.setVisibleRange({ from: snapshot.from, to: snapshot.to });
      } else {
        ts.setVisibleLogicalRange(clampLogicalRange(snapshot.from, snapshot.to, rowCount));
      }
      scheduleSaveChartView();
    } catch (_) { /* ignore */ }
    viewportApplying = false;
  };
  requestAnimationFrame(() => requestAnimationFrame(apply));
  setTimeout(apply, 80);
}

function restoreChartViewport(rows, { forceDefault = false } = {}) {
  if (!chart || !rows.length) return;
  const n = rows.length;
  const saved = forceDefault ? null : loadSavedChartView();
  const ts = chart.timeScale();
  const firstT = rows[0].time;
  const lastT = rows[n - 1].time;

  if (!forceDefault && saved?.fromTime != null && saved?.toTime != null
      && savedTimeRangeValid(saved.fromTime, saved.toTime, firstT, lastT)) {
    let fromT = saved.fromTime;
    let toT = saved.toTime;
    if (saved.anchorRight && saved.barCount != null && saved.barCount < n) {
      const span = Math.max(60, toT - fromT);
      toT = lastT + Math.min(span * 0.02, 300);
      fromT = toT - span;
    }
    fromT = Math.max(firstT, fromT);
    toT = Math.min(lastT + 600, Math.max(fromT + 60, toT));
    if (savedTimeRangeValid(fromT, toT, firstT, lastT)) {
      try {
        ts.setVisibleRange({ from: fromT, to: toT });
        return;
      } catch (_) {
        clearSavedChartView();
      }
    } else {
      clearSavedChartView();
    }
  } else if (!forceDefault && (saved?.fromTime != null || saved?.toTime != null)) {
    clearSavedChartView();
  }

  let range = null;
  if (!forceDefault && saved?.logical && Number.isFinite(saved.logical.from) && Number.isFinite(saved.logical.to)) {
    let { from, to } = saved.logical;
    const width = to - from;
    if (saved.anchorRight && saved.barCount != null && saved.logical.to >= saved.barCount - 3) {
      const delta = n - saved.barCount;
      if (delta > 0) {
        to = saved.logical.to + delta;
        from = to - width;
      }
    }
    range = clampLogicalRange(from, to, n);
  } else {
    range = defaultChartViewport(n);
  }

  try {
    ts.setVisibleLogicalRange(range);
  } catch (_) {
    try { ts.setVisibleLogicalRange(defaultChartViewport(n)); } catch (_) {}
  }
}

function applyChartViewport(rows, { forceDefault = false } = {}) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (!chart || !rows.length) return;
      viewportApplying = true;
      try {
        restoreChartViewport(rows, { forceDefault });
      } catch (_) {
        try { chart.timeScale().setVisibleLogicalRange(defaultChartViewport(rows.length)); } catch (_) {}
      } finally {
        viewportApplying = false;
      }
    });
  });
}

function candlesCompatible(prev, next) {
  if (!prev.length || !next.length) return false;
  if (next.length < prev.length) return false;
  const check = Math.min(prev.length, next.length - 1);
  for (let i = 0; i < check; i++) {
    if (prev[i].time !== next[i].time) return false;
  }
  return true;
}

function patchCandles(prev, next) {
  if (!prev.length || !next.length) return false;
  // Same window — only the forming bar changed (no setData, viewport untouched)
  if (prev.length === next.length && prev[0].time === next[0].time
      && prev[prev.length - 1].time === next[next.length - 1].time) {
    candleSeries.update(next[next.length - 1]);
    return true;
  }
  if (next.length > prev.length && candlesCompatible(prev, next)) {
    for (let i = prev.length; i < next.length; i++) candleSeries.update(next[i]);
    if (isRightPinned(prev.length)) {
      const ts = chart.timeScale();
      const range = ts.getVisibleLogicalRange();
      if (range) {
        const delta = next.length - prev.length;
        viewportApplying = true;
        try {
          ts.setVisibleLogicalRange({ from: range.from + delta, to: range.to + delta });
        } catch (_) { /* ignore */ }
        viewportApplying = false;
      }
    }
    return true;
  }
  return false;
}

function chartHostSize(host) {
  const w = host.clientWidth || host.offsetWidth || Math.floor(window.innerWidth * 0.72);
  const h = host.clientHeight || host.offsetHeight || Math.max(320, window.innerHeight - 220);
  return { width: Math.max(200, w), height: Math.max(240, h) };
}

function initChart() {
  if (chart) return;
  const LC = LightweightCharts;
  const host = $("chartHost");
  const { width, height } = chartHostSize(host);
  chart = LC.createChart($("tvChart"), {
    width, height,
    layout: { background: { color: "#0b0f14" }, textColor: "#8b9cb3" },
    grid: { vertLines: { color: "#151c26" }, horzLines: { color: "#151c26" } },
    crosshair: { mode: LC.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#1e2836" },
    timeScale: { borderColor: "#1e2836", timeVisible: true, secondsVisible: false },
  });
  candleSeries = chart.addSeries(LC.CandlestickSeries, {
    upColor: "#22c55e", downColor: "#ef4444",
    borderUpColor: "#22c55e", borderDownColor: "#ef4444",
    wickUpColor: "#22c55e", wickDownColor: "#ef4444",
  });
  candleMarkers = LC.createSeriesMarkers(candleSeries, []);
  overlayState = createOverlayState(chart, candleSeries, candleMarkers);
  new ResizeObserver(() => {
    chart.applyOptions(chartHostSize(host));
  }).observe(host);
  if (!sessionRepaintHooked) {
    sessionRepaintHooked = true;
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
      if (loadInProgress || viewportApplying) return;
      scheduleSaveChartView();
    });
  }
}

function parseTime(t) {
  if (t == null) return NaN;
  if (typeof t === "number" && Number.isFinite(t)) return t > 1e12 ? Math.floor(t / 1000) : Math.floor(t);
  const ms = Date.parse(String(t).trim().endsWith("Z") ? t : `${t}Z`);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : NaN;
}

function candlesToRows(candles) {
  const seen = new Set();
  const rows = [];
  for (const c of candles) {
    const t = parseTime(c.time);
    if (!Number.isFinite(t) || seen.has(t)) continue;
    const row = { time: t, open: +c.open, high: +c.high, low: +c.low, close: +c.close, volume: +(c.volume || c.tick_volume || 0) };
    if (row.high < row.low) continue;
    seen.add(t);
    rows.push(row);
  }
  rows.sort((a, b) => a.time - b.time);
  return rows;
}

function fmtPrice(p) {
  if (p == null || Number.isNaN(p)) return "—";
  const n = Math.abs(p);
  return (+p).toFixed(n >= 100 ? 2 : n >= 10 ? 3 : 5);
}

function toggleState() {
  return {
    priceAction: $("togPriceAction").checked,
    r1: $("togR1").checked,
    r2: $("togR2").checked,
    r3: $("togR3").checked,
    rsiDiv: $("togRsiDiv").checked,
    pdLevels: $("togPdLevels").checked,
    smc: $("togSmc").checked,
    paSmc: $("togPaSmc").checked,
    nextMove: $("togNextMove").checked,
  };
}

function updateSummary(summary) {
  const st = $("sumStructure");
  if (!summary) {
    ["sumStructure", "sumLabel", "sumSH", "sumSL", "sumOB", "sumConf", "sumRsiDiv", "sumPdh", "sumPdl"].forEach((id) => { $(id).textContent = "—"; });
    st.className = "val neu";
    return;
  }
  st.textContent = summary.structure || "—";
  st.className = "val " + (summary.structure === "UP" ? "up" : summary.structure === "DOWN" ? "dn" : "neu");
  $("sumLabel").textContent = summary.label || "—";
  $("sumSH").textContent = String(summary.swing_highs ?? "—");
  $("sumSL").textContent = String(summary.swing_lows ?? "—");
  $("sumOB").textContent = String(summary.order_blocks ?? "—");
  $("sumConf").textContent = String(summary.confirmations ?? 0);
  $("sumRsiDiv").textContent = String(summary.rsi_divergences ?? 0);
  $("sumPdh").textContent = summary.pdh != null ? fmtPrice(summary.pdh) : "—";
  $("sumPdl").textContent = summary.pdl != null ? fmtPrice(summary.pdl) : "—";
}

function updateTradeSignalPanel(nm, symbol, apiSignal = null) {
  const panel = $("signalPanel");
  if (!$("togTradeSignals")?.checked) {
    panel.classList.add("hidden");
    lastTradeSignal = null;
    return;
  }
  const sym = symbol || $("symbol")?.value?.trim();
  const lastPrice = lastCandles.length ? lastCandles[lastCandles.length - 1].close : null;
  const sig = resolveTradeSignal(apiSignal, nm, lastPrice, sym);
  lastTradeSignal = sig;
  if (!sig) {
    panel.classList.remove("hidden");
    $("sigAction").textContent = "—";
    $("sigAction").className = "signal-action";
    $("sigTier").textContent = "—";
    $("sigTier").className = "signal-tier";
    $("sigScenario").textContent = nm ? "Insufficient data for order levels" : "Load chart to generate signal";
    $("sigStrategy").textContent = "—";
    $("sigConfirmations").style.display = "none";
    $("sigWaitFor").style.display = "none";
    $("sigId").textContent = "—";
    ["sigSymbol", "sigOrderType", "sigEntry", "sigSl", "sigTp1", "sigTp2", "sigRr", "sigConf"].forEach((id) => {
      $(id).textContent = "—";
    });
    $("sigEntryNote").textContent = "";
    return;
  }
  panel.classList.remove("hidden");
  const actionEl = $("sigAction");
  actionEl.textContent = sig.action;
  actionEl.className = `signal-action ${sig.action === "BUY" ? "buy" : "sell"}`;
  const tier = (sig.confidenceTier || "MEDIUM").toLowerCase();
  $("sigTier").textContent = tierLabel(sig.confidenceTier);
  $("sigTier").className = `signal-tier ${tier === "high" ? "high" : tier === "low" ? "low" : "medium"}`;
  $("sigScenario").textContent = sig.scenario;
  $("sigStrategy").textContent = strategyLabel(sig.strategy);
  const confEl = $("sigConfirmations");
  if (sig.confirmations?.length) {
    confEl.innerHTML = sig.confirmations.map((c) => `<li>${c}</li>`).join("");
    confEl.style.display = "block";
  } else {
    confEl.innerHTML = "";
    confEl.style.display = "none";
  }
  const waitEl = $("sigWaitFor");
  if (sig.waitFor) {
    waitEl.textContent = sig.waitFor;
    waitEl.style.display = "block";
  } else {
    waitEl.textContent = "";
    waitEl.style.display = "none";
  }
  $("sigSymbol").textContent = sig.symbol;
  $("sigOrderType").textContent = sig.orderType;
  $("sigEntry").textContent = sig.entryFmt;
  $("sigSl").textContent = sig.slFmt;
  $("sigTp1").textContent = sig.tp1Fmt;
  $("sigTp2").textContent = sig.tp2Fmt;
  $("sigRr").textContent = sig.rr ? `1 : ${sig.rr}` : "—";
  $("sigConf").textContent = sig.confidence != null ? `~${sig.confidence}%` : "—";
  $("sigEntryNote").textContent = sig.entryNote || "";
  const planEl = $("sigPlan");
  const plan = sig.tradePlan;
  if (planEl && plan?.steps?.length) {
    planEl.classList.remove("hidden");
    planEl.innerHTML = `<b>${plan.title || "Trade plan"}</b>`
      + (plan.philosophy ? `<div>${plan.philosophy}</div>` : "")
      + `<ol>${plan.steps.map((s) => `<li><b>${s.title || s.phase}</b> — ${s.detail}</li>`).join("")}</ol>`;
  } else if (planEl) {
    planEl.classList.add("hidden");
    planEl.innerHTML = "";
  }
  updateVirtualTrackingPanel(lastActiveVirtualSignal);
}

function updateMarketBadge(meta) {
  const el = $("marketBadge");
  if (!el) return;
  const status = meta?.market_status || lastMarketStatus || "UNKNOWN";
  lastMarketStatus = status;
  el.textContent = marketStatusLabel(status);
  el.className = `market-badge ${marketStatusClass(status)}`;
  const reason = meta?.market_status_reason;
  const lastCandle = meta?.market_last_candle_at;
  const tip = [marketStatusLabel(status), reason, lastCandle ? `Last M5: ${lastCandle}` : ""]
    .filter(Boolean)
    .join(" — ");
  el.title = tip;
}

function updateVirtualTrackingPanel(active) {
  const trackEl = $("sigTrack");
  const idEl = $("sigId");
  if (idEl) {
    idEl.textContent = active ? displaySignalId(active) : "—";
  }
  if (!trackEl) return;
  if (!$("togTradeSignals")?.checked || !active?.tracking) {
    trackEl.classList.add("hidden");
    trackEl.innerHTML = "";
    return;
  }
  const summary = formatTrackingSummary(active.tracking) || [];
  const last = active.lastPrice != null ? `Last price ${fmtPrice(active.lastPrice)}` : "";
  const timeline = renderTrackingTimelineHtml(active.tracking);
  trackEl.innerHTML = [
    summary.length ? summary.join("<br>") : "",
    last ? `<div class="track-note">${last}</div>` : "",
    timeline,
  ].filter(Boolean).join("");
  trackEl.classList.remove("hidden");
}

function applySignalTracking(tracking, meta) {
  lastActiveVirtualSignal = tracking?.active || null;
  updateMarketBadge(meta || {});
  updateVirtualTrackingPanel(lastActiveVirtualSignal);
}

async function loadSignalHistory() {
  const body = $("histBody");
  if (!body) return;
  body.innerHTML = '<p class="hist-empty">Loading…</p>';
  try {
    const sym = $("symbol")?.value?.trim() || "";
    const signals = await fetchSignalHistory(apiUrl, sym, 80);
    renderHistoryTable(body, signals);
  } catch (e) {
    body.innerHTML = `<p class="hist-empty err">${e.message}</p>`;
  }
}

function openSignalHistory() {
  $("histOverlay").classList.add("open");
  $("histOverlay").setAttribute("aria-hidden", "false");
  loadSignalHistory();
}

function closeSignalHistory() {
  $("histOverlay").classList.remove("open");
  $("histOverlay").setAttribute("aria-hidden", "true");
}

function updateNextMovePanel(nm) {
  const box = $("nextMoveBox");
  if (!nm || !$("togNextMove").checked) {
    box.style.display = "none";
    return;
  }
  box.style.display = "block";
  $("nmScenario").textContent = nm.scenario || "—";
  $("nmWave").textContent = nm.waveLabel || "—";
  $("nmPath").textContent = nm.pathLabel || nm.dir || "—";
  const dirEl = $("nmDir");
  dirEl.textContent = nm.dir || "—";
  dirEl.className = "val " + (nm.dir === "UP" ? "up" : nm.dir === "DOWN" ? "dn" : "neu");
  $("nmT1").textContent = fmtPrice(nm.t1);
  $("nmT2").textContent = nm.t2 != null ? fmtPrice(nm.t2) : "—";
  $("nmInv").textContent = fmtPrice(nm.invalid);
  const htfEl = $("nmHtf");
  htfEl.textContent = nm.htf || "—";
  htfEl.className = "val " + (String(nm.htf).includes("UP") ? "up" : String(nm.htf).includes("DOWN") ? "dn" : "neu");
  $("nmConf").textContent = nm.confidence != null
    ? `Confidence ~${nm.confidence}% · range ${Math.round((nm.posInRange || 0) * 100)}%`
    : "—";
}

function ensureNextMoveVisible(rows) {
  if (!chart || !lastRender?.layers?.next_move || !$("togNextMove").checked) return;
  const pts = lastRender.layers.next_move.polylines?.[0]?.points;
  if (!pts || pts.length < 2) return;
  const lastPt = pts[pts.length - 1];
  const lastBar = rows[rows.length - 1];
  if (!lastBar || lastPt.time <= lastBar.time) return;
  viewportApplying = true;
  requestAnimationFrame(() => {
    try {
      const ts = chart.timeScale();
      const n = rows.length;
      const logical = ts.getVisibleLogicalRange();
      if (logical) {
        const width = logical.to - logical.from;
        const to = Math.max(logical.to, n - 1 + VIEWPORT_RIGHT_PAD + 12);
        ts.setVisibleLogicalRange(clampLogicalRange(to - width, to, n));
      }
    } catch (_) { /* ignore */ }
    viewportApplying = false;
  });
}

function applyRenderBundle(render, { redrawOverlays = true, panToNextMove = false, symbol = "" } = {}) {
  if (!render) return;
  lastRender = render;
  updateSummary(render.summary);
  updateNextMovePanel(render.next_move);
  updateTradeSignalPanel(render.next_move, symbol, render.trade_signal);
  if (redrawOverlays && overlayState) {
    renderServerBundle(overlayState, render, toggleState());
    if (panToNextMove) ensureNextMoveVisible(lastCandles);
  }
}

function updateCandles(rows, { preserveViewport = false, contextChanged = false } = {}) {
  initChart();
  if (!rows.length) return false;
  const sameContext = chartContextKey() === lastContextKey && lastCandles.length > 0;
  const preserve = preserveViewport && sameContext && !contextChanged;

  if (preserve && patchCandles(lastCandles, rows)) {
    lastCandles = rows;
    lastContextKey = chartContextKey();
    return true;
  }

  candleSeries.setData(rows);

  if (!preserve) {
    applyChartViewport(rows, { forceDefault: contextChanged || !loadSavedChartView() });
  }

  lastCandles = rows;
  lastContextKey = chartContextKey();
  return true;
}

const TOGGLE_IDS = [
  "togPriceAction", "togR1", "togR2", "togR3", "togRsiDiv", "togPdLevels",
  "togNextMove", "togSmc", "togPaSmc", "togSessions", "togAutoRefresh",
  "togTradeSignals",
];

const TOGGLE_DEFAULTS = {
  togPriceAction: true,
  togR1: true,
  togR2: true,
  togR3: false,
  togRsiDiv: true,
  togPdLevels: true,
  togNextMove: false,
  togSmc: false,
  togPaSmc: false,
  togSessions: false,
  togAutoRefresh: false,
  togTradeSignals: false,
};

function settingsUserKey() {
  const email = currentUser?.email || getStoredUser()?.email;
  return email ? String(email).toLowerCase() : "_guest";
}

function readSettingsStore() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
  } catch (_) {
    return {};
  }
}

function userSettingsFromStore(store) {
  const key = settingsUserKey();
  if (store[key]) return store[key];
  if (store.symbol != null || store.togR1 != null) return store;
  try {
    const legacy = JSON.parse(localStorage.getItem(LEGACY_SETTINGS_KEY) || "{}");
    return { ...legacy, ...store };
  } catch (_) {
    return {};
  }
}

function loadSettings() {
  try {
    const store = readSettingsStore();
    const s = userSettingsFromStore(store);
    $("apiServer").value = normalizeAnalyserApi(s.server || "");
    $("symbol").value = s.symbol || "XAUUSD";
    $("timeframe").value = s.timeframe || "M5";
    $("barCount").value = s.barCount || "500";
    $("dataSource").value = s.dataSource || "api";
    for (const id of TOGGLE_IDS) {
      $(id).checked = s[id] != null ? !!s[id] : !!TOGGLE_DEFAULTS[id];
    }
  } catch (_) {
    $("apiServer").value = defaultAnalyserApi();
  }
  if (!$("apiServer").value && !import.meta.env.PROD) $("apiServer").value = defaultAnalyserApi();
  $("serverField").style.display = $("dataSource").value === "api" ? "flex" : "none";
  updateChartMeta();
}

function saveSettings() {
  const payload = {
    server: $("apiServer").value.trim(),
    symbol: $("symbol").value.trim(),
    timeframe: $("timeframe").value,
    barCount: $("barCount").value,
    dataSource: $("dataSource").value,
  };
  for (const id of TOGGLE_IDS) payload[id] = $(id).checked;
  const key = settingsUserKey();
  let store = readSettingsStore();
  if (store.symbol != null || store.togR1 != null) {
    const flat = userSettingsFromStore(store);
    store = {};
    store[key] = flat;
  }
  store[key] = payload;
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(store));
  updateChartMeta();
}

function updateChartMeta() {
  const el = $("chartMeta");
  if (!el) return;
  const sym = $("symbol")?.value?.trim() || "—";
  const tf = $("timeframe")?.value || "—";
  const bars = $("barCount")?.value || "—";
  const auto = $("togAutoRefresh")?.checked ? " · auto 5s" : "";
  el.textContent = `${sym} · ${tf} · ${bars} bars${auto}`;
}

function setTgStatus(text, kind = "") {
  const el = $("tgStatus");
  if (!el) return;
  el.textContent = text || "—";
  el.className = `cfg-hint tg-status ${kind}`.trim();
}

async function refreshTelegramUi() {
  const hint = $("tgBotHint");
  if (!$("tgUsername")) return;
  try {
    const data = await fetchTelegramStatus();
    const tg = data.telegram || {};
    if (tg.username) $("tgUsername").value = tg.username.startsWith("@") ? tg.username : `@${tg.username}`;
    setTgStatus(telegramStatusLabel(tg), tg.inChannel ? "ok" : tg.status === "pending_bot" ? "warn" : "");
    if (hint) {
      if (data.configured && tg.status === "pending_bot") {
        hint.style.display = "block";
        hint.textContent = `Then open @${tg.botUsername || "bot"} in Telegram and tap Start.`;
      } else {
        hint.style.display = "none";
        hint.textContent = "";
      }
    }
  } catch (e) {
    setTgStatus(e.message, "err");
  }
}

function openConfigure() {
  $("cfgOverlay").classList.add("open");
  $("cfgOverlay").setAttribute("aria-hidden", "false");
  refreshTelegramUi();
}

function closeConfigure() {
  saveSettings();
  $("cfgOverlay").classList.remove("open");
  $("cfgOverlay").setAttribute("aria-hidden", "true");
}

let currentUser = null;
let appAccessGranted = false;

function subBadgeClass(user) {
  if (user?.is_admin) return "admin";
  if (!user?.has_access) return "bad";
  const days = subscriptionDaysLeft(user);
  if (days != null && days <= 3) return "warn";
  return "ok";
}

function updateUserBar(user) {
  $("userDisplayName").textContent = formatUserDisplay(user?.email);
  $("userEmail").textContent = user?.email || "—";
  $("adminLink").style.display = user?.is_admin ? "inline" : "none";
  const badge = $("subBadge");
  badge.textContent = subscriptionLabel(user);
  badge.className = `sub-badge ${subBadgeClass(user)}`;
}

async function refreshUserSession() {
  const user = await fetchMe();
  if (!user) return;
  currentUser = user;
  updateUserBar(currentUser);
  if (!user.has_access) showSubscriptionBlock(user);
  else hideSubscriptionBlock();
}

function showSubscriptionBlock(user) {
  appAccessGranted = false;
  $("subBlockMsg").textContent = accessMessage(user?.access_reason);
  $("subBlock").classList.add("show");
  document.querySelector(".app")?.classList.add("blocked");
  setStatus("Subscription inactive — contact admin", false);
}

function hideSubscriptionBlock() {
  appAccessGranted = true;
  $("subBlock").classList.remove("show");
  document.querySelector(".app")?.classList.remove("blocked");
}

function setStatus(msg, ok = true) {
  $("statusLeft").innerHTML = ok ? `<span class="ok">${msg}</span>` : `<span class="err">${msg}</span>`;
}

async function fetchFromApi(silent = false) {
  const sym = $("symbol").value.trim();
  const tf = $("timeframe").value;
  const count = $("barCount").value;
  const ctx = chartContextKey();
  const contextChanged = !lastContextKey || ctx !== lastContextKey;
  const preserve = lastCandles.length > 0 && !contextChanged;
  setStatus(preserve ? (silent ? "Refreshing…" : "Updating…") : "Loading chart bundle…");

  const api = baseUrl() || window.location.origin;
  if (!appAccessGranted) {
    throw new Error("Subscription inactive — contact admin");
  }
  const res = await fetch(
    apiUrl(`/getChartBundle?symbol=${encodeURIComponent(sym)}&timeframe=${tf}&count=${count}`),
    { headers: authHeaders() },
  );
  const raw = await res.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (_) {
    if (raw.trimStart().startsWith("<")) {
      throw new Error(
        `Analyser API returned HTML — use localhost:8090 (run alpha_analyser_ec2/run.sh), not MT5 VPS :8080. Current: ${api}`,
      );
    }
    throw new Error(`Invalid JSON from ${api}`);
  }
  if (!res.ok || !data.ok || !data.candles) throw new Error(data.error || "Chart bundle failed");

  const rows = candlesToRows(data.candles);
  const viewportSnapshot = preserve ? captureViewport() : null;
  updateCandles(rows, { preserveViewport: preserve, contextChanged });
  applyRenderBundle(data.render, { redrawOverlays: true, panToNextMove: false, symbol: data.symbol || sym });
  applySignalTracking(data.signal_tracking, data.meta);
  if (viewportSnapshot) {
    restoreViewportDeferred(viewportSnapshot, rows.length);
  }

  const m = data.meta || {};
  const smcInfo = m.smc_fvg_count != null
    ? ` · SMC FVG ${m.smc_fvg_count} · ${m.smc_last_break || "—"}`
      + (m.smc_inducement_count != null ? ` · ⚡ ${m.smc_inducement_count}` : "")
      + (m.beluga_ob_count != null ? ` · BB OB ${m.beluga_ob_count}` : "")
    : "";
  $("sumMeta").innerHTML = `<b>${data.symbol || sym}</b> · ${tf} · ${rows.length} bars${smcInfo}<br>Source: Live API · ${new Date().toLocaleString()}`;
  setStatus(`Loaded ${rows.length} bars from API`);
  $("statusRight").textContent = `${sym} ${tf} · EC2 render`;
}

async function fetchFromLocal() {
  setStatus("Local JSON — candles only (overlays need API)");
  const sym = $("symbol").value.trim();
  const tf = $("timeframe").value;
  const want = parseInt($("barCount").value, 10) || 500;
  const roots = ["/candle_data/", "../../candle_data/", "../../../candle_data/"];
  const counts = ["20k", "50k", "5000", "2000"];
  let data = null;
  for (const root of roots) {
    for (const n of counts) {
      try {
        const res = await fetch(`${root}${sym}_${tf}_${n}.json`);
        if (!res.ok) continue;
        const j = await res.json();
        if (j.candles?.length) { data = j; break; }
      } catch (_) {}
    }
    if (data) break;
  }
  if (!data) throw new Error(`No local file for ${sym} ${tf}`);
  let rows = candlesToRows(data.candles);
  if (rows.length > want) rows = rows.slice(-want);
  updateCandles(rows, { preserveViewport: false, contextChanged: true });
  if (overlayState) clearOverlays(overlayState);
  updateSummary(null);
  updateNextMovePanel(null);
  updateTradeSignalPanel(null, sym);
  applySignalTracking(null, null);
  $("sumMeta").innerHTML = `<b>${sym}</b> · ${tf} · ${rows.length} bars · local JSON`;
}

async function loadChart(silent = false) {
  if (!appAccessGranted) return;
  if (loadInProgress) return;
  loadInProgress = true;
  if (!silent) saveSettings();
  try {
    if ($("dataSource").value === "local") await fetchFromLocal();
    else await fetchFromApi(silent);
  } catch (e) {
    setStatus(`Error: ${e.message}`, false);
  } finally {
    loadInProgress = false;
  }
}

function syncAutoRefresh() {
  clearInterval(autoRefreshTimer);
  autoRefreshTimer = null;
  if ($("togAutoRefresh").checked) {
    autoRefreshTimer = setInterval(() => { if (!loadInProgress) loadChart(true); }, AUTO_REFRESH_MS);
  }
}

function onToggleChange(ev) {
  saveSettings();
  if (ev?.target?.id === "togTradeSignals") {
    updateTradeSignalPanel(lastRender?.next_move, $("symbol")?.value?.trim(), lastRender?.trade_signal);
    return;
  }
  if (lastRender && overlayState) {
    renderServerBundle(overlayState, lastRender, toggleState());
    updateNextMovePanel(lastRender.next_move);
    if (ev?.target?.id === "togNextMove" && $("togNextMove").checked) {
      ensureNextMoveVisible(lastCandles);
    }
  }
}

$("btnCopySignal").addEventListener("click", async () => {
  if (!lastTradeSignal) return;
  const copySig = lastActiveVirtualSignal?.signalId
    ? { ...lastTradeSignal, signalId: lastActiveVirtualSignal.signalId }
    : lastTradeSignal;
  const text = signalSummaryText(copySig);
  try {
    await navigator.clipboard.writeText(text);
    $("btnCopySignal").textContent = "Copied!";
    setTimeout(() => { $("btnCopySignal").textContent = "Copy order details"; }, 1500);
  } catch (_) {
    $("btnCopySignal").textContent = "Copy failed";
  }
});

$("btnLoad").addEventListener("click", () => loadChart(false));
$("btnConfigure").addEventListener("click", openConfigure);
$("btnTgConnect")?.addEventListener("click", async () => {
  const raw = $("tgUsername")?.value?.trim().replace(/^@/, "") || "";
  setTgStatus("Connecting…");
  try {
    const { ok, data, error } = await connectTelegram(raw);
    if (!ok) {
      setTgStatus(error || data?.error || "Failed", "err");
      return;
    }
    setTgStatus(data.message || "Done", data.status === "in_channel" ? "ok" : "warn");
    if (data.botLink) {
      $("tgBotHint").style.display = "block";
      $("tgBotHint").innerHTML = `<a href="${data.botLink}" target="_blank" rel="noopener">Open @${data.botUsername} in Telegram</a> and tap Start.`;
    }
    await refreshTelegramUi();
  } catch (e) {
    setTgStatus(e.message, "err");
  }
});
$("btnTgDisconnect")?.addEventListener("click", async () => {
  if (!window.confirm("Remove Telegram channel access?")) return;
  try {
    await disconnectTelegram();
    $("tgUsername").value = "";
    setTgStatus("Disconnected from signal channel", "ok");
    $("tgBotHint").style.display = "none";
  } catch (e) {
    setTgStatus(e.message, "err");
  }
});
$("btnSignalHistory").addEventListener("click", openSignalHistory);
$("btnHistClose").addEventListener("click", closeSignalHistory);
$("btnHistDone").addEventListener("click", closeSignalHistory);
$("btnHistRefresh").addEventListener("click", () => loadSignalHistory());
$("histOverlay").addEventListener("click", (ev) => {
  if (ev.target === $("histOverlay")) closeSignalHistory();
});
document.querySelector(".hist-modal")?.addEventListener("click", (ev) => ev.stopPropagation());
$("btnCfgClose").addEventListener("click", closeConfigure);
$("btnCfgCancel").addEventListener("click", closeConfigure);
$("btnCfgApply").addEventListener("click", () => {
  saveSettings();
  syncAutoRefresh();
  closeConfigure();
  loadChart(false);
});
$("cfgOverlay").addEventListener("click", (ev) => {
  if (ev.target === $("cfgOverlay")) closeConfigure();
});
document.querySelector(".cfg-modal")?.addEventListener("click", (ev) => ev.stopPropagation());
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  if ($("histOverlay").classList.contains("open")) closeSignalHistory();
  else if ($("cfgOverlay").classList.contains("open")) closeConfigure();
});
window.addEventListener("beforeunload", () => saveSettings());
$("togAutoRefresh").addEventListener("change", () => { saveSettings(); syncAutoRefresh(); });
$("dataSource").addEventListener("change", () => {
  $("serverField").style.display = $("dataSource").value === "api" ? "flex" : "none";
  saveSettings();
});
TOGGLE_IDS.forEach((id) => {
  $(id).addEventListener("change", onToggleChange);
});
["symbol", "timeframe", "barCount", "apiServer"].forEach((id) => {
  $(id).addEventListener("change", saveSettings);
  $(id).addEventListener("input", saveSettings);
});

async function probeAnalyserApi() {
  const api = baseUrl() || window.location.origin;
  try {
    const r = await fetch(apiUrl("/health"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const h = await r.json();
    if (h.service === "alpha-analyser-ec2" || h.service === "alpha-analyser") {
      setStatus(`Analyser API ready · upstream ${h.mt5_vps || "MT5 VPS"}`);
      return;
    }
    setStatus(`Connected to ${api} — unexpected service ${h.service || "?"}`, false);
  } catch (_) {
    setStatus(
      window.location.protocol === "https:"
        ? "API unreachable — clear API host field (use same server) and click Load"
        : "Start analyser API: cd backend && python3 server.py  (or localhost:8090)",
      false,
    );
  }
}

$("btnLogout").addEventListener("click", () => {
  clearSession();
  window.location.href = "/login.html";
});

export function startDashboard(user) {
  currentUser = user;
  updateUserBar(currentUser);
  loadSettings();
  initChart();
  if (currentUser.has_access) {
    hideSubscriptionBlock();
    syncAutoRefresh();
    probeAnalyserApi();
  } else {
    showSubscriptionBlock(currentUser);
  }
  refreshUserSession();
}
