/**
 * Typed search-param validators.
 *
 * These live in their own module on purpose: `validateSearch` runs at router
 * setup, so it is statically imported — if it sat in the page file, that whole
 * page would be pulled into the main chunk and its lazy import would be dead.
 */

/** Accept both `A,B` (hand-typeable) and repeated/array forms. */
export function csvList(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map((s) => String(s).trim()).filter(Boolean);
  if (typeof raw === "string") {
    return raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

/** Symbols are canonically upper-case everywhere in the system. */
export function symbolList(raw: unknown): string[] {
  return csvList(raw).map((s) => s.toUpperCase());
}

export type OutputSearch = { run?: string };

export function validateOutputSearch(raw: Record<string, unknown>): OutputSearch {
  const run = typeof raw.run === "string" && raw.run ? raw.run : undefined;
  return run ? { run } : {};
}

export const CHART_VIEWS = [
  "price",
  "fundamentals_bar",
  "fundamentals_line",
  "radar",
  "dividend_line",
  "heatmap",
  "scores_heatmap",
  "news",
  "filter_fail",
] as const;

export type ChartView = (typeof CHART_VIEWS)[number];

export type ChartSearch = { view: ChartView; symbols: string[]; cols: string[] };

export function validateChartSearch(raw: Record<string, unknown>): ChartSearch {
  const view = CHART_VIEWS.includes(raw.view as ChartView) ? (raw.view as ChartView) : "price";
  return { view, symbols: symbolList(raw.symbols), cols: csvList(raw.cols) };
}
