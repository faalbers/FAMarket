/**
 * /scoring-rules — edit how each metric turns into a 0-100 goodness.
 *
 * A rule is a SHAPE (higher/lower is better, or a sweet spot) plus an ANCHOR
 * (peer, universe or an absolute line). Category scores are deliberately absent:
 * they are results derived from rule goodness, not rules themselves.
 *
 * Saving also refreshes the stored goodness/score columns in analysis.db (a few
 * seconds, no fetch), so Filter and Output agree with the preview immediately.
 *
 * The "Why this rule" box explains why each default is shaped the way it is —
 * composed server-side by config/rule_hints.py and returned on the preview
 * response, so its "Current rule" line reflects UNSAVED edits (preview is
 * re-requested whenever the draft rule changes).
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lightbulb, Save } from "lucide-react";
import { get, post, put } from "@/lib/api";
import { EChart, ECHARTS_BASE, axisStyle } from "@/components/EChart";
import { Button, ButtonGroup, EmptyState, Input, PageHeader, Panel, cn } from "@/components/ui";
import { Markdown } from "@/components/Markdown";

type Rule = {
  shape?: "higher_better" | "lower_better" | "sweet_spot";
  anchor?: "peer" | "universe" | "absolute";
  value?: number | null;
  lo?: number | null;
  hi?: number | null;
  positive_only?: boolean;
};

type MetricInfo = { key: string; label: string; category: string; unit: string };
type Overview = MetricInfo & { shape?: string; anchor?: string; band: string; customised: boolean };
type RulesResponse = {
  metrics: MetricInfo[];
  rules: Record<string, Rule>;
  defaults: Record<string, Rule>;
  overview: Overview[];
};

type Preview = {
  bins: { center: number; count: number; goodness: number | null; color: string }[];
  label?: string;
  unit?: string;
  sweet_spot?: [number | null, number | null] | null;
  line?: number | null;
  hint?: string;
  message: string | null;
};

const SHAPES = [
  { key: "higher_better", label: "Higher is better" },
  { key: "lower_better", label: "Lower is better" },
  { key: "sweet_spot", label: "Sweet spot" },
] as const;

const ANCHORS = [
  { key: "peer", label: "Peer" },
  { key: "universe", label: "Universe" },
  { key: "absolute", label: "Absolute" },
] as const;

export function ScoringRulesPage() {
  const queryClient = useQueryClient();
  const [metric, setMetric] = useState<string>("");
  const [draft, setDraft] = useState<Record<string, Rule>>({});
  const [note, setNote] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["scoring-rules"],
    queryFn: () => get<RulesResponse>("/scoring/rules"),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (data && !metric) setMetric(data.metrics[0]?.key ?? "");
    if (data && Object.keys(draft).length === 0) setDraft(data.rules);
  }, [data, metric, draft]);

  const rule: Rule = draft[metric] ?? data?.rules[metric] ?? {};
  const info = data?.metrics.find((m) => m.key === metric);
  const isDefault =
    data && JSON.stringify(rule) === JSON.stringify(data.defaults[metric] ?? {});

  const { data: preview } = useQuery({
    queryKey: ["scoring-preview", metric, rule],
    queryFn: () => post<Preview>("/scoring/preview", { metric, rule }),
    enabled: Boolean(metric),
    staleTime: 60_000,
  });

  const suggest = useMutation({
    mutationFn: () => get<{ rule: Rule }>("/scoring/suggest", { metric }),
    onSuccess: (res) => {
      setDraft((prev) => ({ ...prev, [metric]: res.rule }));
      setNote(`Suggested a rule for ${info?.label} from its distribution.`);
    },
  });

  const save = useMutation({
    mutationFn: () => put<{ saved: boolean; refreshed: { symbols?: number } | null }>(
      "/scoring/rules",
      { rules: draft, refresh: true },
    ),
    onSuccess: (res) => {
      setNote(
        res.refreshed?.symbols
          ? `Saved — scores refreshed for ${res.refreshed.symbols.toLocaleString()} symbols.`
          : "Saved.",
      );
      void queryClient.invalidateQueries({ queryKey: ["scoring-rules"] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  const set = (patch: Rule) => setDraft((prev) => ({ ...prev, [metric]: { ...rule, ...patch } }));

  const option = useMemo(() => {
    const bins = preview?.bins ?? [];
    const centers = bins.map((b) => b.center);
    const nearest = (value: number) =>
      centers.reduce(
        (best, c, i) => (Math.abs(c - value) < Math.abs(centers[best]! - value) ? i : best),
        0,
      );

    const mark: Record<string, unknown> = {};
    if (preview?.sweet_spot && preview.sweet_spot[0] != null && preview.sweet_spot[1] != null) {
      mark.markArea = {
        silent: true,
        itemStyle: { color: "rgba(224,123,26,0.10)" },
        data: [[{ xAxis: nearest(preview.sweet_spot[0]) }, { xAxis: nearest(preview.sweet_spot[1]) }]],
      };
    } else if (preview?.line != null) {
      mark.markLine = {
        silent: true,
        symbol: "none",
        label: { show: false },
        lineStyle: { type: "dashed", color: "rgba(230,230,230,0.6)" },
        data: [{ xAxis: nearest(preview.line) }],
      };
    }

    return {
      ...ECHARTS_BASE,
      tooltip: { ...ECHARTS_BASE.tooltip, trigger: "axis" },
      grid: { left: 8, right: 18, top: 16, bottom: 32, containLabel: true },
      xAxis: axisStyle({
        type: "category",
        data: centers.map((c) => String(c)),
        name: preview?.label ?? "",
        splitLine: { show: false },
        // ~12 labels however many bars there are, so they stay readable.
        axisLabel: { color: "#8b93a7", interval: Math.max(1, Math.floor(centers.length / 12)) },
      }),
      yAxis: axisStyle({ type: "value", name: "Stocks" }),
      series: [
        {
          type: "bar",
          barCategoryGap: "8%",
          data: bins.map((b) => ({ value: b.count, itemStyle: { color: b.color } })),
          ...mark,
        },
      ],
    };
  }, [preview]);

  if (isLoading) return <EmptyState title="Loading rules…" />;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Scoring rules"
        caption={
          <>
            {data?.metrics.length ?? 0} metrics ·{" "}
            {data?.overview.filter((o) => o.customised).length ?? 0} customised · orange is
            strong, blue is weak
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            <Button onClick={() => suggest.mutate()} loading={suggest.isPending}>
              <Lightbulb size={12} /> Suggest from data
            </Button>
            <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
              <Save size={12} /> Save &amp; refresh scores
            </Button>
          </div>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-[260px_1fr] gap-px bg-line">
        <Panel title="Metric">
          <div className="flex flex-col p-1">
            {(data?.overview ?? []).map((entry) => (
              <button
                key={entry.key}
                onClick={() => setMetric(entry.key)}
                className={cn(
                  "flex items-baseline gap-2 rounded px-2 py-1 text-left text-[12px]",
                  entry.key === metric ? "bg-accent/15 text-accent" : "text-ink hover:bg-panel2",
                )}
              >
                <span className="flex-1 truncate">{entry.label}</span>
                {entry.customised && <span className="text-[10px] text-down">edited</span>}
                <span className="text-[10px] text-dim">{entry.category}</span>
              </button>
            ))}
          </div>
        </Panel>

        <div className="flex min-h-0 flex-col gap-px bg-line">
          <Panel title={`${info?.label ?? metric}${isDefault ? " · default" : " · customised"}`}>
            <div className="flex flex-wrap items-center gap-3 p-3 text-[12px]">
              <div className="flex items-center gap-2">
                <span className="text-dim">Shape</span>
                <ButtonGroup>
                  {SHAPES.map((s) => (
                    <Button
                      key={s.key}
                      size="sm"
                      variant="toggle"
                      active={rule.shape === s.key}
                      onClick={() =>
                        set(
                          s.key === "sweet_spot"
                            ? // A sweet spot is a fixed band, so the anchor is forced.
                              { shape: s.key, anchor: "absolute" }
                            : { shape: s.key },
                        )
                      }
                    >
                      {s.label}
                    </Button>
                  ))}
                </ButtonGroup>
              </div>

              {rule.shape === "sweet_spot" ? (
                <div className="flex items-center gap-2">
                  <span className="text-dim">Ideal</span>
                  <div className="w-24">
                    <Input
                      value={rule.lo ?? ""}
                      onChange={(e) => set({ lo: Number(e.target.value) })}
                      placeholder="low"
                    />
                  </div>
                  <span className="text-dim">to</span>
                  <div className="w-24">
                    <Input
                      value={rule.hi ?? ""}
                      onChange={(e) => set({ hi: Number(e.target.value) })}
                      placeholder="high"
                    />
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-dim">Anchor</span>
                    <ButtonGroup>
                      {ANCHORS.map((a) => (
                        <Button
                          key={a.key}
                          size="sm"
                          variant="toggle"
                          active={rule.anchor === a.key}
                          onClick={() => set({ anchor: a.key })}
                        >
                          {a.label}
                        </Button>
                      ))}
                    </ButtonGroup>
                  </div>
                  {rule.anchor === "absolute" && (
                    <div className="flex items-center gap-2">
                      <span className="text-dim">Line</span>
                      <div className="w-24">
                        <Input
                          value={rule.value ?? ""}
                          onChange={(e) => set({ value: Number(e.target.value) })}
                        />
                      </div>
                    </div>
                  )}
                </>
              )}

              <label className="flex items-center gap-2 text-dim">
                <input
                  type="checkbox"
                  checked={Boolean(rule.positive_only)}
                  onChange={(e) => set({ positive_only: e.target.checked })}
                  className="size-3.5 accent-[#6ea8fe]"
                />
                Ignore non-positive values
              </label>
            </div>
          </Panel>

          {preview?.hint && (
            <Panel title="Why this rule">
              <div className="max-h-56 overflow-auto p-3 text-[12px] leading-relaxed">
                <Markdown>{preview.hint}</Markdown>
              </div>
            </Panel>
          )}

          <Panel
            title={`Preview · ${preview?.bins.length ?? 0} bins over the universe`}
            className="min-h-0 flex-1"
            bodyClassName="flex min-h-0 flex-1 flex-col"
          >
            {preview?.message ? (
              <EmptyState title="No preview" detail={preview.message} />
            ) : (
              <EChart option={option} className="min-h-72 flex-1" />
            )}
          </Panel>
        </div>
      </div>

      {note && (
        <div className="border-t border-line bg-panel px-3 py-1.5 text-[11px] text-dim">{note}</div>
      )}
    </div>
  );
}
