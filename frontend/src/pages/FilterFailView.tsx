/**
 * /charts?view=filter_fail — why each selected symbol passed or failed a saved
 * filter, block by block, with the actual value beside the threshold it needed.
 *
 * The report is plain text on purpose: it's a diagnostic to read and paste back
 * when calibrating thresholds, not a data view.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FolderOpen } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button, EmptyState, PageHeader, Panel } from "@/components/ui";

type FilterEntry = { name: string; path: string };
type FailResponse = { cancelled: boolean; path?: string; report?: string };

export function FilterFailView({ symbols }: { symbols: string[] }) {
  const [report, setReport] = useState<string | null>(null);
  const [used, setUsed] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["saved-filters"],
    queryFn: () => get<{ filters: FilterEntry[] }>("/charts/filters"),
    staleTime: 60_000,
  });

  const run = useMutation({
    mutationFn: (path: string) => post<FailResponse>("/charts/filter-fail", { symbols, path }),
    onSuccess: (res) => {
      if (res.cancelled) return;
      setReport(res.report ?? "");
      setUsed(res.path ?? null);
    },
    onError: (err: Error) => setReport(`Could not build the report: ${err.message}`),
  });

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Filter fail diagnostics"
        caption={
          used
            ? `${symbols.length} symbols against ${used.split(/[\\/]/).pop()}`
            : `${symbols.length} symbols · pick a filter to see why each one passes or fails`
        }
        actions={
          <Button onClick={() => run.mutate("")} loading={run.isPending}>
            <FolderOpen size={12} /> Browse…
          </Button>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-[220px_1fr] gap-px bg-line">
        <Panel title="Saved filters">
          <div className="flex flex-col p-1">
            {(data?.filters ?? []).map((entry) => (
              <button
                key={entry.path}
                onClick={() => run.mutate(entry.path)}
                className="truncate rounded px-2 py-1 text-left text-[12px] text-ink hover:bg-accent/10"
              >
                {entry.name}
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Report">
          {run.isPending ? (
            <EmptyState title="Evaluating…" />
          ) : report === null ? (
            <EmptyState
              title="No filter chosen"
              detail="Pick one from the list, or browse for a .filt file."
            />
          ) : (
            <pre className="tnum overflow-auto whitespace-pre p-3 text-[11px] leading-relaxed text-ink">
              {report}
            </pre>
          )}
        </Panel>
      </div>
    </div>
  );
}
