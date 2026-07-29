/**
 * /charts?view=fundamentals_line and ?view=dividend_line.
 *
 * The two views share every control (period, Actual/Normalized) and differ only
 * in the endpoint and whether a parameter is picked, so they share one page.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { PeriodLineChart, type PeriodLine } from "@/components/PeriodLineChart";
import { ParamPicker, type ParamOption } from "@/components/ParamPicker";
import { Button, ButtonGroup, EmptyState, PageHeader, Panel } from "@/components/ui";

type LineResponse = {
  lines: PeriodLine[];
  no_data: string[];
  skipped?: string[];
  baseline: number | null;
  y_label: string;
  cut_from?: string | null;
  message: string | null;
};

const FREQS = [
  { key: "annual", label: "Annual" },
  { key: "quarterly", label: "Quarterly" },
] as const;

export function PeriodLineView({
  symbols,
  cols,
  kind,
}: {
  symbols: string[];
  cols: string[];
  kind: "fundamentals" | "dividends";
}) {
  const [freq, setFreq] = useState<string>("annual");
  const [normalized, setNormalized] = useState(false);
  const [param, setParam] = useState<string>("");

  const { data: optionData } = useQuery({
    queryKey: ["fundamentals-options"],
    queryFn: () => get<{ options: ParamOption[] }>("/charts/fundamentals/options"),
    enabled: kind === "fundamentals",
    staleTime: Infinity,
  });
  const options = optionData?.options ?? [];
  const initial = cols.find((c) => options.some((o) => o.key === c)) ?? options[0]?.key ?? "";
  const active = param || initial;

  const { data, isLoading } = useQuery({
    queryKey: ["period-line", kind, symbols, active, freq, normalized],
    queryFn: () =>
      kind === "fundamentals"
        ? get<LineResponse>("/charts/fundamentals/line", {
            symbols: symbols.join(","),
            param: active,
            freq,
            normalized: String(normalized),
          })
        : get<LineResponse>("/charts/dividends", {
            symbols: symbols.join(","),
            freq,
            normalized: String(normalized),
          }),
    enabled: kind === "dividends" || Boolean(active),
    staleTime: 5 * 60_000,
  });

  const title = kind === "fundamentals" ? "Fundamentals growth" : "Dividend yield over time";
  const notes = [
    data?.no_data?.length ? `No data for: ${data.no_data.join(", ")}` : "",
    data?.skipped?.length
      ? `Can't normalize (first value ≤ 0) — switch to Actual to see: ${data.skipped.join(", ")}`
      : "",
    data?.cut_from ? `From ${data.cut_from} (after the last reporting gap)` : "",
  ].filter(Boolean);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={title}
        caption={
          <>
            {symbols.length} symbol{symbols.length === 1 ? "" : "s"} · {freq} periods ·{" "}
            {normalized ? "normalized to 100 at the shared start" : "actual reported values"}
          </>
        }
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
            <ButtonGroup>
              <Button
                size="sm"
                variant="toggle"
                active={!normalized}
                onClick={() => setNormalized(false)}
              >
                Actual
              </Button>
              <Button
                size="sm"
                variant="toggle"
                active={normalized}
                onClick={() => setNormalized(true)}
              >
                Normalized
              </Button>
            </ButtonGroup>
            {kind === "fundamentals" && (
              <ParamPicker options={options} value={active} onPick={setParam} />
            )}
          </div>
        }
      >
        {notes.length > 0 && (
          <div className="border-b border-line px-3 py-1 text-[11px] text-dim">
            {notes.join(" · ")}
          </div>
        )}

        {isLoading ? (
          <EmptyState title="Loading…" />
        ) : data && data.lines.length > 0 ? (
          <PeriodLineChart
            lines={data.lines}
            yLabel={data.y_label}
            baseline={data.baseline}
            className="min-h-72 flex-1"
          />
        ) : (
          <EmptyState title="Nothing to plot" detail={data?.message ?? undefined} />
        )}
      </Panel>
    </div>
  );
}
