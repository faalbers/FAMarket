import { useCallback, useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, Logical, SeriesType } from "lightweight-charts";

export type ChartMode = "pan" | "time" | "box";
export type ChartHandle = { reset: () => void };

const MIN_DRAG_PX = 6;

type Point = { x: number; y: number };
type Band = { x1: number; y1: number; x2: number; y2: number };

/**
 * Marquee zoom for Lightweight Charts, which ships wheel zoom and drag-pan but
 * no selection.
 *
 * Coordinates are taken straight from the DOM relative to the chart host. With
 * the left price scale hidden (the default) pane 0 starts at the host's top-left
 * corner, so host-relative pixels are exactly what coordinateToLogical and
 * coordinateToPrice expect — no offset arithmetic, and no dependency on the
 * crosshair firing. The y is clamped to pane 0's height so dragging down into
 * the volume pane can't produce a nonsense price.
 */
export function useZoomSelection(
  chartRef: React.RefObject<IChartApi | null>,
  priceSeriesRef: React.RefObject<ISeriesApi<SeriesType> | null>,
  mode: ChartMode,
) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<Point | null>(null);
  const [band, setBand] = useState<Band | null>(null);
  const [shift, setShift] = useState(false);

  // Shift is a shortcut for time selection without leaving pan mode.
  const activeMode: ChartMode = mode !== "pan" ? mode : shift ? "time" : "pan";

  const localPoint = useCallback((clientX: number, clientY: number): Point | null => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return null;
    return { x: clientX - rect.left, y: clientY - rect.top };
  }, []);

  const reset = useCallback(() => {
    chartRef.current?.timeScale().fitContent();
    priceSeriesRef.current?.priceScale().setAutoScale(true);
  }, [chartRef, priceSeriesRef]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => e.key === "Shift" && setShift(true);
    const up = (e: KeyboardEvent) => e.key === "Shift" && setShift(false);
    const blur = () => setShift(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // Built-in drag-pan stands down while a selection drag is possible.
  useEffect(() => {
    const zooming = activeMode !== "pan";
    chartRef.current?.applyOptions({
      handleScroll: !zooming,
      handleScale: zooming ? { axisPressedMouseMove: false, mouseWheel: true, pinch: true } : true,
    });
  }, [activeMode, chartRef]);

  const commit = useCallback(
    (end: Point | null) => {
      const chart = chartRef.current;
      const series = priceSeriesRef.current;
      const start = dragRef.current;
      dragRef.current = null;
      setBand(null);
      if (!chart || !series || !start || !end) return;

      if (Math.abs(end.x - start.x) < MIN_DRAG_PX) return;

      const ts = chart.timeScale();
      const from = ts.coordinateToLogical(Math.min(start.x, end.x));
      const to = ts.coordinateToLogical(Math.max(start.x, end.x));
      if (from === null || to === null || to - from < 1) return;
      ts.setVisibleLogicalRange({ from: from as Logical, to: to as Logical });

      if (activeMode === "box" && Math.abs(end.y - start.y) >= MIN_DRAG_PX) {
        const paneHeight = chart.panes()[0]?.getHeight() ?? Infinity;
        const clamp = (v: number) => Math.max(0, Math.min(paneHeight, v));
        const high = series.coordinateToPrice(clamp(Math.min(start.y, end.y)));
        const low = series.coordinateToPrice(clamp(Math.max(start.y, end.y)));
        if (high !== null && low !== null && high > low) {
          series.priceScale().setAutoScale(false);
          series.priceScale().setVisibleRange({ from: low, to: high });
          return;
        }
      }
      // Time-only selection: let price refit to whatever is now visible.
      series.priceScale().setAutoScale(true);
    },
    [activeMode, chartRef, priceSeriesRef],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (activeMode === "pan" || e.button !== 0) return;
      const p = localPoint(e.clientX, e.clientY);
      if (!p) return;
      dragRef.current = p;
      setBand({ x1: p.x, y1: p.y, x2: p.x, y2: p.y });
      e.preventDefault();
    },
    [activeMode, localPoint],
  );

  useEffect(() => {
    if (!band) return;

    const move = (e: PointerEvent) => {
      const start = dragRef.current;
      const p = localPoint(e.clientX, e.clientY);
      if (!start || !p) return;
      setBand({ x1: start.x, y1: start.y, x2: p.x, y2: p.y });
    };
    const up = (e: PointerEvent) => commit(localPoint(e.clientX, e.clientY));
    const cancel = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      dragRef.current = null;
      setBand(null);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", cancel);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", cancel);
    };
  }, [band, commit, localPoint]);

  const cursor =
    activeMode === "pan" ? "" : activeMode === "time" ? "cursor-ew-resize" : "cursor-crosshair";

  return { wrapRef, reset, onPointerDown, band, activeMode, cursor };
}

/**
 * Shared rubber-band element. Full height for time selection, a box otherwise.
 *
 * Styling is inline rather than Tailwind classes because Lightweight Charts'
 * canvases sit in their own stacking context inside the host element; the band
 * needs an explicit z-index above them or it renders but stays invisible.
 */
export function bandStyle(band: Band, activeMode: ChartMode): React.CSSProperties {
  const box = activeMode === "box";
  return {
    position: "absolute",
    zIndex: 30,
    pointerEvents: "none",
    left: Math.min(band.x1, band.x2),
    width: Math.abs(band.x2 - band.x1),
    top: box ? Math.min(band.y1, band.y2) : 0,
    height: box ? Math.abs(band.y2 - band.y1) : "100%",
    background: "rgba(110, 168, 254, 0.16)",
    border: "1px solid rgba(110, 168, 254, 0.9)",
    borderTopWidth: box ? 1 : 0,
    borderBottomWidth: box ? 1 : 0,
    boxShadow: "0 0 0 1px rgba(11, 14, 20, 0.5)",
  };
}
