/**
 * Lines over reported periods (fundamentals growth, dividend yield).
 *
 * ECharts rather than Lightweight Charts because these series are sparse and
 * must BREAK where a period is missing — the server inserts a null at the gap
 * and `connectNulls: false` turns it into a real break instead of a line drawn
 * straight across a hole. A true time axis also aligns symbols on different
 * fiscal calendars by date.
 *
 * Markers are shown: reported periods are few and each one is worth seeing.
 */
import { useMemo } from "react";
import { EChart, ECHARTS_BASE, axisStyle } from "@/components/EChart";

export type PeriodLine = { name: string; points: { time: string; value: number | null }[] };

export function PeriodLineChart({
  lines,
  yLabel,
  baseline,
  className,
}: {
  lines: PeriodLine[];
  yLabel: string;
  /** Dashed reference level, e.g. 100 in normalized mode. */
  baseline?: number | null;
  className?: string;
}) {
  const option = useMemo(() => {
    const series: Record<string, unknown>[] = lines.map((line) => ({
      name: line.name,
      type: "line",
      showSymbol: true,
      symbolSize: 5,
      connectNulls: false,
      lineStyle: { width: 1.8 },
      emphasis: { focus: "series" },
      data: line.points.map((p) => [p.time, p.value]),
    }));

    if (baseline !== null && baseline !== undefined) {
      series.push({
        name: "_baseline",
        type: "line",
        data: [],
        silent: true,
        tooltip: { show: false },
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { type: "dashed", color: "rgba(230,230,230,0.5)", width: 1.5 },
          data: [{ yAxis: baseline }],
        },
      });
    }

    return {
      ...ECHARTS_BASE,
      tooltip: { ...ECHARTS_BASE.tooltip, trigger: "axis", order: "valueDesc" },
      legend: {
        type: "scroll",
        orient: "vertical",
        left: 8,
        top: "middle",
        icon: "roundRect",
        itemWidth: 14,
        itemHeight: 8,
        textStyle: { color: "#8b93a7", fontSize: 11 },
        inactiveColor: "#4a5163",
        // The baseline is always drawn and never toggleable.
        data: lines.map((l) => l.name),
      },
      grid: { left: 120, right: 24, top: 16, bottom: 56, containLabel: true },
      xAxis: axisStyle({ type: "time" }),
      yAxis: axisStyle({ type: "value", name: yLabel, scale: true }),
      dataZoom: [
        { type: "inside" },
        {
          type: "slider",
          bottom: 8,
          height: 30,
          borderColor: "rgba(255,255,255,0.18)",
          fillerColor: "rgba(110,168,254,0.18)",
          handleStyle: { color: "#6ea8fe" },
          moveHandleStyle: { color: "#6ea8fe" },
          textStyle: { color: "#8b93a7" },
        },
      ],
      series,
    };
  }, [lines, yLabel, baseline]);

  return <EChart option={option} className={className} />;
}
