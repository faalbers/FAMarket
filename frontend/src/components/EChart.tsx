/**
 * Thin ECharts host: init once, mutate via setOption, resize via ResizeObserver,
 * dispose on unmount. No echarts-for-react wrapper needed.
 *
 * ECharts draws the chart shapes Lightweight Charts can't: bars, radar and the
 * heat map. Price and other dense time series stay on Lightweight Charts.
 */
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, RadarChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ECElementEvent, EChartsCoreOption } from "echarts/core";

// Tree-shaken build: every chart type and component must be registered
// explicitly or setOption fails at runtime — silently, for some components.
echarts.use([
  BarChart,
  LineChart,
  RadarChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  VisualMapComponent,
  DataZoomComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

/** Okabe-Ito, same order as the Lightweight Charts palette. */
export const ECHARTS_COLORWAY = [
  "#56B4E9",
  "#E69F00",
  "#0072B2",
  "#CC79A7",
  "#009E73",
  "#F0E442",
  "#B478F0",
  "#D55E00",
];

const TEXT = "#8b93a7";
const GRID_LINE = "#1a2030";

/** Shared dark-theme defaults matching the app tokens. */
export const ECHARTS_BASE = {
  backgroundColor: "transparent",
  color: ECHARTS_COLORWAY,
  textStyle: {
    color: TEXT,
    fontFamily: "ui-monospace, Consolas, monospace",
    fontSize: 11,
  },
  tooltip: {
    backgroundColor: "rgba(22,27,40,0.92)",
    borderColor: "#232a3b",
    textStyle: { color: "#e6eaf2", fontSize: 12 },
  },
} as const;

/** Axis styling reused by every cartesian chart here. */
export const axisStyle = (extra: Record<string, unknown> = {}) => ({
  axisLine: { lineStyle: { color: "rgba(255,255,255,0.3)" } },
  axisLabel: { color: TEXT },
  splitLine: { show: true, lineStyle: { color: GRID_LINE } },
  ...extra,
});

export function EChart({
  option,
  className,
  onEvent,
}: {
  option: EChartsCoreOption;
  className?: string;
  /** `[eventName, handler]` — e.g. an x-axis label click for sorting. */
  onEvent?: [string, (params: ECElementEvent) => void];
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = echarts.init(host);
    chartRef.current = chart;

    const dispatch = (params: unknown) => handlerRef.current?.[1](params as ECElementEvent);
    const name = onEvent?.[0];
    if (name) chart.on(name, dispatch);

    // ECharts does not observe its container; resize must be driven externally.
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host);

    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // Only the event NAME matters for wiring; the handler is read via a ref.
  }, [onEvent?.[0]]);

  useEffect(() => {
    // notMerge replaces the whole option, which is predictable when the option
    // object is rebuilt from scratch on every change.
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return <div ref={hostRef} className={className} />;
}
