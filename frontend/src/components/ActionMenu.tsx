/**
 * Action menu for the selected rows — chart views open in a new tab at
 * /charts?view=…&symbols=…, external sites use the templates from
 * `settings.EXTERNAL_SITES`. Same URL contract as the Streamlit page.
 */
import { Popover } from "radix-ui";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ExternalLink } from "lucide-react";
import { externalSites } from "@/lib/runs";
import { Button } from "@/components/ui";

/** Param-driven views default their picker to the Output's SHOWN columns. */
function chartUrl(symbols: string[], view: string, cols?: string[]) {
  const params = new URLSearchParams({ view, symbols: symbols.join(",") });
  if (cols?.length) params.set("cols", cols.join(","));
  return `/charts?${params}`;
}

const CHART_ACTIONS: { view: string; label: string; withCols?: boolean; group: string }[] = [
  { view: "price", label: "Normalized price", group: "Charts" },
  { view: "fundamentals_bar", label: "Fundamentals over time", withCols: true, group: "Charts" },
  { view: "fundamentals_line", label: "Fundamentals growth lines", withCols: true, group: "Charts" },
  { view: "radar", label: "Category scores radar", group: "Charts" },
  { view: "heatmap", label: "Metrics heat map", withCols: true, group: "Charts" },
  { view: "scores_heatmap", label: "Scores heat map", group: "Charts" },
  { view: "dividend_line", label: "Dividend yield", group: "Dividends" },
  { view: "news", label: "Latest news", group: "News" },
  { view: "filter_fail", label: "Filter Fail", group: "Diagnostics" },
];

export function ActionMenu({ symbols, cols }: { symbols: string[]; cols: string[] }) {
  const { data: sites } = useQuery({
    queryKey: ["external-sites"],
    queryFn: externalSites,
    staleTime: Infinity,
  });

  const groups = [...new Set(CHART_ACTIONS.map((a) => a.group))];
  const csv = symbols.join(",");

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button variant="primary" disabled={symbols.length === 0}>
          Actions ({symbols.length})
          <ChevronDown size={12} />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={4}
          className="z-50 max-h-[80vh] w-72 overflow-y-auto rounded-md border border-line bg-panel2 p-2 shadow-xl"
        >
          <div className="mb-2 truncate text-[11px] text-dim">
            {symbols.slice(0, 10).join(", ")}
            {symbols.length > 10 ? "…" : ""}
          </div>

          {groups.map((group) => (
            <div key={group} className="mb-2">
              <div className="px-1 py-1 text-[10px] font-semibold uppercase tracking-wider text-dim">
                {group}
              </div>
              {CHART_ACTIONS.filter((a) => a.group === group).map((action) => (
                <a
                  key={action.view}
                  href={chartUrl(symbols, action.view, action.withCols ? cols : undefined)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between rounded px-2 py-1 text-[12px] text-ink hover:bg-accent/15"
                >
                  {action.label}
                  <ExternalLink size={11} className="text-dim" />
                </a>
              ))}
            </div>
          ))}

          {sites && (
            <div className="mb-1 border-t border-line pt-2">
              <div className="px-1 py-1 text-[10px] font-semibold uppercase tracking-wider text-dim">
                External
              </div>
              {sites.finviz && (
                <a
                  href={sites.finviz.replace("{symbols}", csv)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between rounded px-2 py-1 text-[12px] text-ink hover:bg-accent/15"
                >
                  Finviz <ExternalLink size={11} className="text-dim" />
                </a>
              )}
              {sites.yahoo && (
                <a
                  href={sites.yahoo.replace("{symbols}", csv)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between rounded px-2 py-1 text-[12px] text-ink hover:bg-accent/15"
                >
                  Yahoo Finance <ExternalLink size={11} className="text-dim" />
                </a>
              )}
              {sites.tradingview && (
                <>
                  <div className="px-1 pt-1 text-[10px] text-dim">TradingView — one tab each:</div>
                  <div className="flex flex-wrap gap-1 p-1">
                    {symbols.map((sym) => (
                      <a
                        key={sym}
                        href={sites.tradingview.replace("{symbol}", sym)}
                        target="_blank"
                        rel="noreferrer"
                        className="tnum rounded bg-line px-1.5 py-0.5 text-[11px] text-ink hover:bg-accent/20"
                      >
                        {sym}
                      </a>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
