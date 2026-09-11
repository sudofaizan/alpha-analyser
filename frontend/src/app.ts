// @ts-nocheck
/** Alpha Analyser v2 — app logic ported from Beta Stable v1.5 (unchanged behaviour). */
    const LightweightCharts = window.LightweightCharts;
    if (!LightweightCharts?.createChart) {
      throw new Error("chart-bridge failed — LightweightCharts missing");
    }
    const $ = (id) => document.getElementById(id);
    const API_KEY_STORAGE = "alphafx_api_key";
    const SETTINGS_KEY = "alpha_analyser_settings";
    const CHART_VIEW_KEY = "alpha_analyser_chart_view";
    const DEFAULT_VISIBLE_BARS = 72;
    const AUTO_REFRESH_MS = 5000;
    const PA_OVERLAY_BARS = 280;
    const MAX_CHART_MARKERS = 48;

    let chart = null;
    let candleSeries = null;
    let candleMarkers = null;
    let paLineSeries = [];
    let paPriceLines = [];
    let smcSeries = [];
    let bbSmcSeries = [];
    let sessionSeries = [];
    let sessionLabelEls = [];
    let sessionLabelData = [];
    let sessionRepaintHooked = false;
    let chartViewSaveTimer = null;

    function chartContextKey() {
      return [
        $("dataSource").value,
        $("symbol").value.trim().toUpperCase(),
        $("timeframe").value,
        $("barCount").value,
      ].join("|");
    }

    function loadSavedChartView() {
      try {
        const all = JSON.parse(localStorage.getItem(CHART_VIEW_KEY) || "{}");
        return all[chartContextKey()] || null;
      } catch (_) {
        return null;
      }
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
      chartViewSaveTimer = setTimeout(saveChartView, 250);
    }

    function clampLogicalRange(from, to, barCount, minWidth = 14) {
      if (!Number.isFinite(from) || !Number.isFinite(to) || barCount < 1) {
        return defaultChartViewport(barCount);
      }
      const width = Math.max(minWidth, to - from);
      let f = from;
      let t = to;
      if (t > barCount + 6) {
        t = barCount + 6;
        f = t - width;
      }
      if (f < 0) {
        f = 0;
        t = Math.min(barCount + 6, f + width);
      }
      if (t - f < minWidth) t = f + minWidth;
      return { from: f, to: t };
    }

    function defaultChartViewport(barCount) {
      const pad = 5;
      const visible = Math.min(DEFAULT_VISIBLE_BARS, Math.max(24, barCount - 1));
      const from = Math.max(0, barCount - visible);
      return { from, to: barCount - 1 + pad };
    }

    function clearSavedChartView() {
      try {
        const all = JSON.parse(localStorage.getItem(CHART_VIEW_KEY) || "{}");
        delete all[chartContextKey()];
        localStorage.setItem(CHART_VIEW_KEY, JSON.stringify(all));
      } catch (_) {}
    }

    function savedTimeRangeValid(fromT, toT, firstT, lastT) {
      if (!Number.isFinite(fromT) || !Number.isFinite(toT)) return false;
      if (toT < firstT || fromT > lastT) return false;
      if (toT - fromT < 60) return false;
      return true;
    }

    function restoreChartViewport(rows) {
      if (!chart || !rows.length) return;
      const n = rows.length;
      const saved = loadSavedChartView();
      const ts = chart.timeScale();
      const firstT = rows[0].time;
      const lastT = rows[n - 1].time;

      if (saved?.fromTime != null && saved?.toTime != null
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
          } catch (_) { clearSavedChartView(); }
        } else {
          clearSavedChartView();
        }
      } else if (saved?.fromTime != null || saved?.toTime != null) {
        clearSavedChartView();
      }

      let range = null;
      if (saved?.logical && Number.isFinite(saved.logical.from) && Number.isFinite(saved.logical.to)) {
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
        try { ts.setVisibleLogicalRange(defaultChartViewport(n)); } catch (e2) {
          try { ts.fitContent(); } catch (_) {}
        }
      }
    }

    function applyChartViewport(rows) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (!chart || !rows.length) return;
          viewportApplying = true;
          try {
            restoreChartViewport(rows);
          } catch (err) {
            console.warn("Chart viewport:", err);
            try { chart.timeScale().setVisibleLogicalRange(defaultChartViewport(rows.length)); } catch (_) {}
          } finally {
            viewportApplying = false;
            positionSessionLabels();
          }
        });
      });
    }

    function overlayRows(rows) {
      return rows.length > PA_OVERLAY_BARS ? rows.slice(-PA_OVERLAY_BARS) : rows;
    }

    function sessionMaxSegments(barCount) {
      if (barCount > 400) return 6;
      if (barCount > 200) return 10;
      return 24;
    }

    function scheduleRenderOverlays() {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (!lastCandles.length || !chart) return;
          try {
            renderPriceAction(lastCandles, lastAnalysis);
          } catch (err) {
            console.warn("Overlay render:", err);
          }
          scheduleSaveChartView();
        });
      });
    }

    function renderCandlesOnly(rows, meta) {
      initChart();
      if (!rows.length) {
        setStatus("No valid candles in response", false);
        return false;
      }
      lastCandles = rows;
      clearPaOverlays();
      try {
        candleSeries.setData(rows);
      } catch (err) {
        console.error("setData failed:", err);
        setStatus("Chart data error: " + err.message, false);
        return false;
      }
      applyChartViewport(rows);
      if (meta != null) $("sumMeta").innerHTML = meta;
      return true;
    }

    const TRADING_SESSIONS = [
      { name: "Tokyo", tz: "Asia/Tokyo", start: "09:00", end: "15:00", fill: "rgba(41,98,255,0.15)", line: "#2962FF" },
      { name: "London", tz: "Europe/London", start: "08:30", end: "16:30", fill: "rgba(255,152,0,0.15)", line: "#FF9800" },
      { name: "New York", tz: "America/New_York", start: "09:30", end: "16:00", fill: "rgba(8,153,129,0.15)", line: "#089981" },
    ];
    let lastCandles = [];
    let lastAnalysis = null;
    let lastHtfRows = [];
    let lastNextMoveProjection = null;
    let autoRefreshTimer = null;
    let loadInProgress = false;
    let viewportApplying = false;

    const defaultServer = "13.42.76.172:8080";

    function brokerGoldSymbol(server) {
      const s = String(server || "").toUpperCase();
      if (s.includes("XMGLOBAL")) return "GOLD.i#";
      if (s.includes("SKYRISS")) return "XAUUSD.c";
      return "XAUUSD";
    }

    function shouldAutoReplaceGoldSymbol(val) {
      const u = (val || "").trim().toUpperCase();
      return !u || u === "XAUUSD" || u.startsWith("XAUUSD.") || u === "GOLD" || u.startsWith("GOLD.");
    }

    function applyBrokerGoldSymbol(server) {
      const sym = brokerGoldSymbol(server);
      const el = $("symbol");
      if (el && sym && shouldAutoReplaceGoldSymbol(el.value)) {
        el.value = sym;
        saveSettings();
      }
      return sym;
    }

    async function detectBrokerSymbol() {
      if ($("dataSource").value !== "api") return null;
      try {
        const res = await fetch(`${baseUrl()}/health`);
        if (!res.ok) return null;
        const h = await res.json();
        if (h.server) return applyBrokerGoldSymbol(h.server);
      } catch (_) {}
      return null;
    }

    function htfForTf(tf) {
      return { M1: "M15", M5: "H1", M15: "H4", M30: "H4", H1: "D1", H4: "W1" }[tf] || null;
    }

    function loadSettings() {
      try {
        const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
        if (s.server) $("apiServer").value = s.server;
        if (s.symbol) $("symbol").value = s.symbol;
        if (s.timeframe) $("timeframe").value = s.timeframe;
        if (s.barCount) $("barCount").value = s.barCount;
        if (s.dataSource) $("dataSource").value = s.dataSource;
        if (s.togSmc != null) $("togSmc").checked = !!s.togSmc;
        if (s.togPaSmc != null) $("togPaSmc").checked = !!s.togPaSmc;
        if (s.togSessions != null) $("togSessions").checked = !!s.togSessions;
        if (s.togAutoRefresh != null) $("togAutoRefresh").checked = !!s.togAutoRefresh;
      } catch (_) {}
      try {
        const dash = JSON.parse(localStorage.getItem("alphafx_dashboard_settings") || "{}");
        if (dash.server && (!$("apiServer").value || $("apiServer").value === defaultServer)) {
          $("apiServer").value = dash.server;
        }
      } catch (_) {}
      if (!$("apiServer").value) $("apiServer").value = defaultServer;
      toggleServerField();
    }

    async function bootstrapFromServer() {
      if ($("dataSource").value !== "api") return;
      await detectBrokerSymbol();
    }

    function saveSettings() {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        server: $("apiServer").value.trim(),
        symbol: $("symbol").value.trim(),
        timeframe: $("timeframe").value,
        barCount: $("barCount").value,
        dataSource: $("dataSource").value,
        togSmc: $("togSmc").checked,
        togPaSmc: $("togPaSmc").checked,
        togSessions: $("togSessions").checked,
        togAutoRefresh: $("togAutoRefresh").checked,
      }));
    }

    function syncAutoRefresh() {
      if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
      }
      if (!$("togAutoRefresh").checked) return;
      autoRefreshTimer = setInterval(() => {
        if (!loadInProgress) loadChart(true);
      }, AUTO_REFRESH_MS);
    }

    function toggleServerField() {
      $("serverField").style.display = $("dataSource").value === "api" ? "flex" : "none";
    }

    function apiKey() {
      return sessionStorage.getItem(API_KEY_STORAGE) || "alphafx";
    }

    function baseUrl() {
      let s = $("apiServer").value.trim().replace(/\/$/, "");
      if (!/^https?:\/\//i.test(s)) s = "http://" + s;
      return s;
    }

    function setStatus(msg, ok = true) {
      $("statusLeft").innerHTML = ok ? `<span class="ok">${msg}</span>` : `<span class="err">${msg}</span>`;
    }

    function parseTime(t) {
      if (t == null || t === "") return NaN;
      if (typeof t === "number" && Number.isFinite(t)) {
        return t > 1e12 ? Math.floor(t / 1000) : Math.floor(t);
      }
      const s = String(t).trim();
      if (!s) return NaN;
      const ms = Date.parse(s.endsWith("Z") ? s : (s.includes("+") ? s : s + "Z"));
      return Number.isFinite(ms) ? Math.floor(ms / 1000) : NaN;
    }

    function isValidCandleRow(r) {
      return r && Number.isFinite(r.time) && Number.isFinite(r.open) && Number.isFinite(r.high)
        && Number.isFinite(r.low) && Number.isFinite(r.close) && r.high >= r.low;
    }

    function candlesToRows(candles) {
      const seen = new Set();
      const rows = [];
      for (const c of candles) {
        const t = parseTime(c.time);
        if (!Number.isFinite(t) || seen.has(t)) continue;
        const row = {
          time: t,
          open: +c.open,
          high: +c.high,
          low: +c.low,
          close: +c.close,
          volume: +(c.volume || c.tick_volume || 0),
        };
        if (!isValidCandleRow(row)) continue;
        seen.add(t);
        rows.push(row);
      }
      rows.sort((a, b) => a.time - b.time);
      return rows;
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
        width,
        height,
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
      const ro = new ResizeObserver(() => {
        const sz = chartHostSize(host);
        chart.applyOptions(sz);
        positionSessionLabels();
      });
      ro.observe(host);
      requestAnimationFrame(() => {
        if (!chart) return;
        chart.applyOptions(chartHostSize(host));
      });
      if (!sessionRepaintHooked) {
        sessionRepaintHooked = true;
        chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
          if (loadInProgress || viewportApplying) return;
          positionSessionLabels();
          scheduleSaveChartView();
        });
      }
    }

    function clearSmcOverlays() {
      for (const s of smcSeries) {
        try { chart.removeSeries(s); } catch (_) {}
      }
      smcSeries = [];
    }

    function clearBbSmcOverlays() {
      for (const s of bbSmcSeries) {
        try { chart.removeSeries(s); } catch (_) {}
      }
      bbSmcSeries = [];
    }

    const BB_PAL = {
      bullLine: "#089981",
      bearLine: "#f23645",
      bullFill: "rgba(8,153,129,0.22)",
      bearFill: "rgba(242,54,69,0.22)",
      mitLine: "#64748b",
      mitFill: "rgba(100,116,139,0.35)",
    };

    function bbSmcDrawBox(t0, t1, top, bottom, bullish, mitigated) {
      smcDrawFvgBox(t0, t1, top, bottom, bullish, mitigated, BB_PAL, bbSmcSeries);
    }

    function bbSmcHLine(t0, t1, price, color, width, style) {
      smcHLine(t0, t1, price, color, width, style, bbSmcSeries);
    }

    function bbSmcChartMarkers(beluga) {
      if (!beluga) return [];
      const out = [];
      for (const st of (beluga.structures || []).slice(-12)) {
        const t = smcSafeTime(st.time_end);
        if (!t) continue;
        const bull = st.kind === "bullish" || st.label === "BOS" && st.kind !== "bearish";
        const isChoch = st.label === "CHoCH";
        out.push({
          time: t,
          position: bull ? "belowBar" : "aboveBar",
          color: isChoch ? "#fbbf24" : (bull ? BB_PAL.bullLine : BB_PAL.bearLine),
          shape: st.sweep ? "circle" : "arrowUp",
          text: st.sweep ? "x" : (st.label || "MS"),
        });
      }
      const obs = (beluga.order_blocks || []).filter((o) => !o.mitigated).slice(0, 1);
      for (const ob of obs) {
        const t = smcSafeTime(ob.time_start);
        if (!t || ob.avg == null) continue;
        out.push({
          time: t,
          position: ob.type === "bullish" ? "belowBar" : "aboveBar",
          color: ob.type === "bullish" ? BB_PAL.bullLine : BB_PAL.bearLine,
          shape: "square",
          text: ob.volume_pct != null ? `${ob.volume_pct}%` : "OB",
        });
      }
      return out;
    }

    function renderBbSmcOverlays(beluga) {
      clearBbSmcOverlays();
      if (!$("togPaSmc").checked || !beluga || !chart) return;
      try {
        const fvgs = (beluga.fvgs || []).filter((f) => !f.mitigated).slice(0, 5);
        for (const f of fvgs) {
          const t0 = smcSafeTime(f.time_start);
          const t1 = smcSafeTime(f.time_end);
          if (!t0 || !t1 || f.top <= f.bottom) continue;
          bbSmcDrawBox(t0, t1, f.top, f.bottom, f.type === "bullish", false);
          if (f.avg != null || f.top != null) {
            const mid = (f.top + f.bottom) / 2;
            bbSmcHLine(t0, t1, mid, f.type === "bullish" ? "#089981aa" : "#f23645aa", 1, 2);
          }
        }
        for (const st of (beluga.structures || []).slice(-20)) {
          const t0 = smcSafeTime(st.time_start);
          const t1 = smcSafeTime(st.time_end);
          if (!t0 || !t1) continue;
          const bull = st.kind === "bullish";
          const col = st.label === "CHoCH" ? "#fbbf24" : (bull ? BB_PAL.bullLine : BB_PAL.bearLine);
          bbSmcHLine(t0, t1, st.level, col, st.label === "CHoCH" ? 2 : 1, st.sweep ? 1 : 0);
        }
        const obs = (beluga.order_blocks || []).filter((o) => !o.mitigated).slice(0, 5);
        for (const ob of obs) {
          const t0 = smcSafeTime(ob.time_start);
          const t1 = smcSafeTime(ob.time_end);
          if (!t0 || !t1 || ob.top <= ob.bottom) continue;
          bbSmcDrawBox(t0, t1, ob.top, ob.bottom, ob.type === "bullish", false);
          if (ob.avg != null) {
            bbSmcHLine(t0, t1, ob.avg, ob.type === "bullish" ? "#08998188" : "#f2364588", 1, 2);
          }
        }
      } catch (err) {
        console.warn("BigBeluga SMC overlay:", err);
      }
    }

    function localInducementsFromRows(rows, swings, max = 5) {
      const out = [];
      const seen = new Set();
      const start = Math.max(0, rows.length - 120);
      const recentLows = swings.lows.slice(-5);
      const recentHighs = swings.highs.slice(-5);
      let lastBar = -20;
      for (let i = start; i < rows.length; i++) {
        if (i - lastBar < 10) continue;
        const c = rows[i];
        const rng = c.high - c.low;
        const minWick = rng * 0.35;
        for (const sl of recentLows) {
          if (sl.index >= i - 5) continue;
          const wick = sl.price - c.low;
          if (c.low < sl.price && c.close > sl.price && wick >= minWick) {
            const key = `${c.time}-bull`;
            if (!seen.has(key)) {
              seen.add(key);
              out.push({ kind: "bullish", time: c.time, level: sl.price, sweep_price: c.low, label: "INDUCEMENT" });
              lastBar = i;
            }
            break;
          }
        }
        for (const sh of recentHighs) {
          if (sh.index >= i - 5) continue;
          const wick = c.high - sh.price;
          if (c.high > sh.price && c.close < sh.price && wick >= minWick) {
            const key = `${c.time}-bear`;
            if (!seen.has(key)) {
              seen.add(key);
              out.push({ kind: "bearish", time: c.time, level: sh.price, sweep_price: c.high, label: "INDUCEMENT" });
              lastBar = i;
            }
            break;
          }
        }
      }
      return out.slice(-max);
    }

    function smcSafeTime(iso) {
      if (!iso) return null;
      const t = parseTime(iso);
      return Number.isFinite(t) && t > 0 ? t : null;
    }

    function smcDrawFvgBox(t0, t1, top, bottom, bullish, mitigated, palette, target) {
      if (!chart || t0 == null || t1 == null || top <= bottom) return;
      const tA = Math.min(t0, t1);
      const tB = Math.max(t0, t1);
      if (tA === tB) return;
      const LC = LightweightCharts;
      const pal = palette || {};
      const bullLine = pal.bullLine || "#16a34a";
      const bearLine = pal.bearLine || "#dc2626";
      const bullFill = pal.bullFill || "rgba(34,197,94,0.42)";
      const bearFill = pal.bearFill || "rgba(239,68,68,0.42)";
      const mitLine = pal.mitLine || "#94a3b8";
      const mitFill = pal.mitFill || "rgba(148,163,184,0.50)";
      const lineColor = mitigated ? mitLine : (bullish ? bullLine : bearLine);
      const fill = mitigated ? mitFill : (bullish ? bullFill : bearFill);
      const s = chart.addSeries(LC.BaselineSeries, {
        baseValue: { type: "price", price: bottom },
        topLineColor: lineColor,
        bottomLineColor: lineColor,
        topFillColor1: fill,
        topFillColor2: fill,
        bottomFillColor1: "transparent",
        bottomFillColor2: "transparent",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData([{ time: tA, value: top }, { time: tB, value: top }]);
      (target || smcSeries).push(s);
    }

    function smcDrawObBox(t0, t1, top, bottom, bullish, mitigated) {
      const pal = bullish
        ? { bullLine: "#089981", bullFill: "rgba(8,153,129,0.22)", mitLine: "#64748b", mitFill: "rgba(100,116,139,0.18)" }
        : { bearLine: "#f23645", bearFill: "rgba(242,54,69,0.22)", mitLine: "#64748b", mitFill: "rgba(100,116,139,0.18)" };
      smcDrawFvgBox(t0, t1, top, bottom, bullish, mitigated, pal);
      if (!mitigated) smcHLine(t0, t1, (top + bottom) / 2, bullish ? "#08998188" : "#f2364588", 1, 2);
    }

    function smcHLine(t0, t1, price, color, width, style, target) {
      if (!chart || t0 == null || t1 == null || !Number.isFinite(price)) return;
      const tA = Math.min(t0, t1);
      const tB = Math.max(t0, t1);
      if (tA === tB) return;
      const LC = LightweightCharts;
      const s = chart.addSeries(LC.LineSeries, {
        color,
        lineWidth: width || 2,
        lineStyle: style ?? 0,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData([{ time: tA, value: price }, { time: tB, value: price }]);
      (target || smcSeries).push(s);
    }

    function smcChartMarkers(smc) {
      if (!smc) return [];
      const out = [];
      const fvgs = (smc.fvgs || []).filter((f) => !f.mitigated).slice(-3);
      for (const f of fvgs) {
        const t0 = smcSafeTime(f.time_start);
        const t1 = smcSafeTime(f.time_end);
        if (!t0 || !t1) continue;
        out.push({
          time: Math.floor((t0 + t1) / 2),
          position: "inBar",
          color: f.type === "bullish" ? "#22c55e" : "#ef4444",
          shape: "square",
          text: "FVG",
        });
      }
      for (const st of (smc.structures || []).slice(-4)) {
        const t = smcSafeTime(st.time_end);
        if (!t) continue;
        const isChoch = st.label === "CHoCH";
        out.push({
          time: t,
          position: st.kind === "bullish" ? "belowBar" : "aboveBar",
          color: isChoch ? "#fbbf24" : "#e2e8f0",
          shape: st.kind === "bullish" ? "arrowUp" : "arrowDown",
          text: st.label,
        });
      }
      for (const ind of smc.inducements || []) {
        const t = typeof ind.time === "number" ? ind.time : smcSafeTime(ind.time);
        if (!t) continue;
        const bull = ind.kind === "bullish";
        out.push({
          time: t,
          position: "inBar",
          color: bull ? "#22d3ee" : "#e879f9",
          shape: "circle",
          text: "⚡",
        });
      }
      return out;
    }

    function renderSmcOverlays(smc) {
      clearSmcOverlays();
      if (!$("togSmc").checked || !smc || !chart) return;
      try {
        const fvgs = (smc.fvgs || []).filter((f) => !f.mitigated).slice(-3);
        for (const f of fvgs) {
          const t0 = smcSafeTime(f.time_start);
          const t1 = smcSafeTime(f.time_end);
          if (!t0 || !t1 || f.top <= f.bottom) continue;
          smcDrawFvgBox(t0, t1, f.top, f.bottom, f.type === "bullish", false);
        }
        for (const st of (smc.structures || []).slice(-4)) {
          const t0 = smcSafeTime(st.time_start);
          const t1 = smcSafeTime(st.time_end);
          if (!t0 || !t1) continue;
          smcHLine(t0, t1, st.level, st.label === "CHoCH" ? "#fbbf24" : "#cbd5e1", st.label === "CHoCH" ? 2 : 1, 0);
        }
        const cur = smc.current;
        if (cur) {
          const th0 = smcSafeTime(cur.high_start);
          const th1 = smcSafeTime(cur.high_end);
          const tl0 = smcSafeTime(cur.low_start);
          const tl1 = smcSafeTime(cur.low_end);
          if (th0 && th1) smcHLine(th0, th1, cur.structure_high, "#3b82f6", 2, 2);
          if (tl0 && tl1) smcHLine(tl0, tl1, cur.structure_low, "#3b82f6", 2, 2);
        }
      } catch (err) {
        console.warn("SMC overlay:", err);
      }
    }

    function parseSessionHm(hm) {
      const [h, m] = hm.split(":").map(Number);
      return h * 60 + m;
    }

    function minutesInTimezone(unixSec, tz) {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: tz, hour: "numeric", minute: "numeric", hour12: false,
      }).formatToParts(new Date(unixSec * 1000));
      const h = parseInt(parts.find((p) => p.type === "hour").value, 10);
      const mi = parseInt(parts.find((p) => p.type === "minute").value, 10);
      return h * 60 + mi;
    }

    function dayKeyInTimezone(unixSec, tz) {
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
      }).format(new Date(unixSec * 1000));
    }

    function barInTradingSession(barTime, session) {
      const startMin = parseSessionHm(session.start);
      const endMin = parseSessionHm(session.end);
      const mins = minutesInTimezone(barTime, session.tz);
      if (startMin <= endMin) return mins >= startMin && mins < endMin;
      return mins >= startMin || mins < endMin;
    }

    function buildTradingSessionSegments(rows, session, maxSeg = 24) {
      const segments = [];
      let cur = null;
      for (const bar of rows) {
        const inSess = barInTradingSession(bar.time, session);
        const dk = dayKeyInTimezone(bar.time, session.tz);
        if (inSess) {
          if (!cur || cur.dayKey !== dk) {
            if (cur) segments.push(cur);
            cur = {
              dayKey: dk, name: session.name, t0: bar.time, t1: bar.time,
              high: bar.high, low: bar.low, open: bar.open, close: bar.close,
              sumClose: bar.close, bars: 1, fill: session.fill, line: session.line,
            };
          } else {
            cur.t1 = bar.time;
            cur.high = Math.max(cur.high, bar.high);
            cur.low = Math.min(cur.low, bar.low);
            cur.close = bar.close;
            cur.sumClose += bar.close;
            cur.bars += 1;
          }
        } else if (cur) {
          segments.push(cur);
          cur = null;
        }
      }
      if (cur) segments.push(cur);
      return segments.slice(-maxSeg);
    }

    function clearSessionOverlays() {
      for (const s of sessionSeries) {
        try { chart.removeSeries(s); } catch (_) {}
      }
      sessionSeries = [];
      const layer = $("sessionLabelLayer");
      if (layer) layer.innerHTML = "";
      sessionLabelEls = [];
      sessionLabelData = [];
    }

    function ensureSessionRepaint() {
      requestAnimationFrame(positionSessionLabels);
    }

    function positionSessionLabels() {
      const layer = $("sessionLabelLayer");
      if (!layer || !chart || !candleSeries || !$("togSessions").checked) return;
      try {
        const ts = chart.timeScale();
        for (let i = 0; i < sessionLabelEls.length; i++) {
          const el = sessionLabelEls[i];
          const seg = sessionLabelData[i];
          if (!el || !seg || !Number.isFinite(seg.t0) || !Number.isFinite(seg.low)) continue;
          const x = ts.timeToCoordinate(seg.t0);
          const y = candleSeries.priceToCoordinate(seg.low);
          if (x == null || y == null) { el.style.display = "none"; continue; }
          el.style.display = "block";
          el.style.left = `${x + 4}px`;
          el.style.top = `${y + 4}px`;
        }
      } catch (_) {}
    }

    function sessionDrawHLine(t0, t1, price, color, width, style) {
      if (!chart || t0 == null || t1 == null || !Number.isFinite(price)) return;
      const tA = Math.min(t0, t1);
      const tB = Math.max(t0, t1);
      if (tA === tB) return;
      const LC = LightweightCharts;
      const s = chart.addSeries(LC.LineSeries, {
        color, lineWidth: width || 1, lineStyle: style ?? 0,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      s.setData([{ time: tA, value: price }, { time: tB, value: price }]);
      sessionSeries.push(s);
    }

    function sessionDrawBox(t0, t1, high, low, fill, border) {
      if (!chart || t0 == null || t1 == null || high <= low) return;
      const tA = Math.min(t0, t1);
      const tB = Math.max(t0, t1);
      if (tA === tB) return;
      const LC = LightweightCharts;
      const s = chart.addSeries(LC.BaselineSeries, {
        baseValue: { type: "price", price: low },
        topLineColor: border, bottomLineColor: border,
        topFillColor1: fill, topFillColor2: fill,
        bottomFillColor1: "transparent", bottomFillColor2: "transparent",
        lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      s.setData([{ time: tA, value: high }, { time: tB, value: high }]);
      sessionSeries.push(s);
    }

    function formatSessionRange(high, low) {
      const r = high - low;
      return r >= 1 ? r.toFixed(2) : r.toFixed(5);
    }

    function renderSessionOverlays(rows) {
      clearSessionOverlays();
      if (!$("togSessions").checked || !rows?.length || !chart || !candleSeries) return;
      const maxSeg = sessionMaxSegments(rows.length);
      const layer = $("sessionLabelLayer");
      try {
        for (const sess of TRADING_SESSIONS) {
          for (const seg of buildTradingSessionSegments(rows, sess, maxSeg)) {
            sessionDrawBox(seg.t0, seg.t1, seg.high, seg.low, seg.fill, seg.line);
            sessionDrawHLine(seg.t0, seg.t1, seg.open, seg.line, 1, 2);
            sessionDrawHLine(seg.t0, seg.t1, seg.close, seg.line, 1, 2);
            const avg = seg.sumClose / seg.bars;
            sessionDrawHLine(seg.t0, seg.t1, avg, seg.line, 2, 3);
            if (layer) {
              const el = document.createElement("div");
              el.className = "session-label";
              el.style.color = seg.line;
              el.style.borderColor = seg.line;
              el.style.background = seg.fill.replace("0.15", "0.55");
              el.textContent = `Range: ${formatSessionRange(seg.high, seg.low)}\nAvg: ${avg.toFixed(avg >= 1 ? 2 : 5)}\n${seg.name}`;
              layer.appendChild(el);
              sessionLabelEls.push(el);
              sessionLabelData.push(seg);
            }
          }
        }
        ensureSessionRepaint();
        requestAnimationFrame(positionSessionLabels);
      } catch (err) {
        console.warn("Session overlay:", err);
      }
    }

    function sessionChartMarkers(rows) {
      if (!$("togSessions").checked || !rows?.length) return [];
      const maxSeg = sessionMaxSegments(rows.length);
      const out = [];
      for (const sess of TRADING_SESSIONS) {
        for (const seg of buildTradingSessionSegments(rows, sess, maxSeg)) {
          out.push({
            time: seg.t0, position: "aboveBar", color: seg.line, shape: "square",
            text: seg.name.slice(0, 3).toUpperCase(),
          });
        }
      }
      return out;
    }

    function clearPaOverlays() {
      if (!chart) return;
      clearSmcOverlays();
      clearBbSmcOverlays();
      clearSessionOverlays();
      for (const s of paLineSeries) {
        try { chart.removeSeries(s); } catch (_) {}
      }
      paLineSeries = [];
      for (const ln of paPriceLines) {
        try { candleSeries?.removePriceLine(ln); } catch (_) {}
      }
      paPriceLines = [];
      if (candleMarkers) {
        try { candleMarkers.setMarkers([]); } catch (_) {}
      }
    }

    function swingLookback(tf) {
      return { M1: 8, M5: 6, M15: 5, M30: 4, H1: 4, H4: 3, D1: 3 }[tf] || 5;
    }

    function nearestBarIndex(rows, t) {
      let best = 0, bestD = Infinity;
      for (let i = 0; i < rows.length; i++) {
        const d = Math.abs(rows[i].time - t);
        if (d < bestD) { bestD = d; best = i; }
      }
      return best;
    }

    function enrichSwings(rows, swings) {
      for (const s of swings.highs) {
        if (s.index == null) s.index = nearestBarIndex(rows, s.time);
      }
      for (const s of swings.lows) {
        if (s.index == null) s.index = nearestBarIndex(rows, s.time);
      }
      return swings;
    }

    function findSwings(rows, lookback = 3) {
      const highs = [], lows = [];
      for (let i = lookback; i < rows.length - lookback; i++) {
        const h = rows[i].high, l = rows[i].low;
        let isH = true, isL = true;
        for (let j = 1; j <= lookback; j++) {
          if (rows[i - j].high >= h || rows[i + j].high >= h) isH = false;
          if (rows[i - j].low <= l || rows[i + j].low <= l) isL = false;
        }
        if (isH) highs.push({ index: i, time: rows[i].time, price: h });
        if (isL) lows.push({ index: i, time: rows[i].time, price: l });
      }
      return { highs, lows };
    }

    function classifyStructure(highs, lows) {
      if (highs.length < 2 || lows.length < 2) return { trend: "NEUTRAL", label: "—" };
      const h1 = highs[highs.length - 2].price, h2 = highs[highs.length - 1].price;
      const l1 = lows[lows.length - 2].price, l2 = lows[lows.length - 1].price;
      const hh = h2 > h1, hl = l2 > l1, lh = h2 < h1, ll = l2 < l1;
      let label = "";
      if (hh && hl) { label = "HH + HL"; return { trend: "UP", label }; }
      if (lh && ll) { label = "LH + LL"; return { trend: "DOWN", label }; }
      if (hh && ll) { label = "HH + LL (range/expansion)"; return { trend: "NEUTRAL", label }; }
      if (lh && hl) { label = "LH + HL (compression)"; return { trend: "NEUTRAL", label }; }
      return { trend: "NEUTRAL", label: "Mixed" };
    }

    function buildZigzag(highs, lows, maxPoints = 6) {
      const all = [
        ...highs.map((h) => ({ ...h, kind: "H" })),
        ...lows.map((l) => ({ ...l, kind: "L" })),
      ].sort((a, b) => a.time - b.time);
      const zig = [];
      for (const p of all) {
        if (!zig.length) { zig.push(p); continue; }
        const last = zig[zig.length - 1];
        if (last.kind === p.kind) {
          if (p.kind === "H" && p.price > last.price) zig[zig.length - 1] = p;
          else if (p.kind === "L" && p.price < last.price) zig[zig.length - 1] = p;
        } else {
          zig.push(p);
        }
      }
      return zig.slice(-maxPoints);
    }

    function latestStructureLabels(highs, lows) {
      const markers = [];
      if (highs.length >= 2) {
        const prev = highs[highs.length - 2], cur = highs[highs.length - 1];
        markers.push({
          time: cur.time, position: "aboveBar", shape: "circle", color: "#38bdf8",
          text: cur.price > prev.price ? "HH" : "LH",
        });
      }
      if (lows.length >= 2) {
        const prev = lows[lows.length - 2], cur = lows[lows.length - 1];
        markers.push({
          time: cur.time, position: "belowBar", shape: "circle", color: "#38bdf8",
          text: cur.price > prev.price ? "HL" : "LL",
        });
      }
      return markers;
    }

    function detectConfirmations(rows, swings, lookbackBars = 35, maxMarkers = 2) {
      const markers = [];
      const start = Math.max(0, rows.length - lookbackBars);
      const nearPct = 0.0012;
      const keyLevels = [
        ...swings.highs.slice(-2),
        ...swings.lows.slice(-2),
      ];

      for (let i = rows.length - 1; i > start; i--) {
        const c = rows[i], p = rows[i - 1];
        const body = Math.abs(c.close - c.open);
        const upperWick = c.high - Math.max(c.open, c.close);
        const lowerWick = Math.min(c.open, c.close) - c.low;
        const bullEngulf = c.close > c.open && p.close < p.open && c.close >= p.open && c.open <= p.close;
        const bearEngulf = c.close < c.open && p.close > p.open && c.close <= p.open && c.open >= p.close;
        const bullPin = lowerWick > body * 2.5 && lowerWick > upperWick * 2 && c.close > c.open;
        const bearPin = upperWick > body * 2.5 && upperWick > lowerWick * 2 && c.close < c.open;

        const nearLevel = keyLevels.some((s) => Math.abs(s.price - c.close) / s.price < nearPct);
        if (!nearLevel) continue;

        if (bullEngulf || bullPin) {
          markers.push({
            time: c.time, position: "belowBar", shape: "arrowUp", color: "#a78bfa",
            text: bullEngulf ? "Engulf" : "Pin",
          });
        } else if (bearEngulf || bearPin) {
          markers.push({
            time: c.time, position: "aboveBar", shape: "arrowDown", color: "#a78bfa",
            text: bearEngulf ? "Engulf" : "Pin",
          });
        }
        if (markers.length >= maxMarkers) break;
      }
      return markers.reverse();
    }

    function nearestOrderBlocks(orderBlocks, price, max = 2) {
      const pool = (orderBlocks || []).filter((ob) => ob.high != null && ob.low != null);
      if (!pool.length) return [];
      const tradeable = pool.filter((ob) => ob.is_tradeable);
      const src = tradeable.length ? tradeable : pool;
      return [...src]
        .sort((a, b) => {
          const midA = (a.high + a.low) / 2, midB = (b.high + b.low) / 2;
          return Math.abs(midA - price) - Math.abs(midB - price);
        })
        .slice(0, max);
    }

    function dedupeBreaks(breaks, minGap = 12) {
      const out = [];
      for (const b of breaks) {
        const last = out[out.length - 1];
        if (!last || b.index - last.index >= minGap || b.type === "ChoCH") out.push(b);
      }
      return out;
    }

    function findSmcOrderBlocks(rows, breaks) {
      const obs = [];
      const n = rows.length;
      if (n < 20) return obs;
      const avgRange = rows.slice(-60).reduce((s, c) => s + c.high - c.low, 0) / Math.min(60, n);
      for (const br of breaks.slice(-6)) {
        const bosIdx = br.index;
        if (br.direction === "BULLISH") {
          for (let i = bosIdx - 1; i > Math.max(0, bosIdx - 14); i--) {
            if (rows[i].close < rows[i].open) {
              const move = rows[bosIdx].high - rows[i].low;
              if (move > avgRange * 1.4) {
                obs.push({ type: "BULLISH_OB", time: rows[i].time, high: rows[i].high, low: rows[i].low });
                break;
              }
            }
          }
        } else {
          for (let i = bosIdx - 1; i > Math.max(0, bosIdx - 14); i--) {
            if (rows[i].close > rows[i].open) {
              const move = rows[i].high - rows[bosIdx].low;
              if (move > avgRange * 1.4) {
                obs.push({ type: "BEARISH_OB", time: rows[i].time, high: rows[i].high, low: rows[i].low });
                break;
              }
            }
          }
        }
      }
      const uniq = [];
      for (const ob of obs) {
        if (!uniq.some((u) => Math.abs((u.high + u.low) / 2 - (ob.high + ob.low) / 2) < avgRange * 0.5)) uniq.push(ob);
      }
      return uniq;
    }

    function drawFilledZone(rows, t0, t1, low, high, fillColor, borderColor) {
      const LC = LightweightCharts;
      if (high <= low || t1 <= t0) return;
      const area = chart.addSeries(LC.AreaSeries, {
        topColor: fillColor,
        bottomColor: fillColor,
        lineColor: borderColor,
        lineWidth: 1,
        baseValue: { type: "price", price: low },
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      area.setData([{ time: t0, value: high }, { time: t1, value: high }]);
      paLineSeries.push(area);
    }

    function drawSupplyDemandZones(rows, swings) {
      const tEnd = rows[rows.length - 1].time;
      const atr = calcAtr(rows, 14);
      const pad = atr * 0.4;
      const lastH = swings.highs[swings.highs.length - 1];
      const lastL = swings.lows[swings.lows.length - 1];
      if (lastH) {
        drawFilledZone(rows, lastH.time, tEnd, lastH.price - pad, lastH.price + pad * 0.6,
          "rgba(239,68,68,0.28)", "rgba(239,68,68,0.65)");
      }
      if (lastL) {
        drawFilledZone(rows, lastL.time, tEnd, lastL.price - pad * 0.6, lastL.price + pad,
          "rgba(148,163,184,0.25)", "rgba(148,163,184,0.55)");
      }
    }

    function drawHtfWaveLevels(rows, htfRows, chartTf) {
      const htf = htfForTf(chartTf);
      if (!htf || !htfRows.length) return [];
      const lb = swingLookback(htf);
      const htfSwings = enrichSwings(htfRows, findSwings(htfRows, lb));
      const tEnd = rows[rows.length - 1].time;
      const atr = calcAtr(htfRows, 14);
      const markers = [];
      const lastL = htfSwings.lows[htfSwings.lows.length - 1];
      const lastH = htfSwings.highs[htfSwings.highs.length - 1];
      if (lastL) {
        const t0 = Math.max(lastL.time, rows[0].time);
        drawFilledZone(rows, t0, tEnd, lastL.price - atr * 0.15, lastL.price + atr * 0.2,
          "rgba(56,189,248,0.12)", "rgba(56,189,248,0.5)");
        paPriceLines.push(candleSeries.createPriceLine({
          price: lastL.price, color: "rgba(56,189,248,0.75)", lineWidth: 2, lineStyle: 2,
          axisLabelVisible: true, title: `${htf} wave low`,
        }));
        markers.push({
          time: lastL.time, position: "belowBar", shape: "circle", color: "#38bdf8",
          text: `${htf} HL`,
        });
      }
      if (lastH) {
        paPriceLines.push(candleSeries.createPriceLine({
          price: lastH.price, color: "rgba(239,68,68,0.45)", lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: `${htf} wave high`,
        }));
      }
      return markers;
    }

    function findStructureBreaks(rows, swings) {
      const breaks = [];
      const n = rows.length;
      for (const sh of swings.highs) {
        const idx = sh.index ?? 0;
        for (let i = idx + 1; i < Math.min(idx + 50, n); i++) {
          if (rows[i].close > sh.price) {
            breaks.push({ index: i, time: rows[i].time, level: sh.price, direction: "BULLISH", swingTime: sh.time });
            break;
          }
        }
      }
      for (const sl of swings.lows) {
        const idx = sl.index ?? 0;
        for (let i = idx + 1; i < Math.min(idx + 50, n); i++) {
          if (rows[i].close < sl.price) {
            breaks.push({ index: i, time: rows[i].time, level: sl.price, direction: "BEARISH", swingTime: sl.time });
            break;
          }
        }
      }
      breaks.sort((a, b) => a.index - b.index);
      let prevDir = null;
      for (const b of breaks) {
        b.type = prevDir && b.direction !== prevDir ? "ChoCH" : "BOS";
        prevDir = b.direction;
      }
      return dedupeBreaks(breaks);
    }

    function pullbackLabel(swings) {
      const markers = [];
      const lastL = swings.lows[swings.lows.length - 1];
      const lastH = swings.highs[swings.highs.length - 1];
      if (lastL && swings.lows.length >= 2) {
        const prev = swings.lows[swings.lows.length - 2];
        markers.push({
          time: lastL.time, position: "belowBar", shape: "circle", color: "#38bdf8",
          text: lastL.price > prev.price ? "HL" : "LL",
        });
      }
      if (lastH && swings.highs.length >= 2) {
        const prev = swings.highs[swings.highs.length - 2];
        if (lastH.time > (lastL?.time || 0)) {
          markers.push({
            time: lastH.time, position: "aboveBar", shape: "circle", color: "#94a3b8",
            text: lastH.price > prev.price ? "HH" : "LH",
          });
        }
      }
      return markers;
    }

    function detectLiquiditySweeps(rows, swings, max = 5) {
      const markers = [];
      const start = Math.max(0, rows.length - 150);
      for (let i = start; i < rows.length; i++) {
        const c = rows[i];
        for (const sl of swings.lows.slice(-8)) {
          if (c.low < sl.price && c.close > sl.price) markers.push({ time: c.time, position: "belowBar", shape: "circle", color: "#64748b", text: "×" });
        }
        for (const sh of swings.highs.slice(-8)) {
          if (c.high > sh.price && c.close < sh.price) markers.push({ time: c.time, position: "aboveBar", shape: "circle", color: "#64748b", text: "×" });
        }
      }
      const seen = new Set(), uniq = [];
      for (const m of markers) { if (!seen.has(m.time)) { seen.add(m.time); uniq.push(m); } }
      return uniq.slice(-max);
    }

    function bosMarkers(breaks, max = 5) {
      return breaks.slice(-max).map((b) => ({
        time: b.time,
        position: b.direction === "BULLISH" ? "aboveBar" : "belowBar",
        shape: "square",
        color: b.type === "ChoCH" ? "#fbbf24" : "#e2e8f0",
        text: b.type,
      }));
    }

    function drawBosLevels(rows, breaks, max = 5) {
      const LC = LightweightCharts;
      for (const b of breaks.slice(-max)) {
        if (!Number.isFinite(b.level) || !Number.isFinite(b.time)) continue;
        const t0 = Math.max(b.swingTime || 0, rows[Math.max(0, rows.length - 120)].time);
        if (!Number.isFinite(t0) || t0 >= b.time) continue;
        const s = chart.addSeries(LC.LineSeries, {
          color: "rgba(100,116,139,0.55)", lineWidth: 1, lineStyle: 2,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        s.setData([{ time: t0, value: b.level }, { time: b.time, value: b.level }]);
        paLineSeries.push(s);
      }
    }

    function drawObZone(rows, ob) {
      const bull = ob.type === "BULLISH_OB";
      const t0 = ob.time ? (typeof ob.time === "number" ? ob.time : parseTime(ob.time)) : rows[Math.max(0, rows.length - 80)].time;
      const t1 = rows[rows.length - 1].time;
      const fill = bull ? "rgba(148,163,184,0.32)" : "rgba(239,68,68,0.3)";
      const border = bull ? "rgba(148,163,184,0.7)" : "rgba(239,68,68,0.75)";
      drawFilledZone(rows, t0, t1, ob.low, ob.high, fill, border);
      paPriceLines.push(candleSeries.createPriceLine({
        price: bull ? ob.low : ob.high,
        color: border, lineWidth: 1, lineStyle: 0,
        axisLabelVisible: true, title: "OB",
      }));
    }

    function drawSmcWave(rows, swings, breaks) {
      const LC = LightweightCharts;
      const startT = rows[Math.max(0, rows.length - Math.floor(rows.length * 0.55))].time;
      const zig = buildZigzag(swings.highs, swings.lows, 14).filter((p) => p.time >= startT);
      if (zig.length >= 2) {
        const pts = zig
          .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.price))
          .map((p) => ({ time: p.time, value: p.price }));
        if (pts.length < 2) return [...pullbackLabel(swings), ...bosMarkers(breaks, 5), ...detectLiquiditySweeps(rows, swings, 5)];
        const s = chart.addSeries(LC.LineSeries, {
          color: "rgba(248,250,252,0.92)", lineWidth: 2,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        s.setData(pts);
        paLineSeries.push(s);
      }
      drawBosLevels(rows, breaks, 5);
      return [...pullbackLabel(swings), ...bosMarkers(breaks, 5), ...detectLiquiditySweeps(rows, swings, 5)];
    }

    function drawRule1Structure(rows, swings, breaks) {
      return drawSmcWave(rows, swings, breaks);
    }

    function drawRule2Levels(rows, swings, orderBlocks, smcObs) {
      const price = rows[rows.length - 1].close;
      drawSupplyDemandZones(rows, swings);
      const merged = [...smcObs];
      for (const ob of (orderBlocks || [])) {
        if (!merged.some((m) => Math.abs(m.high - ob.high) < 0.01 && Math.abs(m.low - ob.low) < 0.01)) merged.push(ob);
      }
      for (const ob of nearestOrderBlocks(merged, price, 3)) drawObZone(rows, ob);
    }

    function paEnabled() {
      return $("togPriceAction").checked;
    }

    function barDurationSec(rows) {
      if (rows.length < 2) return 300;
      const d = rows[rows.length - 1].time - rows[rows.length - 2].time;
      return d > 0 ? d : 300;
    }

    function futureTime(t, barsAhead, barSec) {
      return t + barsAhead * barSec;
    }

    function calcAtr(rows, period = 14) {
      if (rows.length < period + 1) return 1;
      let sum = 0;
      for (let i = rows.length - period; i < rows.length; i++) {
        const c = rows[i], p = rows[i - 1];
        sum += Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close));
      }
      return sum / period;
    }

    function fmtPrice(p) {
      if (p == null || Number.isNaN(p)) return "—";
      const n = Math.abs(p);
      const d = n >= 100 ? 2 : n >= 10 ? 3 : 5;
      return (+p).toFixed(d);
    }

    function htfLabel(analysis) {
      const h = analysis?.overall_trend || "NEUTRAL";
      if (h.includes("STRONG_UP")) return { text: "STRONG UP", bias: "UP" };
      if (h.includes("STRONG_DOWN")) return { text: "STRONG DOWN", bias: "DOWN" };
      if (h === "UP") return { text: "UP", bias: "UP" };
      if (h === "DOWN") return { text: "DOWN", bias: "DOWN" };
      return { text: "NEUTRAL", bias: "NEUTRAL" };
    }

    function pathDirectionLabel(path, price) {
      if (!path || path.length < 2) return "—";
      if (path.length >= 3) {
        const leg1 = path[1].price < price ? "DOWN" : path[1].price > price ? "UP" : "FLAT";
        const leg2 = path[2].price < path[1].price ? "DOWN" : path[2].price > path[1].price ? "UP" : "FLAT";
        if (leg1 !== leg2 && leg1 !== "FLAT" && leg2 !== "FLAT") return `${leg1} → ${leg2}`;
      }
      const end = path[path.length - 1].price;
      return end > price ? "UP" : end < price ? "DOWN" : "FLAT";
    }

    function buildNextMoveReasoning(proj, ctx) {
      const lines = [];
      const pct = Math.round(ctx.posInRange * 100);
      lines.push(`Structure is <b>${ctx.structure.trend}</b> (${ctx.structure.label || "swing read"}).`);
      lines.push(`Price is at <b>${pct}%</b> of the last swing leg (${fmtPrice(ctx.lastL.price)} low → ${fmtPrice(ctx.lastH.price)} high).`);
      lines.push(`Higher-timeframe bias: <b>${ctx.htf.text}</b>.`);
      lines.push(`Rule matched: <b>${proj.branchLabel}</b>.`);
      lines.push(proj.branchWhy);
      if (proj.path.length >= 3) {
        const leg1Dir = proj.path[1].price < ctx.price ? "down" : "up";
        const leg2Dir = proj.path[2].price < proj.path[1].price ? "down" : "up";
        lines.push(`<b>Leg 1 (${leg1Dir})</b>: pullback/retest toward T1 <b>${fmtPrice(proj.t1)}</b> — classic Wave B or resistance/support retest before continuation.`);
        lines.push(`<b>Leg 2 (${leg2Dir})</b>: measured move toward T2 <b>${fmtPrice(proj.t2)}</b> — 1:1 extension of the prior swing leg (Wave C target).`);
        lines.push(`Net arrow shows <b>${proj.pathLabel}</b> because the model expects a two-step path, not a straight line.`);
      } else {
        lines.push(`Single-leg target T1 <b>${fmtPrice(proj.t1)}</b> from current price.`);
      }
      lines.push(`Invalidation if price breaks <b>${fmtPrice(proj.invalid)}</b> (setup wrong).`);
      if (proj.confidenceNotes?.length) {
        for (const n of proj.confidenceNotes) lines.push(n);
      }
      return lines;
    }

    function finalizeNextMove(base, ctx) {
      const pathLabel = pathDirectionLabel(base.path, ctx.price);
      const endPrice = base.path[base.path.length - 1].price;
      const netDir = endPrice < ctx.price ? "DOWN" : endPrice > ctx.price ? "UP" : base.dir;
      const out = { ...base, dir: netDir, pathLabel, reasoning: buildNextMoveReasoning({ ...base, pathLabel }, ctx) };
      return out;
    }

    function computeNextMove(rows, swings, structure, analysis, orderBlocks) {
      const price = rows[rows.length - 1].close;
      const lastBar = rows[rows.length - 1];
      const barSec = barDurationSec(rows);
      const lastH = swings.highs[swings.highs.length - 1];
      const lastL = swings.lows[swings.lows.length - 1];
      if (!lastH || !lastL) return null;

      const range = lastH.price - lastL.price;
      if (range <= 0) return null;

      const posInRange = (price - lastL.price) / range;
      const leg = range;
      const atr = analysis?.chart?.atr || calcAtr(rows, 14);
      const htf = htfLabel(analysis);
      const fib50 = lastL.price + leg * 0.5;
      const fib618 = lastL.price + leg * 0.618;
      const extDown = lastL.price - leg;
      const extUp = lastH.price + leg;
      const fib50Up = lastH.price - leg * 0.5;
      const fib618Up = lastH.price - leg * 0.618;

      const ctx = { price, lastH, lastL, posInRange, structure, htf, atr };
      let scenario, dir, confidence, path, t1, t2, invalid, waveLabel, fibLevels;
      let branchLabel, branchWhy;
      const confidenceNotes = [];

      const boost = (base) => {
        let c = base;
        if (htf.bias === structure.trend && structure.trend !== "NEUTRAL") {
          c += 12;
          confidenceNotes.push("HTF aligns with structure (+12% confidence).");
        }
        if (htf.bias !== "NEUTRAL" && htf.bias !== structure.trend && structure.trend !== "NEUTRAL") {
          c -= 10;
          confidenceNotes.push("HTF conflicts with structure (−10% confidence).");
        }
        const nearOb = nearestOrderBlocks(orderBlocks, price, 1)[0];
        if (nearOb) {
          const mid = (nearOb.high + nearOb.low) / 2;
          if (Math.abs(mid - price) / price < 0.002) {
            c += 8;
            confidenceNotes.push("Price sits near an order block (+8% confidence).");
          }
        }
        return Math.max(35, Math.min(88, c));
      };

      if (structure.trend === "DOWN") {
        if (posInRange < 0.4) {
          branchLabel = "Downtrend · low in range";
          branchWhy = "In a downtrend, price in the lower 40% of the swing often bounces (Wave B) before continuing lower.";
          scenario = "Bounce to resistance (Wave B)";
          dir = "UP";
          t1 = lastH.price;
          t2 = extDown;
          invalid = lastL.price - atr * 0.5;
          waveLabel = "Wave B up → Wave C down (1:1)";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 10, barSec), price: t1 },
            { time: futureTime(lastBar.time, 22, barSec), price: t2 },
          ];
          fibLevels = [
            { price: fib50, label: "50%" },
            { price: fib618, label: "61.8%" },
            { price: extDown, label: "1:1" },
          ];
          confidence = boost(52);
        } else if (posInRange > 0.6) {
          branchLabel = "Downtrend · high in range";
          branchWhy = "In a downtrend, price in the upper 40% is often rejecting resistance for continuation down.";
          scenario = "Reject resistance · continuation down";
          dir = "DOWN";
          t1 = lastL.price;
          t2 = extDown;
          invalid = lastH.price + atr * 0.35;
          waveLabel = "Wave C extension to 1:1 target";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 8, barSec), price: t1 },
            { time: futureTime(lastBar.time, 18, barSec), price: t2 },
          ];
          fibLevels = [{ price: extDown, label: "1:1" }, { price: lastL.price, label: "Sup" }];
          confidence = boost(65);
        } else {
          branchLabel = "Downtrend · mid range";
          branchWhy = "Mid-range in downtrend: expect retest of resistance (T1) then measured move down (T2).";
          scenario = "Pullback to resistance, then sell";
          dir = "DOWN";
          t1 = lastH.price;
          t2 = extDown;
          invalid = lastH.price + atr * 0.4;
          waveLabel = "Retest Res → measured move down";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 7, barSec), price: t1 },
            { time: futureTime(lastBar.time, 17, barSec), price: t2 },
          ];
          fibLevels = [
            { price: fib618, label: "61.8%" },
            { price: extDown, label: "1:1" },
          ];
          confidence = boost(58);
        }
      } else if (structure.trend === "UP") {
        if (posInRange > 0.6) {
          branchLabel = "Uptrend · high in range";
          branchWhy = "In an uptrend, price in the upper 40% often pulls back to support (Wave B) before continuing higher — this is the DOWN then UP path you see.";
          scenario = "Pullback to support (Wave B)";
          dir = "DOWN";
          t1 = lastL.price;
          t2 = extUp;
          invalid = lastH.price + atr * 0.5;
          waveLabel = "Wave B down → Wave C up (1:1)";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 10, barSec), price: t1 },
            { time: futureTime(lastBar.time, 22, barSec), price: t2 },
          ];
          fibLevels = [
            { price: fib50Up, label: "50%" },
            { price: fib618Up, label: "61.8%" },
            { price: extUp, label: "1:1" },
          ];
          confidence = boost(52);
        } else if (posInRange < 0.4) {
          branchLabel = "Uptrend · low in range";
          branchWhy = "In an uptrend, price in the lower 40% is often bouncing from support for continuation up.";
          scenario = "Bounce support · continuation up";
          dir = "UP";
          t1 = lastH.price;
          t2 = extUp;
          invalid = lastL.price - atr * 0.35;
          waveLabel = "Wave C extension to 1:1 target";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 8, barSec), price: t1 },
            { time: futureTime(lastBar.time, 18, barSec), price: t2 },
          ];
          fibLevels = [{ price: extUp, label: "1:1" }, { price: lastH.price, label: "Res" }];
          confidence = boost(65);
        } else {
          branchLabel = "Uptrend · mid range";
          branchWhy = "Mid-range in uptrend: expect retest of support (T1) then measured move up (T2).";
          scenario = "Pullback to support, then buy";
          dir = "UP";
          t1 = lastL.price;
          t2 = extUp;
          invalid = lastL.price - atr * 0.4;
          waveLabel = "Retest Sup → measured move up";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 7, barSec), price: t1 },
            { time: futureTime(lastBar.time, 17, barSec), price: t2 },
          ];
          fibLevels = [
            { price: fib618Up, label: "61.8%" },
            { price: extUp, label: "1:1" },
          ];
          confidence = boost(58);
        }
      } else {
        if (posInRange < 0.45) {
          branchLabel = "Range · lower half";
          branchWhy = "No clear trend: price near range low → bounce toward resistance.";
          scenario = "Range · bounce toward resistance";
          dir = "UP";
          t1 = lastH.price;
          t2 = fib618;
          invalid = lastL.price - atr * 0.4;
          waveLabel = "Range low → mid/fib target";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 12, barSec), price: t1 },
          ];
          fibLevels = [{ price: fib50, label: "50%" }, { price: fib618, label: "61.8%" }];
          confidence = boost(48);
        } else {
          branchLabel = "Range · upper half";
          branchWhy = "No clear trend: price near range high → fade toward support.";
          scenario = "Range · fade toward support";
          dir = "DOWN";
          t1 = lastL.price;
          t2 = fib50;
          invalid = lastH.price + atr * 0.4;
          waveLabel = "Range high → mid/fib target";
          path = [
            { time: lastBar.time, price },
            { time: futureTime(lastBar.time, 12, barSec), price: t1 },
          ];
          fibLevels = [{ price: fib50Up, label: "50%" }, { price: fib618Up, label: "61.8%" }];
          confidence = boost(48);
        }
      }

      return finalizeNextMove({
        scenario, dir, confidence, path, t1, t2, invalid,
        waveLabel, fibLevels, htf: htf.text, posInRange,
        branchLabel, branchWhy, confidenceNotes,
      }, ctx);
    }

    function drawNextMove(projection) {
      const LC = LightweightCharts;
      const end = projection.path[projection.path.length - 1];
      const start = projection.path[0];
      const bullPath = end.price >= start.price;
      const col = bullPath ? "rgba(34,197,94,.8)" : "rgba(239,68,68,.8)";

      const s = chart.addSeries(LC.LineSeries, {
        color: col, lineWidth: 2, lineStyle: 2,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      s.setData(projection.path.map((p) => ({ time: p.time, value: p.price })));
      paLineSeries.push(s);

      const tCol = bullPath ? "rgba(34,197,94,.7)" : "rgba(239,68,68,.7)";
      paPriceLines.push(candleSeries.createPriceLine({
        price: projection.t1, color: tCol, lineWidth: 2, lineStyle: 0,
        axisLabelVisible: true, title: "T1",
      }));
      if (projection.t2 != null && Math.abs(projection.t2 - projection.t1) > 1e-9) {
        paPriceLines.push(candleSeries.createPriceLine({
          price: projection.t2, color: tCol, lineWidth: 1, lineStyle: 2,
          axisLabelVisible: true, title: "T2",
        }));
      }
      paPriceLines.push(candleSeries.createPriceLine({
        price: projection.invalid, color: "rgba(239,68,68,.55)", lineWidth: 1, lineStyle: 0,
        axisLabelVisible: true, title: "Inv",
      }));

      for (const f of (projection.fibLevels || []).slice(0, 3)) {
        paPriceLines.push(candleSeries.createPriceLine({
          price: f.price, color: "rgba(167,139,250,.4)", lineWidth: 1, lineStyle: 3,
          axisLabelVisible: true, title: f.label,
        }));
      }

      return [{
        time: end.time,
        position: end.price >= start.price ? "belowBar" : "aboveBar",
        shape: end.price >= start.price ? "arrowUp" : "arrowDown",
        color: col,
        text: projection.pathLabel || ("→ " + projection.dir),
      }, ...(projection.path.length >= 3 ? [{
        time: projection.path[1].time,
        position: projection.path[1].price < start.price ? "aboveBar" : "belowBar",
        shape: "circle",
        color: "rgba(251,191,36,0.95)",
        text: "T1",
      }] : [])];
    }

    function toggleNextMoveLogic(forceOpen) {
      const logic = $("nmLogic");
      const box = $("nextMoveBox");
      if (!logic || !lastNextMoveProjection) return;
      const open = forceOpen === true ? true : forceOpen === false ? false : !logic.classList.contains("open");
      logic.classList.toggle("open", open);
      box.classList.toggle("logic-open", open);
    }

    function updateNextMovePanel(projection) {
      lastNextMoveProjection = projection;
      const box = $("nextMoveBox");
      if (!projection || !$("togNextMove").checked) {
        box.style.display = "none";
        toggleNextMoveLogic(false);
        return;
      }
      box.style.display = "block";
      $("nmScenario").textContent = projection.scenario;
      $("nmWave").textContent = projection.waveLabel;
      $("nmPath").textContent = projection.pathLabel || projection.dir;
      const dirEl = $("nmDir");
      dirEl.textContent = projection.dir;
      dirEl.className = "val " + (projection.dir === "UP" ? "up" : projection.dir === "DOWN" ? "dn" : "neu");
      $("nmT1").textContent = fmtPrice(projection.t1);
      $("nmT2").textContent = projection.t2 != null ? fmtPrice(projection.t2) : "—";
      $("nmInv").textContent = fmtPrice(projection.invalid);
      const htfEl = $("nmHtf");
      htfEl.textContent = projection.htf;
      htfEl.className = "val " + (projection.htf.includes("UP") ? "up" : projection.htf.includes("DOWN") ? "dn" : "neu");
      $("nmConf").textContent = `Confidence ~${projection.confidence}% · position in range ${Math.round(projection.posInRange * 100)}%`;
      const logicEl = $("nmLogic");
      if (projection.reasoning?.length) {
        logicEl.innerHTML = projection.reasoning.map((l) => `<div style="margin-bottom:.25rem">${l}</div>`).join("");
      } else {
        logicEl.innerHTML = "";
      }
    }

    function renderPriceAction(rows, analysis) {
      clearPaOverlays();
      if (!rows.length) {
        updatePaSummary(null, null, 0);
        updateNextMovePanel(null);
        return;
      }

      const paRows = overlayRows(rows);
      let swings = findSwings(paRows, swingLookback($("timeframe").value));
      swings = enrichSwings(paRows, swings);
      const breaks = findStructureBreaks(paRows, swings);
      const smcObs = findSmcOrderBlocks(rows, breaks);
      const structure = classifyStructure(swings.highs, swings.lows);
      if (breaks.length) structure.label = breaks[breaks.length - 1].type + " · " + breaks[breaks.length - 1].direction.toLowerCase();
      const obs = (analysis && (analysis.valid_order_blocks || analysis.order_blocks)) || [];
      const chartTf = $("timeframe").value;
      const markers = [];
      let projection = null;

      let r3Markers = [];
      const obCount = nearestOrderBlocks([...smcObs, ...obs], rows[rows.length - 1].close, 5).length;

      if (paEnabled()) {
        try {
          if ($("togR1").checked) {
            markers.push(...drawRule1Structure(rows, swings, breaks));
            markers.push(...drawHtfWaveLevels(rows, lastHtfRows, chartTf));
          }
        } catch (err) { console.warn("R1 overlay:", err); }
        try {
          if ($("togR2").checked) drawRule2Levels(rows, swings, obs, smcObs);
        } catch (err) { console.warn("R2 overlay:", err); }
        try {
          if ($("togR3").checked) r3Markers = detectConfirmations(rows, swings);
          markers.push(...r3Markers);
        } catch (err) { console.warn("R3 overlay:", err); }
        updatePaSummary(structure, swings, r3Markers.length, obCount);
      } else {
        updatePaSummary(null, null, 0);
      }

      try {
        if ($("togNextMove").checked) {
          projection = computeNextMove(rows, swings, structure, analysis, obs);
          if (projection) markers.push(...drawNextMove(projection));
        }
        updateNextMovePanel(projection);
      } catch (err) { console.warn("Next move:", err); }

      try {
        if ($("togSmc").checked) {
          const base = analysis?.smc || {};
          const inducements = (base.inducements && base.inducements.length)
            ? base.inducements
            : localInducementsFromRows(rows, swings);
          const smcPayload = { ...base, inducements };
          renderSmcOverlays(smcPayload);
          markers.push(...smcChartMarkers(smcPayload));
        } else {
          clearSmcOverlays();
        }
      } catch (err) { console.warn("SMC overlay:", err); }

      try {
        if ($("togPaSmc").checked) {
          const beluga = analysis?.beluga_smc;
          renderBbSmcOverlays(beluga);
          markers.push(...bbSmcChartMarkers(beluga));
        } else {
          clearBbSmcOverlays();
        }
      } catch (err) { console.warn("BigBeluga SMC:", err); }

      try {
        if ($("togSessions").checked) {
          renderSessionOverlays(rows);
          markers.push(...sessionChartMarkers(rows));
        } else {
          clearSessionOverlays();
        }
      } catch (err) { console.warn("Sessions:", err); }

      const validMarkers = markers
        .filter((m) => m && Number.isFinite(m.time))
        .sort((a, b) => a.time - b.time)
        .slice(-MAX_CHART_MARKERS);
      if (candleMarkers) {
        try { candleMarkers.setMarkers(validMarkers); } catch (err) {
          console.warn("Markers:", err);
        }
      }
    }

    function updatePaSummary(structure, swings, confCount, obCount) {
      const st = $("sumStructure");
      if (!structure) {
        ["sumStructure", "sumLabel", "sumSH", "sumSL", "sumOB", "sumConf"].forEach((id) => $(id).textContent = "—");
        st.className = "val neu";
        return;
      }
      st.textContent = structure.trend;
      st.className = "val " + (structure.trend === "UP" ? "up" : structure.trend === "DOWN" ? "dn" : "neu");
      $("sumLabel").textContent = structure.label;
      $("sumSH").textContent = swings ? String(swings.highs.length) : "—";
      $("sumSL").textContent = swings ? String(swings.lows.length) : "—";
      $("sumOB").textContent = obCount != null ? String(obCount) : "—";
      $("sumConf").textContent = String(confCount || 0);
    }

    function renderChart(rows, meta) {
      if (!renderCandlesOnly(rows, meta)) return;
      scheduleRenderOverlays();
    }

    async function fetchHtfCandles(sym, chartTf, count, key, base) {
      const htf = htfForTf(chartTf);
      if (!htf) { lastHtfRows = []; return; }
      const htfCount = Math.min(300, Math.max(80, Math.floor(count / 5)));
      try {
        const res = await fetch(`${base}/getCandles?symbol=${encodeURIComponent(sym)}&timeframe=${htf}&count=${htfCount}`, {
          headers: { "X-API-Key": key },
        });
        const data = res.ok ? await res.json() : null;
        lastHtfRows = data?.candles ? candlesToRows(data.candles) : [];
      } catch (_) {
        lastHtfRows = [];
      }
    }

    async function fetchFromApi() {
      await detectBrokerSymbol();
      const sym = $("symbol").value.trim();
      const tf = $("timeframe").value;
      const count = $("barCount").value;
      const key = apiKey();
      const base = baseUrl();
      setStatus("Loading candles…");
      const cRes = await fetch(`${base}/getCandles?symbol=${encodeURIComponent(sym)}&timeframe=${tf}&count=${count}`, {
        headers: { "X-API-Key": key },
      });
      const cData = await cRes.json();
      if (!cRes.ok || !cData.candles) throw new Error(cData.error || "Candles failed");
      const rows = candlesToRows(cData.candles);
      if (!rows.length) throw new Error("No valid candles after parse");
      const partialMeta = `<b>${cData.symbol || sym}</b> · ${tf} · ${rows.length} bars<br>Source: Live API · loading analysis…`;
      renderCandlesOnly(rows, partialMeta);
      setStatus(`Loaded ${rows.length} candles · fetching analysis…`);
      try {
        const aRes = await fetch(`${base}/getAnalysis?symbol=${encodeURIComponent(sym)}&timeframe=${tf}&count=${count}`, {
          headers: { "X-API-Key": key },
        });
        lastAnalysis = aRes.ok ? await aRes.json() : null;
      } catch (_) {
        lastAnalysis = null;
      }
      await fetchHtfCandles(sym, tf, count, key, base);
      const indN = lastAnalysis?.smc?.inducement_count ?? lastAnalysis?.chart?.smc_inducement_count;
      const smcInfo = lastAnalysis?.chart
        ? ` · SMC FVG ${lastAnalysis.chart.smc_fvg_count ?? "—"} · ${lastAnalysis.chart.smc_last_break || "—"}`
          + (indN != null ? ` · ⚡ ${indN}` : "")
          + (lastAnalysis.chart.beluga_ob_count != null ? ` · BB OB ${lastAnalysis.chart.beluga_ob_count}` : "")
        : (lastAnalysis?.smc ? "" : " · <span class=err>SMC: update API v1.8.6+</span>");
      const meta = `<b>${cData.symbol || sym}</b> · ${tf} · ${rows.length} bars${smcInfo}<br>Source: Live API · ${new Date().toLocaleString()}`;
      $("sumMeta").innerHTML = meta;
      scheduleRenderOverlays();
      setStatus(`Loaded ${rows.length} bars from API`);
      $("statusRight").textContent = `${sym} ${tf} · API`;
    }

    async function localCandlePaths(sym, tf) {
      const counts = ["20k", "50k", "99k", "5000", "2000", "1000"];
      const roots = ["/candle_data/", "../../candle_data/", "../../../candle_data/"];
      const paths = [];
      for (const root of roots) {
        for (const n of counts) {
          paths.push(`${root}${sym}_${tf}_${n}.json`);
        }
        paths.push(`${root}${sym}_${tf}.json`);
      }
      return paths;
    }

    async function fetchFromLocal() {
      const sym = $("symbol").value.trim();
      const tf = $("timeframe").value;
      const want = parseInt($("barCount").value, 10) || 500;
      setStatus("Loading local JSON…");
      lastAnalysis = null;
      lastHtfRows = [];
      let data = null;
      let usedPath = "";
      for (const path of await localCandlePaths(sym, tf)) {
        try {
          const res = await fetch(path);
          if (!res.ok) continue;
          const j = await res.json();
          if (j.candles && j.candles.length) {
            data = j;
            usedPath = path;
            break;
          }
        } catch (_) {}
      }
      if (!data) throw new Error(`No local file for ${sym} ${tf}. Serve from repo root (see serve-alpha-analyser.sh).`);
      let rows = candlesToRows(data.candles);
      if (rows.length > want) rows = rows.slice(-want);
      const meta = `<b>${data.symbol || sym}</b> · ${data.timeframe || tf} · ${rows.length} bars<br>File: <code>${usedPath}</code>`;
      renderChart(rows, meta);
      setStatus(`Loaded ${rows.length} bars (local)`);
      $("statusRight").textContent = `${sym} ${tf} · local`;
    }

    async function loadChart(silent = false) {
      if (loadInProgress) return;
      loadInProgress = true;
      if (!silent) saveSettings();
      try {
        if ($("dataSource").value === "local") await fetchFromLocal();
        else await fetchFromApi();
      } catch (e) {
        setStatus("Error: " + e.message, false);
      } finally {
        loadInProgress = false;
      }
    }

    function syncPaToggles() {
      const on = paEnabled();
      ["togR1", "togR2", "togR3"].forEach((id) => { $(id).disabled = !on; });
      renderPriceAction(lastCandles, lastAnalysis);
    }

    $("btnLoad").addEventListener("click", () => loadChart(false));
    $("togAutoRefresh").addEventListener("change", () => {
      saveSettings();
      syncAutoRefresh();
      if ($("togAutoRefresh").checked && !lastCandles.length) loadChart(true);
    });
    $("dataSource").addEventListener("change", toggleServerField);
    $("togPriceAction").addEventListener("change", syncPaToggles);
    $("togNextMove").addEventListener("change", () => { saveSettings(); renderPriceAction(lastCandles, lastAnalysis); });
    $("nextMoveLabel").addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!$("togNextMove").checked) {
        $("togNextMove").checked = true;
        saveSettings();
        renderPriceAction(lastCandles, lastAnalysis);
      }
      $("nextMoveBox").scrollIntoView({ behavior: "smooth", block: "nearest" });
      toggleNextMoveLogic(true);
    });
    $("nextMoveBox").addEventListener("click", () => toggleNextMoveLogic());
    $("togSmc").addEventListener("change", () => { saveSettings(); renderPriceAction(lastCandles, lastAnalysis); });
    $("togPaSmc").addEventListener("change", () => { saveSettings(); renderPriceAction(lastCandles, lastAnalysis); });
    $("togSessions").addEventListener("change", () => { saveSettings(); renderPriceAction(lastCandles, lastAnalysis); });
    ["togR1", "togR2", "togR3"].forEach((id) => $(id).addEventListener("change", () => renderPriceAction(lastCandles, lastAnalysis)));
    ["symbol", "timeframe", "barCount", "apiServer"].forEach((id) => {
      $(id).addEventListener("change", saveSettings);
      if (id === "symbol" || id === "apiServer") $(id).addEventListener("input", saveSettings);
    });
    $("apiServer").addEventListener("change", () => detectBrokerSymbol());

    if (location.protocol === "file:") {
      $("backLink").href = "../dashboard.html";
      setStatus("Run ./run-dev.sh — file:// blocks fetch", false);
    }

    loadSettings();
    initChart();
    bootstrapFromServer();
    syncAutoRefresh();
    if ($("togAutoRefresh").checked) loadChart(true);
