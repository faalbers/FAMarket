/**
 * /charts?view=price — normalized adjusted close for the chosen symbols, with
 * the sector/industry relative-strength view layered on top.
 *
 * Period presets set the window that gets LOADED; arbitrary sub-ranges come
 * from the chart itself (wheel zoom, drag-pan, shift-drag marquee).
 */
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Maximize2, MousePointer2, MoveHorizontal, SquareDashed } from "lucide-react";
import { get } from "@/lib/api";
import { PriceChart, type ChartLine } from "@/components/PriceChart";
import { GroupSelect, type GroupTree } from "@/components/GroupSelect";
import type { ChartHandle, ChartMode } from "@/components/useZoomSelection";
import { Button, ButtonGroup, EmptyState, PageHeader, Panel } from "@/components/ui";

type PriceResponse = {
  lines: ChartLine[];
  missing: string[];
  tree: GroupTree;
  baseline: number;
  period: string;
  mode: string | null;
  group?: string;
  group_label?: string;
  y_label: string;
  message: string | null;
};

const PERIODS = ["1Y", "3Y", "5Y"] as const;
const MODES = [
  { key: "relative", label: "Relative" },
  { key: "symbols", label: "Symbols" },
  { key: "index", label: "Index" },
] as const;

export function PriceView({ symbols }: { symbols: string[] }) {
  const [period, setPeriod] = useState<string>("3Y");
  const [group, setGroup] = useState<string | null>(null);
  const [mode, setMode] = useState<string>("relative");
  const [chartMode, setChartMode] = useState<ChartMode>("pan");
  const chartRef = useRef<ChartHandle>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["chart-price", symbols, period, group, mode],
    queryFn: () =>
      get<PriceResponse>("/charts/price", {
        symbols: symbols.join(","),
        period,
        group: group ?? "",
        mode,
      }),
    staleTime: 5 * 60_000,
  });

  const headline = !group
    ? "Normalized adjusted close — every line indexed to 100 at the window start."
    : mode === "index"
      ? `${data?.group_label ?? ""} index — normalized to 100 at the window start.`
      : mode === "relative"
        ? `${data?.group_label ?? ""} — each symbol relative to its index (symbol − index + 100). Above 100 beats the group; the flat 100 line is the index.`
        : `${data?.group_label ?? ""} symbols — normalized to 100 at the window start, not relative to the index.`;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Normalized price"
        caption={
          <>
            {symbols.length} symbol{symbols.length === 1 ? "" : "s"} · {headline}
            {data?.missing.length ? ` · No price data for: ${data.missing.join(", ")}` : ""}
          </>
        }
      />

      <Panel
        title={data?.y_label ?? "Indexed (100)"}
        className="min-h-0 flex-1"
        bodyClassName="flex min-h-0 flex-1 flex-col"
        actions={
          <div className="flex items-center gap-2">
            <ButtonGroup>
              {PERIODS.map((p) => (
                <Button
                  key={p}
                  size="sm"
                  variant="toggle"
                  active={period === p}
                  onClick={() => setPeriod(p)}
                >
                  {p}
                </Button>
              ))}
            </ButtonGroup>

            <GroupSelect tree={data?.tree ?? {}} value={group} onChange={setGroup} />

            {group && (
              <ButtonGroup>
                {MODES.map((m) => (
                  <Button
                    key={m.key}
                    size="sm"
                    variant="toggle"
                    active={mode === m.key}
                    onClick={() => setMode(m.key)}
                  >
                    {m.label}
                  </Button>
                ))}
              </ButtonGroup>
            )}

            <ButtonGroup>
              <Button
                size="icon"
                variant="toggle"
                active={chartMode === "pan"}
                onClick={() => setChartMode("pan")}
                title="Pan and wheel-zoom (hold shift to select a time range)"
              >
                <MousePointer2 size={12} />
              </Button>
              <Button
                size="icon"
                variant="toggle"
                active={chartMode === "time"}
                onClick={() => setChartMode("time")}
                title="Drag to select a time range"
              >
                <MoveHorizontal size={12} />
              </Button>
              <Button
                size="icon"
                variant="toggle"
                active={chartMode === "box"}
                onClick={() => setChartMode("box")}
                title="Drag a box to zoom both axes"
              >
                <SquareDashed size={12} />
              </Button>
            </ButtonGroup>

            <Button size="icon" onClick={() => chartRef.current?.reset()} title="Reset zoom">
              <Maximize2 size={12} />
            </Button>
          </div>
        }
      >
        {isLoading ? (
          <EmptyState title="Loading prices…" />
        ) : error ? (
          <EmptyState title="Could not load prices" detail={String(error)} />
        ) : data && data.lines.length > 0 ? (
          <PriceChart
            ref={chartRef}
            lines={data.lines}
            baseline={data.baseline}
            // Changing any of these is a change of subject, so the chart refits;
            // anything else (a re-render, a refetch) preserves the zoom.
            subject={`${symbols.join(",")}|${period}|${group ?? ""}|${mode}`}
            mode={chartMode}
          />
        ) : (
          <EmptyState title="Nothing to plot" detail={data?.message ?? undefined} />
        )}
      </Panel>
    </div>
  );
}
