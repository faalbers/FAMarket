/**
 * /charts?view=valuation_range — the "football field": each symbol's bear→bull
 * fair-value span, as percent above/below today's price.
 *
 * The zero line IS the current price, so anything right of it is worth more
 * than it costs. Normalising that way lets a $5 stock and a $500 stock share
 * one axis. All maths (including clamping) happens server-side in
 * `services/valuation_data.py`; this file only renders.
 *
 * Three things it must show honestly, all real cases rather than edge cases:
 *   - the base marker may sit OUTSIDE its bar (Graham is in the base blend but
 *     not the scenarios) — drawn where it truly is, never clamped into the bar;
 *   - a symbol with no computable scenario gets a row marked "no range", not a
 *     zero-width bar that would read as certainty;
 *   - a bar past the axis cap is clipped with a ▶ and its true value in the
 *     label, so an absurd estimate looks absurd instead of eating the axis.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { EChart, ECHARTS_BASE, axisStyle } from "@/components/EChart";
import { EmptyState, PageHeader, Panel } from "@/components/ui";

type Row = {
  symbol: string;
  name: string;
  price: number;
  has_range: boolean;
  guardrail: boolean;
  bear_flags: number;
  bear: number | null;
  base: number | null;
  bull: number | null;
  bear_value: number | null;
  base_value: number | null;
  bull_value: number | null;
  bear_true_pct: number | null;
  base_true_pct: number | null;
  bull_true_pct: number | null;
  bear_overflow_high: boolean;
  bull_overflow_high: boolean;
  base_overflow_high: boolean;
  bear_overflow_low: boolean;
  bull_overflow_low: boolean;
  base_overflow_low: boolean;
};

type ValuationResponse = {
  rows: Row[];
  missing: string[];
  axis: { min: number; max: number };
  labels: Record<string, string>;
  n_no_range: number;
  message: string | null;
};

// Blue = the range itself, amber reserved for the warnings (no red/green
// anywhere). Colour is never the only cue here: the span also carries a printed
// value label, the base is a distinct SHAPE, and price is a dashed reference.
const SPAN = "#4a9eff";
const MARKER = "#e6eaf2";
const WARN = "#f0a202";
const ROW_PX = 30;

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(0)}%`;

export function ValuationRangeView({ symbols }: { symbols: string[] }) {
  const { data, isLoading } = useQuery({
    queryKey: ["chart-valuation-range", symbols],
    queryFn: () =>
      get<ValuationResponse>("/charts/valuation-range", { symbols: symbols.join(",") }),
    staleTime: 5 * 60_000,
  });

  const option = useMemo(() => {
    const rows = data?.rows ?? [];
    if (!rows.length) return null;

    // ECharts category axes run bottom-up, so reverse to keep the server's
    // most-undervalued-first order reading top-down on screen.
    const ordered = [...rows].reverse();
    const axis = data?.axis ?? { min: -100, max: 200 };

    // Floating bar = transparent spacer to `bear`, then the visible span.
    // stackStrategy "all" is REQUIRED: bear is routinely negative while bull is
    // positive, and ECharts stacks positive and negative values separately by
    // default, which would tear mixed-sign bars apart.
    const spacer = ordered.map((r) => (r.has_range ? r.bear : null));
    const span = ordered.map((r) =>
      r.has_range && r.bear !== null && r.bull !== null ? r.bull - r.bear : null,
    );

    return {
      ...ECHARTS_BASE,
      grid: { left: 8, right: 128, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        ...ECHARTS_BASE.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: { dataIndex: number }[]) => {
          const r = ordered[params?.[0]?.dataIndex ?? 0];
          if (!r) return "";
          const line = (label: string, p: number | null, v: number | null, over: boolean) =>
            v === null
              ? ""
              : `${label}: $${v.toFixed(2)} (${pct(p)}${over ? ", beyond axis" : ""})<br/>`;
          return (
            `<b>${r.symbol}</b> — ${r.name}<br/>` +
            `Price: $${r.price.toFixed(2)}<br/>` +
            line("Bear", r.bear_true_pct, r.bear_value, r.bear_overflow_high) +
            line("Base", r.base_true_pct, r.base_value, r.base_overflow_high) +
            line("Bull", r.bull_true_pct, r.bull_value, r.bull_overflow_high) +
            (r.has_range ? "" : "<i>No range available</i><br/>") +
            (r.guardrail ? "⚠ Valuation assumptions at the edge of plausibility<br/>" : "") +
            (r.bear_flags ? `⚠ ${r.bear_flags} bear flag(s)<br/>` : "")
          );
        },
      },
      xAxis: axisStyle({
        type: "value",
        min: axis.min,
        max: axis.max,
        axisLabel: { color: "#8b93a7", formatter: (v: number) => `${v}%` },
      }),
      yAxis: axisStyle({
        type: "category",
        data: ordered.map((r) => r.symbol),
        splitLine: { show: false },
        axisLabel: {
          color: "#8b93a7",
          // Mark the rows that have no range right on the label, so the gap in
          // the chart is never left unexplained.
          formatter: (s: string) => {
            const r = ordered.find((x) => x.symbol === s);
            return r && !r.has_range ? `${s} ·` : s;
          },
        },
      }),
      series: [
        {
          name: "spacer",
          type: "bar",
          stack: "range",
          stackStrategy: "all",
          silent: true,
          itemStyle: { color: "transparent" },
          data: spacer,
          barMaxWidth: 16,
        },
        {
          name: "Bear → Bull",
          type: "bar",
          stack: "range",
          stackStrategy: "all",
          itemStyle: { color: SPAN, borderRadius: 2 },
          barMaxWidth: 16,
          data: span,
          label: {
            show: true,
            position: "right",
            color: "#8b93a7",
            fontSize: 10,
            formatter: (p: { dataIndex: number }) => {
              const r = ordered[p.dataIndex];
              if (!r || !r.has_range) return ""; // labelled on the marker series instead
              // A collapsed range is a real result (every scenario clipped to the
              // same growth cap), but a zero-length bar draws nothing — so say it
              // in words rather than printing "-47% … -47%" and leaving the eye
              // hunting for a bar that was never there.
              if (r.bear_true_pct === r.bull_true_pct) {
                return `${pct(r.bear_true_pct)} · no spread`;
              }
              // "▶" marks the end that ran past the axis; the number beside it is
              // already the TRUE value, so it is never printed twice.
              const lo = (r.bear_overflow_low ? "◀" : "") + pct(r.bear_true_pct);
              const hi = (r.bull_overflow_high ? "▶" : "") + pct(r.bull_true_pct);
              return `${lo} … ${hi}`;
            },
          },
          markLine: {
            silent: true,
            symbol: "none",
            // Dashed because it marks a reference level, not data.
            lineStyle: { color: "#8b93a7", type: "dashed", width: 1 },
            label: { show: false },
            data: [{ xAxis: 0 }],
          },
        },
        {
          // Base fair value. A LINE series with hidden line + diamond symbol:
          // ScatterChart and MarkPoint are not registered in EChart.tsx, and an
          // unregistered component fails silently.
          name: "Base fair value",
          type: "line",
          lineStyle: { opacity: 0 },
          symbol: "diamond",
          symbolSize: 11,
          itemStyle: {
            color: MARKER,
            borderColor: SPAN,
            borderWidth: 1,
          },
          data: ordered.map((r, i) => (r.base === null ? null : [r.base, i])),
          z: 5,
          // The "no range" wording has to ride HERE, not on the bar: that row's
          // bar data is null, and ECharts draws no label for a null point — the
          // row would otherwise show a lone marker and read as a confident
          // estimate, which is the exact false-certainty this view exists to
          // avoid. Positioned left so a marker pinned at the axis cap stays legible.
          label: {
            show: true,
            position: "left",
            color: "#8b93a7",
            fontSize: 10,
            formatter: (p: { dataIndex: number }) => {
              const r = ordered[p.dataIndex];
              if (!r || r.has_range) return "";
              const at = (r.base_overflow_high ? "▶" : "") + pct(r.base_true_pct);
              return `no range · base ${at}`;
            },
          },
        },
        {
          // Warning overlay: a small amber ring on rows carrying a guardrail
          // flag, so a shaky valuation is visible without hovering.
          name: "Flagged",
          type: "line",
          lineStyle: { opacity: 0 },
          symbol: "circle",
          symbolSize: 5,
          itemStyle: { color: WARN },
          data: ordered.map((r, i) => (r.guardrail && r.base !== null ? [r.base, i] : null)),
          z: 6,
        },
      ],
    };
  }, [data]);

  const rows = data?.rows ?? [];
  const caption = [
    `${rows.length} symbol${rows.length === 1 ? "" : "s"}`,
    "0% = today's price",
    data?.n_no_range ? `${data.n_no_range} without a range` : "",
    data?.missing?.length ? `${data.missing.length} not found` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Valuation range" caption={caption} />
      <Panel
        title="Bear → Bull fair value vs. price"
        className="min-h-0 flex-1"
        bodyClassName="flex min-h-0 flex-1 flex-col overflow-auto"
      >
        {isLoading ? (
          <EmptyState title="Loading…" />
        ) : option ? (
          // Height grows with the row count; the Panel body scrolls. A chart
          // host sized only by flex-1 inside an overflow-auto ancestor would
          // collapse to a sliver.
          <div
            className="relative w-full"
            style={{ height: Math.max(240, rows.length * ROW_PX + 72) }}
          >
            <EChart option={option} className="absolute inset-0" />
          </div>
        ) : (
          <EmptyState title="Nothing to plot" detail={data?.message ?? undefined} />
        )}
      </Panel>
    </div>
  );
}
