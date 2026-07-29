/**
 * Normalized price chart on Lightweight Charts.
 *
 * The rules that matter (all learned the hard way — see the FAUI stack guide):
 *  - the chart is created ONCE and mutated afterwards; series are added,
 *    updated and hidden in place. Recreating it on every render is what made
 *    the Streamlit version lose zoom on any interaction;
 *  - `fitContent()` runs only when the SUBJECT changes (a different symbol set
 *    or window), tracked with a key — never on every data update;
 *  - the host sits in a `relative` parent with an `absolute inset-0` child, so
 *    it can never collapse to a sliver inside a flex/overflow ancestor.
 *
 * Identity never depends on hue alone: every line is labelled DIRECTLY at its
 * right edge on the price scale, and the readout strip above names each line
 * beside its value. Lines are solid on purpose — dash patterns are a second cue
 * that works on sparse charts but reads as noise (or as phantom data gaps) at
 * ~750 daily points, so this chart uses labels instead. See chartTheme.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type MouseEventParams,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { CHART_LAYOUT, SOLID, seriesColor } from "@/components/chartTheme";
import {
  bandStyle,
  useZoomSelection,
  type ChartHandle,
  type ChartMode,
} from "@/components/useZoomSelection";

export type ChartLine = { name: string; points: { time: string; value: number }[] };

const toTime = (iso: string): UTCTimestamp =>
  (Date.parse(`${iso}T00:00:00Z`) / 1000) as UTCTimestamp;

type Track = {
  name: string;
  color: string;
  hidden: boolean;
  points: LineData[];
  last: number;
};

export function PriceChart({
  lines,
  baseline = 100,
  subject,
  mode = "pan",
  ref,
}: {
  lines: ChartLine[];
  /** Dashed reference level — 100 on every base-100 view. */
  baseline?: number | null;
  /** Changes when the plotted subject changes, triggering a refit. */
  subject: string;
  mode?: ChartMode;
  ref?: React.Ref<ChartHandle>;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRefs = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const baselineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const anchorRef = useRef<ISeriesApi<"Line"> | null>(null);
  const tracksRef = useRef<Track[]>([]);
  const lastSubjectRef = useRef<string>("");

  const [hover, setHover] = useState<{ date: string; rows: Map<string, number> } | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());

  const zoom = useZoomSelection(
    chartRef,
    anchorRef as React.RefObject<ISeriesApi<SeriesType> | null>,
    mode,
  );

  const tracks = useMemo<Track[]>(
    () =>
      lines.map((line, i) => {
        const points = line.points.map(
          (p): LineData => ({ time: toTime(p.time) as Time, value: p.value }),
        );
        return {
          name: line.name,
          color: seriesColor(i),
          hidden: hidden.has(line.name),
          points,
          last: points.at(-1)?.value ?? baseline ?? 100,
        };
      }),
    [lines, hidden, baseline],
  );
  tracksRef.current = tracks;

  const toggle = (name: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (!next.delete(name)) next.add(name);
      return next;
    });

  /** Alt-click isolates one line; alt-click again brings everyone back. */
  const solo = (name: string) =>
    setHidden((prev) => {
      const others = tracksRef.current.filter((t) => t.name !== name).map((t) => t.name);
      const isSolo = prev.size === others.length && others.every((n) => prev.has(n));
      return isSolo ? new Set() : new Set(others);
    });

  useEffect(() => {
    if (!ref) return;
    const handle: ChartHandle = { reset: zoom.reset };
    if (typeof ref === "function") ref(handle);
    else ref.current = handle;
  }, [ref, zoom.reset]);

  // --- create once ---------------------------------------------------------
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const chart = createChart(host, {
      ...CHART_LAYOUT,
      rightPriceScale: { borderColor: "#232a3b", scaleMargins: { top: 0.12, bottom: 0.12 } },
    });

    const onMove = (param: MouseEventParams) => {
      if (!param.time || !param.point) {
        setHover(null);
        return;
      }
      const rows = new Map<string, number>();
      for (const track of tracksRef.current) {
        if (track.hidden) continue;
        const api = seriesRefs.current.get(track.name);
        const point = api ? param.seriesData.get(api) : undefined;
        if (point && "value" in point) rows.set(track.name, point.value as number);
      }
      const seconds = param.time as UTCTimestamp;
      setHover(
        rows.size
          ? { date: new Date(seconds * 1000).toISOString().slice(0, 10), rows }
          : null,
      );
    };

    chart.subscribeCrosshairMove(onMove);
    chartRef.current = chart;

    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      seriesRefs.current.clear();
      baselineRef.current = null;
      anchorRef.current = null;
    };
  }, []);

  // --- series sync ---------------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const wanted = tracks.map((t) => t.name);

    for (const [name, api] of seriesRefs.current) {
      if (!wanted.includes(name)) {
        chart.removeSeries(api);
        seriesRefs.current.delete(name);
      }
    }

    for (const track of tracks) {
      let api = seriesRefs.current.get(track.name);
      if (!api) {
        api = chart.addSeries(LineSeries, {
          color: track.color,
          lineStyle: SOLID,
          lineWidth: 2,
          title: track.name, // direct label on the price scale — beats a legend
          priceLineVisible: false,
          lastValueVisible: true,
        });
        seriesRefs.current.set(track.name, api);
      }
      api.applyOptions({ color: track.color, visible: !track.hidden });
      api.setData(track.points);
    }

    anchorRef.current = seriesRefs.current.get(wanted[0] ?? "") ?? null;

    // The flat reference level, drawn as its own faint series across the same
    // span so it is always visible without being toggleable.
    if (baseline !== null && tracks.length > 0) {
      const spanned = tracks.flatMap((t) => t.points);
      const first = spanned.reduce<Time | null>(
        (acc, p) => (acc === null || p.time < acc ? p.time : acc),
        null,
      );
      const last = spanned.reduce<Time | null>(
        (acc, p) => (acc === null || p.time > acc ? p.time : acc),
        null,
      );
      if (first !== null && last !== null) {
        if (!baselineRef.current) {
          baselineRef.current = chart.addSeries(LineSeries, {
            color: "rgba(230,230,230,0.45)",
            lineWidth: 1,
            lineStyle: 2, // dashed
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
        }
        baselineRef.current.setData([
          { time: first, value: baseline },
          { time: last, value: baseline },
        ]);
      }
    } else if (baselineRef.current) {
      chart.removeSeries(baselineRef.current);
      baselineRef.current = null;
    }

    // Refit ONLY when the subject changes, so a zoom survives a data refresh.
    if (subject !== lastSubjectRef.current) {
      lastSubjectRef.current = subject;
      anchorRef.current?.priceScale().setAutoScale(true);
      chart.timeScale().fitContent();
    }
  }, [tracks, baseline, subject]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-3 py-1.5">
        <span className="tnum w-[76px] shrink-0 text-[11px] text-dim">
          {hover?.date ?? "latest"}
        </span>
        {tracks.map((track) => {
          const value = hover?.rows.get(track.name) ?? track.last;
          const change = value - (baseline ?? 100);
          const up = change >= 0;
          return (
            <button
              key={track.name}
              onClick={(e) => (e.altKey ? solo(track.name) : toggle(track.name))}
              aria-pressed={!track.hidden}
              title={
                track.hidden
                  ? `Show ${track.name}`
                  : `Hide ${track.name} — alt-click to isolate it`
              }
              className={`flex items-center gap-1.5 rounded px-1 py-0.5 transition-opacity hover:bg-panel2 ${
                track.hidden ? "opacity-40" : ""
              }`}
            >
              {/* Solid swatch matching the line exactly, so the strip is a
                  reliable key back to the chart. */}
              <svg width="18" height="8" aria-hidden>
                <line
                  x1="0"
                  y1="4"
                  x2="18"
                  y2="4"
                  stroke={track.hidden ? "#8b93a7" : track.color}
                  strokeWidth="2"
                />
              </svg>
              <span
                className={`tnum text-[11px] font-semibold ${
                  track.hidden ? "text-dim line-through" : "text-ink"
                }`}
              >
                {track.name}
              </span>
              {!track.hidden && (
                <span className={`tnum text-[11px] ${up ? "text-up" : "text-down"}`}>
                  {up ? "▲" : "▼"} {up ? "+" : ""}
                  {change.toFixed(1)}
                </span>
              )}
            </button>
          );
        })}
        {hidden.size > 0 && (
          <button
            onClick={() => setHidden(new Set())}
            className="rounded px-1.5 py-0.5 text-[11px] text-accent hover:bg-panel2"
          >
            show all ({hidden.size} hidden)
          </button>
        )}
      </div>

      {/* relative parent + absolute child: this pair cannot collapse to a sliver */}
      <div
        ref={zoom.wrapRef}
        onPointerDown={zoom.onPointerDown}
        className={`relative min-h-64 flex-1 ${zoom.cursor}`}
      >
        <div ref={hostRef} className="absolute inset-0" />
        {zoom.band && <div style={bandStyle(zoom.band, zoom.activeMode)} />}
      </div>
    </div>
  );
}
