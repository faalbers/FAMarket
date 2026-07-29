/**
 * Peak-detection tuner, embedded in Settings.
 *
 * Detection runs through the SAME `trend_signals` the analysis pipeline uses,
 * so what you see here is what a real run would find. Sample stocks are picked
 * by price BEHAVIOUR — clear trend, choppy, volatile, calm — so both knobs get
 * tested against the range of shapes they have to handle.
 *
 * Moving a slider only calls `setMarkers` on the existing chart, so the zoom
 * survives every adjustment.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  LineSeries,
  createSeriesMarkers,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { Save } from "lucide-react";
import { get, put } from "@/lib/api";
import { CHART_LAYOUT, SOLID, seriesColor } from "@/components/chartTheme";
import { Button, EmptyState, Input } from "@/components/ui";

type Sample = { symbol: string; name: string; tag: string };
type Point = { time: string; value: number };
type Signals = {
  symbol?: string;
  points: Point[];
  highs: Point[];
  lows: Point[];
  trend?: string;
  trend_label?: string;
  message: string | null;
};

const toTime = (iso: string): UTCTimestamp =>
  (Date.parse(`${iso}T00:00:00Z`) / 1000) as UTCTimestamp;

export function CalibrationTuner() {
  const [index, setIndex] = useState(0);
  const [override, setOverride] = useState("");
  const [prominence, setProminence] = useState(0.05);
  const [distance, setDistance] = useState(10);
  const [note, setNote] = useState<string | null>(null);

  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const subjectRef = useRef("");

  const { data: samples } = useQuery({
    queryKey: ["calibration-samples"],
    queryFn: () => get<{ samples: Sample[] }>("/calibration/samples"),
    staleTime: Infinity,
  });

  // Seed the sliders from the saved settings so the tuner opens where you left it.
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () =>
      get<{ sections: { fields: { path: string; value: number | boolean }[] }[] }>("/settings"),
    staleTime: 60_000,
  });

  useEffect(() => {
    const fields = settings?.sections.flatMap((s) => s.fields) ?? [];
    const saved = (path: string) => fields.find((f) => f.path === path)?.value;
    const p = saved("PEAK_PROMINENCE");
    const d = saved("PEAK_DISTANCE");
    if (typeof p === "number") setProminence(p);
    if (typeof d === "number") setDistance(d);
  }, [settings]);

  const list = samples?.samples ?? [];
  const current = override.trim().toUpperCase() || list[index]?.symbol || "";

  const { data } = useQuery({
    queryKey: ["calibration-signals", current, prominence, distance],
    queryFn: () =>
      get<Signals>("/calibration/signals", { symbol: current, prominence, distance }),
    enabled: Boolean(current),
    staleTime: 60_000,
  });

  const save = useMutation({
    mutationFn: () =>
      put<{ saved: number }>("/settings", {
        changes: { PEAK_PROMINENCE: prominence, PEAK_DISTANCE: distance },
      }),
    onSuccess: () => setNote("Calibration saved."),
    onError: (err: Error) => setNote(err.message),
  });

  // --- create the chart once -----------------------------------------------
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = createChart(host, CHART_LAYOUT);
    const series = chart.addSeries(LineSeries, {
      color: seriesColor(0),
      lineStyle: SOLID,
      lineWidth: 2,
      priceLineVisible: false,
    });
    // v5: markers are a plugin, not series.setMarkers().
    markersRef.current = createSeriesMarkers(series, []);
    chartRef.current = chart;
    seriesRef.current = series;

    return () => {
      markersRef.current?.detach();
      markersRef.current = null;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  const markers = useMemo<SeriesMarker<Time>[]>(() => {
    if (!data) return [];
    // Shape AND letter carry the meaning, so colour is never the only cue.
    const highs: SeriesMarker<Time>[] = data.highs.map((p) => ({
      time: toTime(p.time) as Time,
      position: "aboveBar",
      shape: "arrowDown",
      color: "#f0a202",
      text: "H",
    }));
    const lows: SeriesMarker<Time>[] = data.lows.map((p) => ({
      time: toTime(p.time) as Time,
      position: "belowBar",
      shape: "arrowUp",
      color: "#4a9eff",
      text: "L",
    }));
    return [...highs, ...lows].sort((a, b) => Number(a.time) - Number(b.time));
  }, [data]);

  // --- data + markers: mutate, never recreate ------------------------------
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !data) return;
    series.setData(data.points.map((p) => ({ time: toTime(p.time) as Time, value: p.value })));
    // Only a change of SYMBOL is a change of subject; slider moves must not refit.
    if (data.symbol && data.symbol !== subjectRef.current) {
      subjectRef.current = data.symbol;
      chartRef.current?.timeScale().fitContent();
    }
  }, [data]);

  useEffect(() => {
    markersRef.current?.setMarkers(markers);
  }, [markers]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>
          ◀ Prev
        </Button>
        <Button
          size="sm"
          disabled={index >= list.length - 1}
          onClick={() => setIndex((i) => i + 1)}
        >
          Next ▶
        </Button>
        <span className="text-[12px] text-ink">
          <span className="tnum font-semibold">{current}</span>{" "}
          <span className="text-dim">
            {list[index]?.name} · {list[index]?.tag}
          </span>
        </span>
        <div className="w-40">
          <Input
            value={override}
            onChange={(e) => setOverride(e.target.value)}
            placeholder="…or any symbol"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 text-[12px]">
        <label className="flex items-center gap-2">
          <span className="text-dim">Prominence</span>
          <input
            type="range"
            min={0.005}
            max={0.2}
            step={0.005}
            value={prominence}
            onChange={(e) => setProminence(Number(e.target.value))}
            className="w-40 accent-[#6ea8fe]"
          />
          <span className="tnum w-12 text-right text-ink">{prominence.toFixed(3)}</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-dim">Distance</span>
          <input
            type="range"
            min={5}
            max={60}
            step={1}
            value={distance}
            onChange={(e) => setDistance(Number(e.target.value))}
            className="w-40 accent-[#6ea8fe]"
          />
          <span className="tnum w-8 text-right text-ink">{distance}</span>
        </label>
        <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
          <Save size={12} /> Save calibration
        </Button>
      </div>

      <div className="text-[12px] text-ink">
        {data?.trend_label ?? "—"}{" "}
        <span className="text-dim">
          · {data?.highs.length ?? 0} swing highs, {data?.lows.length ?? 0} swing lows
        </span>
      </div>

      {/* relative parent + absolute child: this pair cannot collapse to a sliver */}
      <div className="relative min-h-80 flex-1">
        <div ref={hostRef} className="absolute inset-0" />
      </div>

      {data?.message && <EmptyState title="No data" detail={data.message} />}
      {note && <div className="text-[11px] text-dim">{note}</div>}
    </div>
  );
}
