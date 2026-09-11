/**
 * Single lightweight-charts instance — re-export full LWC API (Area, Baseline, etc.)
 * with alphafx-style createChart option normalization.
 */
import * as LWC from "lightweight-charts";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  type ChartOptions,
  type DeepPartial,
} from "lightweight-charts";

type LayoutInput = {
  background?: { color: string } | ChartOptions["layout"]["background"];
  textColor?: string;
};

type ChartInput = DeepPartial<{
  width: number;
  height: number;
  layout: LayoutInput;
  grid: ChartOptions["grid"];
  crosshair: ChartOptions["crosshair"];
  timeScale: ChartOptions["timeScale"];
  rightPriceScale: ChartOptions["rightPriceScale"];
}>;

function normalizeOptions(options?: ChartInput): DeepPartial<ChartOptions> {
  if (!options) return { layout: { attributionLogo: false } };
  const next: DeepPartial<ChartOptions> = { ...options };
  if (options.layout) {
    const bg = options.layout.background;
    if (bg && typeof bg === "object" && "color" in bg && !("type" in bg)) {
      next.layout = {
        ...options.layout,
        background: { type: ColorType.Solid, color: bg.color },
        attributionLogo: false,
      };
    } else {
      next.layout = { ...options.layout, attributionLogo: false };
    }
  }
  return next;
}

function createAlphaChart(
  container: string | HTMLElement,
  options?: ChartInput,
) {
  const el =
    typeof container === "string"
      ? (document.querySelector(container) as HTMLElement)
      : container;
  const chart = createChart(el, normalizeOptions(options));
  const compat = chart as typeof chart & {
    addCandlestickSeries: (opts?: object) => ReturnType<typeof chart.addSeries>;
  };
  compat.addCandlestickSeries = (opts = {}) =>
    chart.addSeries(CandlestickSeries, opts);
  return compat;
}

/** Full LWC namespace + normalized createChart (matches unpkg standalone surface). */
export const LightweightCharts = {
  ...LWC,
  createChart: createAlphaChart,
  engine: "alphafx-charts",
  version: "0.1.0",
} as const;

declare global {
  interface Window {
    LightweightCharts: typeof LightweightCharts;
    AlphaFXCharts?: typeof LightweightCharts;
  }
}

window.LightweightCharts = LightweightCharts;
window.AlphaFXCharts = LightweightCharts;
