/** Draw server-computed overlay layers — no analysis logic on client. */

export type LayerSpec = {
  lines?: Array<{ t0: number; t1: number; price: number; color: string; width?: number; style?: number }>;
  polylines?: Array<{
    points: Array<{ time: number; price: number }>;
    color: string;
    width?: number;
    style?: number;
  }>;
  areas?: Array<{ t0: number; t1: number; low: number; high: number; fill: string; border: string }>;
  baselines?: Array<{ t0: number; t1: number; bottom: number; top: number; bullish: boolean; mitigated?: boolean }>;
  price_lines?: Array<{ price: number; color: string; title: string; width?: number; style?: number }>;
  glow_lines?: Array<{ t0: number; t1: number; price: number; title?: string }>;
  markers?: Array<{ time: number; position: string; shape: string; color: string; text?: string }>;
};

export type RenderBundle = {
  summary?: Record<string, unknown>;
  next_move?: Record<string, unknown> | null;
  layers?: Record<string, LayerSpec>;
};

export type ChartOverlayState = {
  chart: any;
  candleSeries: any;
  candleMarkers: any;
  lineSeries: any[];
  priceLines: any[];
  smcSeries: any[];
};

const BB_PAL = {
  bullLine: "#089981",
  bearLine: "#f23645",
  bullFill: "rgba(8,153,129,0.22)",
  bearFill: "rgba(242,54,69,0.22)",
  mitLine: "#64748b",
  mitFill: "rgba(100,116,139,0.35)",
};

const SMC_PAL = {
  bullLine: "#16a34a",
  bearLine: "#dc2626",
  bullFill: "rgba(34,197,94,0.42)",
  bearFill: "rgba(239,68,68,0.42)",
  mitLine: "#94a3b8",
  mitFill: "rgba(148,163,184,0.50)",
};

export function createOverlayState(chart: any, candleSeries: any, candleMarkers: any): ChartOverlayState {
  return { chart, candleSeries, candleMarkers, lineSeries: [], priceLines: [], smcSeries: [] };
}

export function clearOverlays(state: ChartOverlayState) {
  if (!state.chart) return;
  for (const s of [...state.lineSeries, ...state.smcSeries]) {
    try { state.chart.removeSeries(s); } catch (_) { /* ignore */ }
  }
  state.lineSeries = [];
  state.smcSeries = [];
  for (const ln of state.priceLines) {
    try { state.candleSeries?.removePriceLine(ln); } catch (_) { /* ignore */ }
  }
  state.priceLines = [];
  try { state.candleMarkers?.setMarkers([]); } catch (_) { /* ignore */ }
}

function drawBaselineBox(state: ChartOverlayState, item: NonNullable<LayerSpec["baselines"]>[0], pal: typeof SMC_PAL) {
  const LC = window.LightweightCharts;
  const lineColor = item.mitigated ? pal.mitLine : item.bullish ? pal.bullLine : pal.bearLine;
  const fill = item.mitigated ? pal.mitFill : item.bullish ? pal.bullFill : pal.bearFill;
  const s = state.chart.addSeries(LC.BaselineSeries, {
    baseValue: { type: "price", price: item.bottom },
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
  s.setData([{ time: item.t0, value: item.top }, { time: item.t1, value: item.top }]);
  state.smcSeries.push(s);
}

/** Reconstruct v1-style connected paths from legacy horizontal segment pairs. */
function upgradeLegacyPolylines(layer: LayerSpec, colors: string[]): LayerSpec {
  if (layer.polylines?.length) return layer;
  const targets = new Set(colors);
  const legacy = (layer.lines || []).filter((ln) => targets.has(ln.color));
  if (legacy.length < 2) return layer;

  const bySpan = new Map<string, number[]>();
  for (const ln of legacy) {
    const key = `${ln.t0}|${ln.t1}`;
    const bucket = bySpan.get(key) || [];
    bucket.push(ln.price);
    bySpan.set(key, bucket);
  }

  const points: Array<{ time: number; price: number }> = [];
  const spans = [...bySpan.entries()].sort((a, b) => {
    const ta = Number(a[0].split("|")[0]);
    const tb = Number(b[0].split("|")[0]);
    return ta - tb;
  });

  for (const [key, prices] of spans) {
    const [t0, t1] = key.split("|").map(Number);
    prices.sort((a, b) => a - b);
    if (!points.length) points.push({ time: t0, price: prices[0] });
    points.push({ time: t1, price: prices[prices.length - 1] });
  }

  if (points.length < 2) return layer;

  const color = legacy[0].color;
  const width = legacy[0].width || 2;
  const style = legacy[0].style ?? 0;
  const legacyKeys = new Set(spans.map(([k]) => k));

  return {
    ...layer,
    lines: (layer.lines || []).filter((ln) => {
      if (!targets.has(ln.color)) return true;
      return !legacyKeys.has(`${ln.t0}|${ln.t1}`);
    }),
    polylines: [{ points, color, width, style }],
  };
}

function normalizeLayer(layer: LayerSpec | undefined): LayerSpec | undefined {
  if (!layer) return layer;
  let next = layer;
  next = upgradeLegacyPolylines(next, ["rgba(248,250,252,0.92)"]);
  next = upgradeLegacyPolylines(next, ["rgba(34,197,94,.8)", "rgba(239,68,68,.8)"]);
  return next;
}

function drawPolyline(state: ChartOverlayState, item: NonNullable<LayerSpec["polylines"]>[0]) {
  const LC = window.LightweightCharts;
  const pts = (item.points || [])
    .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.price))
    .map((p) => ({ time: p.time, value: p.price }));
  if (pts.length < 2) return;
  const s = state.chart.addSeries(LC.LineSeries, {
    color: item.color,
    lineWidth: item.width || 2,
    lineStyle: item.style ?? 0,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
  s.setData(pts);
  state.lineSeries.push(s);
}

function drawGlowHLine(state: ChartOverlayState, t0: number, t1: number, price: number) {
  const LC = window.LightweightCharts;
  const stacks = [
    { color: "rgba(250,204,21,0.12)", width: 6, style: 2 },
    { color: "rgba(250,204,21,0.28)", width: 3, style: 2 },
    { color: "rgba(255,235,59,0.95)", width: 1, style: 2 },
  ];
  for (const st of stacks) {
    const s = state.chart.addSeries(LC.LineSeries, {
      color: st.color,
      lineWidth: st.width,
      lineStyle: st.style,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData([{ time: t0, value: price }, { time: t1, value: price }]);
    state.lineSeries.push(s);
  }
}

export function applyPdLevelsLayer(state: ChartOverlayState, layer: LayerSpec | undefined) {
  if (!layer || !state.chart) return;
  for (const gl of layer.glow_lines || []) {
    if (!Number.isFinite(gl.t0) || !Number.isFinite(gl.t1) || !Number.isFinite(gl.price)) continue;
    drawGlowHLine(state, gl.t0, gl.t1, gl.price);
  }
  for (const pl of layer.price_lines || []) {
    state.priceLines.push(
      state.candleSeries.createPriceLine({
        price: pl.price,
        color: pl.color || "#facc15",
        lineWidth: pl.width || 1,
        lineStyle: pl.style ?? 2,
        axisLabelVisible: true,
        title: pl.title,
      }),
    );
  }
}

export function applyLayer(state: ChartOverlayState, layer: LayerSpec | undefined, baselinePal = SMC_PAL) {
  if (!layer || !state.chart) return;
  const LC = window.LightweightCharts;
  const spec = normalizeLayer(layer) || layer;

  for (const pl of spec.polylines || []) drawPolyline(state, pl);

  for (const ln of spec.lines || []) {
    const s = state.chart.addSeries(LC.LineSeries, {
      color: ln.color,
      lineWidth: ln.width || 1,
      lineStyle: ln.style ?? 0,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData([{ time: ln.t0, value: ln.price }, { time: ln.t1, value: ln.price }]);
    state.lineSeries.push(s);
  }

  for (const a of spec.areas || []) {
    const s = state.chart.addSeries(LC.AreaSeries, {
      topColor: a.fill,
      bottomColor: a.fill,
      lineColor: a.border,
      lineWidth: 1,
      baseValue: { type: "price", price: a.low },
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData([{ time: a.t0, value: a.high }, { time: a.t1, value: a.high }]);
    state.lineSeries.push(s);
  }

  for (const b of spec.baselines || []) {
    drawBaselineBox(state, b, baselinePal);
  }

  for (const pl of spec.price_lines || []) {
    state.priceLines.push(
      state.candleSeries.createPriceLine({
        price: pl.price,
        color: pl.color,
        lineWidth: pl.width || 1,
        lineStyle: pl.style ?? 0,
        axisLabelVisible: true,
        title: pl.title,
      }),
    );
  }
}

export type ToggleState = {
  priceAction: boolean;
  r1: boolean;
  r2: boolean;
  r3: boolean;
  rsiDiv: boolean;
  pdLevels: boolean;
  smc: boolean;
  paSmc: boolean;
  nextMove: boolean;
};

export function collectMarkers(layers: Record<string, LayerSpec>, toggles: ToggleState, max = 48) {
  const out: any[] = [];
  const push = (layer: LayerSpec | undefined) => {
    for (const m of layer?.markers || []) {
      if (m && Number.isFinite(m.time)) out.push(m);
    }
  };
  if (toggles.priceAction && toggles.r1) push(layers.r1);
  if (toggles.priceAction && toggles.r3) push(layers.r3);
  if (toggles.rsiDiv) push(layers.rsi_div);
  if (toggles.nextMove) push(layers.next_move);
  if (toggles.smc) push(layers.smc);
  if (toggles.paSmc) push(layers.beluga);
  return out.sort((a, b) => a.time - b.time).slice(-max);
}

export function renderServerBundle(state: ChartOverlayState, bundle: RenderBundle, toggles: ToggleState) {
  clearOverlays(state);
  const layers = bundle.layers || {};

  if (toggles.priceAction && toggles.r1) applyLayer(state, layers.r1);
  if (toggles.priceAction && toggles.r2) applyLayer(state, layers.r2);
  if (toggles.rsiDiv) applyLayer(state, layers.rsi_div);
  if (toggles.pdLevels) applyPdLevelsLayer(state, layers.pd_levels);
  if (toggles.smc) applyLayer(state, layers.smc, SMC_PAL);
  if (toggles.paSmc) applyLayer(state, layers.beluga, BB_PAL);
  if (toggles.nextMove) applyLayer(state, layers.next_move);

  const markers = collectMarkers(layers, toggles);
  if (state.candleMarkers && markers.length) {
    try { state.candleMarkers.setMarkers(markers); } catch (_) { /* ignore */ }
  }
}
