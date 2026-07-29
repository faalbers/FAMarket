/**
 * /charts?view=radar — the five 0-100 category scores, one polygon per symbol.
 * Read straight from the analysis snapshot; nothing is recomputed here.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, type HintRegistry } from "@/lib/api";
import { EChart, ECHARTS_BASE } from "@/components/EChart";
import { EmptyState, PageHeader, Panel } from "@/components/ui";

type RadarResponse = {
  axes: { key: string; label: string; hint_key: string }[];
  rows: { name: string; values: (number | null)[] }[];
  missing?: string[];
  message: string | null;
};

export function RadarView({ symbols }: { symbols: string[] }) {
  const { data, isLoading } = useQuery({
    queryKey: ["chart-radar", symbols],
    queryFn: () => get<RadarResponse>("/charts/radar", { symbols: symbols.join(",") }),
    staleTime: 5 * 60_000,
  });

  const { data: hints } = useQuery({
    queryKey: ["hints"],
    queryFn: () => get<HintRegistry>("/hints"),
    staleTime: Infinity,
  });

  const option = useMemo(
    () => ({
      ...ECHARTS_BASE,
      tooltip: { ...ECHARTS_BASE.tooltip, trigger: "item" },
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
        data: (data?.rows ?? []).map((r) => r.name),
      },
      radar: {
        indicator: (data?.axes ?? []).map((a) => ({ name: a.label, max: 100 })),
        center: ["56%", "54%"],
        radius: "82%",
        axisName: { color: "#e6eaf2", fontSize: 11 },
        splitLine: { lineStyle: { color: "#232a3b" } },
        splitArea: { areaStyle: { color: ["rgba(255,255,255,0.02)", "transparent"] } },
        axisLine: { lineStyle: { color: "#232a3b" } },
      },
      series: [
        {
          type: "radar",
          // focus:self dims the others on hover, so overlapping polygons stay readable.
          emphasis: { focus: "self", lineStyle: { width: 3 } },
          blur: { areaStyle: { opacity: 0.02 }, lineStyle: { opacity: 0.15 } },
          areaStyle: { opacity: 0.08 },
          lineStyle: { width: 2 },
          symbolSize: 4,
          data: (data?.rows ?? []).map((r) => ({ name: r.name, value: r.values })),
        },
      ],
    }),
    [data],
  );

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Category scores"
        caption={
          data?.missing?.length
            ? `No analysis row for: ${data.missing.join(", ")}`
            : `${data?.rows.length ?? 0} symbols · each axis is a 0-100 score`
        }
      />

      {/* One cell per category with its canonical hint — the same registry the
          rest of the app reads. */}
      {data && data.axes.length > 0 && (
        <div className="grid grid-cols-5 gap-px border-b border-line bg-line">
          {data.axes.map((axis) => {
            const hint = hints?.[axis.hint_key];
            return (
              <div key={axis.key} className="bg-panel px-3 py-2">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">
                  {axis.label}
                </div>
                {hint && (
                  <div className="mt-1 line-clamp-3 text-[11px] text-dim/80">
                    {hint.what_it_is}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Panel className="min-h-0 flex-1" bodyClassName="flex min-h-0 flex-1 flex-col">
        {isLoading ? (
          <EmptyState title="Loading scores…" />
        ) : data && data.rows.length > 0 ? (
          <EChart option={option} className="min-h-96 flex-1" />
        ) : (
          <EmptyState title="No category scores" detail={data?.message ?? undefined} />
        )}
      </Panel>
    </div>
  );
}
