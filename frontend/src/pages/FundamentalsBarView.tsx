/**
 * /charts?view=fundamentals_bar — one symbol × one parameter, bars across its
 * reported periods.
 *
 * Ratios are computed server-side by the SAME functions the analysis snapshot
 * uses, so a chart can never disagree with a stored metric.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { EChart, ECHARTS_BASE, ECHARTS_COLORWAY, axisStyle } from "@/components/EChart";
import { ParamPicker, type ParamOption } from "@/components/ParamPicker";
import { Button, ButtonGroup, EmptyState, PageHeader, Panel } from "@/components/ui";

type BarResponse = {
  bars: { period: string; value: number | null }[];
  kind?: string;
  y_label?: string;
  message: string | null;
};

const FREQS = [
  { key: "annual", label: "Annual" },
  { key: "quarterly", label: "Quarterly" },
] as const;

export function FundamentalsBarView({ symbols, cols }: { symbols: string[]; cols: string[] }) {
  const [freq, setFreq] = useState<string>("annual");
  const [symbol, setSymbol] = useState(symbols[0] ?? "");

  const { data: optionData } = useQuery({
    queryKey: ["fundamentals-options"],
    queryFn: () => get<{ options: ParamOption[] }>("/charts/fundamentals/options"),
    staleTime: Infinity,
  });
  const options = optionData?.options ?? [];

  // Default to the first Output-shown column this view can actually plot.
  const initial = cols.find((c) => options.some((o) => o.key === c)) ?? options[0]?.key ?? "";
  const [param, setParam] = useState<string>("");
  const active = param || initial;

  const { data, isLoading } = useQuery({
    queryKey: ["fundamentals-bar", symbol, active, freq],
    queryFn: () =>
      get<BarResponse>("/charts/fundamentals/bar", { symbol, param: active, freq }),
    enabled: Boolean(symbol && active),
    staleTime: 5 * 60_000,
  });

  const option = useMemo(
    () => ({
      ...ECHARTS_BASE,
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      tooltip: { ...ECHARTS_BASE.tooltip, trigger: "axis" },
      xAxis: axisStyle({
        type: "category",
        data: (data?.bars ?? []).map((b) => b.period),
        splitLine: { show: false },
      }),
      yAxis: axisStyle({ type: "value", name: data?.y_label ?? "", scale: true }),
      series: [
        {
          type: "bar",
          data: (data?.bars ?? []).map((b) => b.value),
          itemStyle: { color: ECHARTS_COLORWAY[0] },
          barMaxWidth: 46,
        },
      ],
    }),
    [data],
  );

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Fundamentals over time"
        caption={`${symbol} · ${options.find((o) => o.key === active)?.label ?? active} · ${freq}`}
      />
      <Panel
        title={data?.y_label ?? ""}
        className="min-h-0 flex-1"
        bodyClassName="flex min-h-0 flex-1 flex-col"
        actions={
          <div className="flex items-center gap-2">
            <ButtonGroup>
              {FREQS.map((f) => (
                <Button
                  key={f.key}
                  size="sm"
                  variant="toggle"
                  active={freq === f.key}
                  onClick={() => setFreq(f.key)}
                >
                  {f.label}
                </Button>
              ))}
            </ButtonGroup>
            <ParamPicker options={options} value={active} onPick={setParam} />
          </div>
        }
      >
        {symbols.length > 1 && (
          <div className="flex flex-wrap gap-1 border-b border-line px-3 py-1.5">
            {symbols.map((s) => (
              <Button
                key={s}
                size="sm"
                variant="toggle"
                active={symbol === s}
                onClick={() => setSymbol(s)}
              >
                {s}
              </Button>
            ))}
          </div>
        )}

        {isLoading ? (
          <EmptyState title="Loading…" />
        ) : data && data.bars.length > 0 ? (
          <EChart option={option} className="min-h-72 flex-1" />
        ) : (
          <EmptyState title="Nothing to plot" detail={data?.message ?? undefined} />
        )}
      </Panel>
    </div>
  );
}
