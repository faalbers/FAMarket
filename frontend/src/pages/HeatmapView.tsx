/**
 * /charts?view=heatmap and ?view=scores_heatmap — symbols × metrics, each cell
 * coloured by its scoring-rule goodness.
 *
 * Colour runs blue (weak) → pale → orange (strong), never red/green, and the
 * value is printed in each cell whenever the grid is small enough — so the
 * reading never depends on hue alone. Goodness is ranked server-side against
 * the FULL universe, not just the charted symbols.
 *
 * Click a column header to sort the rows by that metric; click again to flip.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ECElementEvent } from "echarts/core";
import { get } from "@/lib/api";
import { EChart, ECHARTS_BASE } from "@/components/EChart";
import { ColumnPicker } from "@/components/ColumnPicker";
import type { ColumnInfo } from "@/lib/runs";
import { EmptyState, PageHeader, Panel } from "@/components/ui";

type Cell = { symbol: string; metric: string; value: number | null; goodness: number | null };
type HeatmapResponse = {
  symbols: string[];
  missing: string[];
  metrics: { key: string; label: string; unit: string; verdict: string }[];
  cells: Cell[];
  ramp: string[];
  message: string | null;
};

export function HeatmapView({
  symbols,
  cols,
  kind,
}: {
  symbols: string[];
  cols: string[];
  kind: "metrics" | "scores";
}) {
  const [picked, setPicked] = useState<string[]>(cols);
  const [sort, setSort] = useState<{ col: string; desc: boolean } | null>(null);

  const { data: optionData } = useQuery({
    queryKey: ["heatmap-options", kind],
    queryFn: () => get<{ options: ColumnInfo[] }>("/charts/heatmap/options", { kind }),
    staleTime: Infinity,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["chart-heatmap", kind, symbols, picked],
    queryFn: () =>
      get<HeatmapResponse>("/charts/heatmap", {
        symbols: symbols.join(","),
        cols: picked.join(","),
        kind,
      }),
    staleTime: 5 * 60_000,
  });

  const option = useMemo(() => {
    if (!data || data.cells.length === 0) return null;

    const metrics = data.metrics;
    const byKey = new Map(data.cells.map((c) => [`${c.symbol}|${c.metric}`, c]));

    // Rows read top-down, so the y-axis order is reversed against display order.
    let rows = [...data.symbols];
    if (sort) {
      rows = [...rows].sort((a, b) => {
        const va = byKey.get(`${a}|${sort.col}`)?.goodness;
        const vb = byKey.get(`${b}|${sort.col}`)?.goodness;
        // NaN always last, in both directions.
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return sort.desc ? vb - va : va - vb;
      });
    }
    const yOrder = [...rows].reverse();

    const label = (m: { key: string; label: string }) =>
      sort?.col === m.key ? `${m.label}  ${sort.desc ? "▼" : "▲"}` : m.label;

    const points = data.cells.map((c) => [
      metrics.findIndex((m) => m.key === c.metric),
      yOrder.indexOf(c.symbol),
      c.goodness,
    ]);

    const showLabels = rows.length * metrics.length <= 120;

    return {
      ...ECHARTS_BASE,
      tooltip: {
        ...ECHARTS_BASE.tooltip,
        formatter: (params: { data: [number, number, number | null] }) => {
          const [x, y] = params.data;
          const metric = metrics[x];
          const symbol = yOrder[y];
          const cell = byKey.get(`${symbol}|${metric?.key}`);
          if (!metric || !cell) return "";
          const value = cell.value === null ? "—" : `${cell.value}${metric.unit ?? ""}`;
          const good = cell.goodness === null ? "—" : cell.goodness.toFixed(0);
          return `<b>${symbol}</b><br/>${metric.label}: ${value}<br/>score ${good} / 100 · ${metric.verdict}`;
        },
      },
      grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
      xAxis: {
        type: "category",
        data: metrics.map(label),
        position: "top",
        // triggerEvent lets a label click drive the row sort.
        triggerEvent: true,
        splitArea: { show: true },
        axisLabel: {
          color: "#e6eaf2",
          fontSize: 11,
          // Horizontal, every column labelled. Long names wrap rather than
          // tilt — and rather than being auto-hidden, which would silently
          // drop a column's name.
          interval: 0,
          width: 78,
          overflow: "break",
          lineHeight: 13,
        },
        axisLine: { lineStyle: { color: "#232a3b" } },
      },
      yAxis: {
        type: "category",
        data: yOrder,
        splitArea: { show: true },
        axisLabel: { color: "#e6eaf2", fontSize: 11 },
        axisLine: { lineStyle: { color: "#232a3b" } },
      },
      visualMap: {
        min: 0,
        max: 100,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        textStyle: { color: "#8b93a7" },
        inRange: { color: data.ramp },
      },
      series: [
        {
          type: "heatmap",
          data: points,
          label: {
            show: showLabels,
            color: "#0b0e14",
            fontSize: 10,
            formatter: (p: { data: [number, number, number | null] }) =>
              p.data[2] === null ? "—" : String(Math.round(p.data[2])),
          },
          itemStyle: { borderColor: "#0b0e14", borderWidth: 1 },
          emphasis: { itemStyle: { borderColor: "#6ea8fe", borderWidth: 2 } },
        },
      ],
    };
  }, [data, sort]);

  // The click returns the DISPLAYED label, so map it back through the same strings.
  const onAxisClick = useMemo<[string, (p: ECElementEvent) => void]>(
    () => [
      "click",
      (params) => {
        const raw = (params as unknown as { targetType?: string; value?: string });
        if (raw.targetType !== "axisLabel" || typeof raw.value !== "string") return;
        const clean = raw.value.replace(/\s+[▼▲]$/, "");
        const metric = data?.metrics.find((m) => m.label === clean);
        if (!metric) return;
        setSort((prev) =>
          prev?.col === metric.key ? { col: metric.key, desc: !prev.desc } : { col: metric.key, desc: true },
        );
      },
    ],
    [data],
  );

  const title = kind === "scores" ? "Scores heat map" : "Metrics heat map";

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={title}
        caption={
          <>
            {data?.symbols.length ?? 0} symbols × {data?.metrics.length ?? 0} columns · blue is
            weak, orange is strong, ranked against the whole universe
            {data?.missing?.length ? ` · No analysis row for: ${data.missing.join(", ")}` : ""}
          </>
        }
      />
      <Panel
        title={sort ? `sorted by ${sort.col} ${sort.desc ? "▼" : "▲"}` : "click a column to sort"}
        className="min-h-0 flex-1"
        bodyClassName="flex min-h-0 flex-1 flex-col"
        actions={
          <ColumnPicker
            options={optionData?.options ?? []}
            selected={new Set(data?.metrics.map((m) => m.key) ?? [])}
            onToggle={(key) =>
              setPicked((prev) => {
                const current = prev.length ? prev : (data?.metrics.map((m) => m.key) ?? []);
                return current.includes(key)
                  ? current.filter((k) => k !== key)
                  : [...current, key];
              })
            }
            label="Columns"
          />
        }
      >
        {isLoading ? (
          <EmptyState title="Loading…" />
        ) : option ? (
          <EChart option={option} className="min-h-96 flex-1" onEvent={onAxisClick} />
        ) : (
          <EmptyState title="Nothing to plot" detail={data?.message ?? undefined} />
        )}
      </Panel>
    </div>
  );
}
