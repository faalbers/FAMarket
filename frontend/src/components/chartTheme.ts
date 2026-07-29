import type { DeepPartial, ChartOptions, LineStyle } from "lightweight-charts";

export const CHART_LAYOUT: DeepPartial<ChartOptions> = {
  autoSize: true,
  layout: {
    background: { color: "#11151f" },
    textColor: "#8b93a7",
    fontFamily: "ui-monospace, Consolas, monospace",
    fontSize: 11,
    attributionLogo: true,
    panes: { separatorColor: "#232a3b", separatorHoverColor: "#2f3850" },
  },
  grid: {
    vertLines: { color: "#1a2030" },
    horzLines: { color: "#1a2030" },
  },
  rightPriceScale: { borderColor: "#232a3b" },
  timeScale: { borderColor: "#232a3b", rightOffset: 4 },
  crosshair: {
    mode: 1,
    vertLine: { color: "#6ea8fe", labelBackgroundColor: "#2a3550" },
    horzLine: { color: "#6ea8fe", labelBackgroundColor: "#2a3550" },
  },
};

/**
 * Okabe-Ito, reordered so the most-used slots are maximally separable for
 * red-weak vision, and with vermillion pushed last so it never lands next to
 * the green.
 *
 * Hue is never the only cue for identity, but on a dense chart the second cue
 * is a DIRECT LABEL (each line named at its right edge, plus the named readout
 * strip), not a dash pattern — see SERIES_DASH below.
 */
export const SERIES_COLORS = [
  "#56B4E9", // sky blue
  "#E69F00", // orange
  "#0072B2", // deep blue
  "#CC79A7", // purple
  "#009E73", // bluish green
  "#F0E442", // yellow
  "#B478F0", // violet
  "#D55E00", // vermillion
] as const;

/**
 * Dash patterns for SPARSE charts only — a handful of lines with few points,
 * where a pattern reads clearly (e.g. an indicator overlay against price).
 *
 * Do NOT use these on dense time series: at ~750 daily points in a few hundred
 * pixels a dashed line reads as noise, or worse as gaps in the data that aren't
 * there. Those charts stay solid and rely on direct labels for identity.
 *
 * LineStyle enum values: 0 solid, 1 dotted, 2 dashed, 3 large dashed.
 */
export const SERIES_DASH: LineStyle[] = [0, 2, 0, 1, 0, 2, 3, 0] as LineStyle[];

export const SOLID: LineStyle = 0 as LineStyle;

export const seriesColor = (i: number) => SERIES_COLORS[i % SERIES_COLORS.length]!;
export const seriesDash = (i: number) => SERIES_DASH[i % SERIES_DASH.length]!;
