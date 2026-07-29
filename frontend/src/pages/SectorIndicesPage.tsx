/**
 * /sector-indices — sector index levels, or one sector's industries.
 *
 * The series come from `indices.db`, which is written on FULL analysis runs
 * only; a subset run leaves it untouched, so an empty page here means no full
 * run has happened yet rather than a failure.
 *
 * Lines are ordered strongest-first at the right edge, so the readout strip
 * reads like a leaderboard.
 */
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Maximize2 } from "lucide-react";
import { get } from "@/lib/api";
import { PriceChart, type ChartLine } from "@/components/PriceChart";
import type { ChartHandle } from "@/components/useZoomSelection";
import { Button, ButtonGroup, EmptyState, PageHeader, Panel } from "@/components/ui";

type IndexResponse = {
  lines: ChartLine[];
  sectors: string[];
  sector?: string;
  y_label?: string;
  baseline?: number;
  message: string | null;
};

const PERIODS = ["1Y", "3Y", "5Y", "Max"] as const;

export function SectorIndicesPage() {
  const [period, setPeriod] = useState<string>("3Y");
  const [view, setView] = useState<"sectors" | "industries">("sectors");
  const [sector, setSector] = useState("");
  const [mode, setMode] = useState<"absolute" | "relative">("absolute");
  const chartRef = useRef<ChartHandle>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["indices", view, period, sector, mode],
    queryFn: () =>
      get<IndexResponse>("/indices/series", { view, period, sector, mode }),
    staleTime: 5 * 60_000,
  });

  const activeSector = data?.sector ?? sector;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Sector indices"
        caption={
          view === "sectors"
            ? `${data?.lines.length ?? 0} sectors · rebased to 100 at the window start`
            : `${activeSector} · ${data?.lines.length ?? 0} industries · ${
                mode === "relative"
                  ? "each relative to its sector (industry − sector + 100)"
                  : "rebased to 100 at the window start"
              }`
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

            <ButtonGroup>
              <Button
                size="sm"
                variant="toggle"
                active={view === "sectors"}
                onClick={() => setView("sectors")}
              >
                Sectors
              </Button>
              <Button
                size="sm"
                variant="toggle"
                active={view === "industries"}
                onClick={() => setView("industries")}
              >
                Industries
              </Button>
            </ButtonGroup>

            {view === "industries" && (
              <>
                <select
                  value={activeSector}
                  onChange={(e) => setSector(e.target.value)}
                  className="rounded-md border border-line bg-panel2 px-2 py-1.5 text-[12px] text-ink focus:border-accent/60 focus:outline-none"
                  title="Sectors are listed strongest-first over the window"
                >
                  {(data?.sectors ?? []).map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <ButtonGroup>
                  <Button
                    size="sm"
                    variant="toggle"
                    active={mode === "absolute"}
                    onClick={() => setMode("absolute")}
                  >
                    Absolute
                  </Button>
                  <Button
                    size="sm"
                    variant="toggle"
                    active={mode === "relative"}
                    onClick={() => setMode("relative")}
                  >
                    Relative
                  </Button>
                </ButtonGroup>
              </>
            )}

            <Button size="icon" onClick={() => chartRef.current?.reset()} title="Reset zoom">
              <Maximize2 size={12} />
            </Button>
          </div>
        }
      >
        {isLoading ? (
          <EmptyState title="Loading indices…" />
        ) : data && data.lines.length > 0 ? (
          <PriceChart
            ref={chartRef}
            lines={data.lines}
            baseline={data.baseline ?? 100}
            subject={`${view}|${period}|${activeSector}|${mode}`}
          />
        ) : (
          <EmptyState
            title="No index data"
            detail={data?.message ?? "Indices are built on full analysis runs only."}
          />
        )}
      </Panel>
    </div>
  );
}
