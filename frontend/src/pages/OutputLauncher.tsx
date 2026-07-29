/**
 * Output launcher (no `?run=`): recent saved outputs plus the Custom Symbols
 * box. Each output opens in its own tab at /output?run=<id>, the same URL
 * contract the Streamlit app used.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, FolderOpen, Save, Table2, Trash2 } from "lucide-react";
import {
  createCustomRun,
  deleteRuns,
  listRuns,
  loadSelection,
  saveSelection,
  type RunMeta,
} from "@/lib/runs";
import { localTime } from "@/lib/format";
import { SymbolPicker } from "@/components/SymbolPicker";
import { Button, EmptyState, Input, Panel, PageHeader, cn } from "@/components/ui";

function runUrl(runId: string) {
  return `/output?run=${encodeURIComponent(runId)}`;
}

export function OutputLauncher() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);

  const { data, isLoading } = useQuery({ queryKey: ["runs"], queryFn: listRuns, staleTime: 0 });
  const runs = data?.runs ?? [];

  const remove = useMutation({
    mutationFn: (ids: string[]) => deleteRuns(ids),
    onSuccess: (res) => {
      setSelected(new Set());
      setNote(`Deleted ${res.deleted} output${res.deleted === 1 ? "" : "s"}.`);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const createCustom = useMutation({
    mutationFn: () => createCustomRun(name.trim() || "Custom", symbols),
    onSuccess: (res) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      window.open(runUrl(res.run_id), "_blank");
      setNote(
        res.missing.length
          ? `Opened ${res.row_count} symbols. Not in analysis.db: ${res.missing.join(", ")}`
          : `Opened ${res.row_count} symbols in a new tab.`,
      );
    },
    onError: (err: Error) => setNote(err.message),
  });

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Output"
        caption={
          isLoading
            ? "Loading…"
            : `${runs.length} saved output${runs.length === 1 ? "" : "s"} · newest ${data?.keep ?? 0} kept`
        }
      />

      <div className="grid min-h-0 flex-1 grid-rows-[auto_1fr] gap-px bg-line">
        <Panel title="Custom symbols" className="shrink-0">
          <div className="flex items-start gap-2 p-3">
            <div className="w-48 shrink-0">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Output name"
              />
            </div>
            <div className="min-w-64 flex-1">
              <SymbolPicker symbols={symbols} onChange={setSymbols} />
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                variant="primary"
                disabled={symbols.length === 0}
                loading={createCustom.isPending}
                onClick={() => createCustom.mutate()}
              >
                <Table2 size={12} /> Open {symbols.length || ""}
              </Button>
              <Button
                disabled={symbols.length === 0}
                onClick={async () => {
                  const res = await saveSelection("symbols", symbols, name.trim());
                  if (!res.cancelled) setNote(`Saved ${res.count} symbols as ${res.name}.`);
                }}
              >
                <Save size={12} /> Save
              </Button>
              <Button
                onClick={async () => {
                  const res = await loadSelection("symbols");
                  if (res.cancelled || !res.items) return;
                  setSymbols(res.items);
                  if (!name.trim() && res.name) setName(res.name);
                }}
              >
                <FolderOpen size={12} /> Load
              </Button>
            </div>
          </div>
          {note && <div className="border-t border-line px-3 py-1.5 text-[11px] text-dim">{note}</div>}
        </Panel>

        <Panel
          title={`Recent outputs${selected.size ? ` · ${selected.size} selected` : ""}`}
          actions={
            <Button
              variant="danger"
              size="sm"
              disabled={selected.size === 0}
              loading={remove.isPending}
              onClick={() => remove.mutate([...selected])}
            >
              <Trash2 size={12} /> Delete selected
            </Button>
          }
        >
          {runs.length === 0 && !isLoading ? (
            <EmptyState
              icon={<Table2 size={28} />}
              title="No outputs yet"
              detail="Run a screen on the Filter page, or open a set of symbols with the Custom symbols box above."
            />
          ) : (
            <table className="w-full border-collapse text-[12px]">
              <thead className="sticky top-0 bg-panel">
                <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-dim">
                  <th className="w-8 px-3 py-1.5" />
                  <th className="px-2 py-1.5">Name</th>
                  <th className="w-20 px-2 py-1.5">Type</th>
                  <th className="w-44 px-2 py-1.5">Run at</th>
                  <th className="w-20 px-2 py-1.5 text-right">Rows</th>
                  <th className="px-2 py-1.5">Security types</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run: RunMeta) => (
                  <tr
                    key={run.run_id}
                    onClick={() => toggle(run.run_id)}
                    className={cn(
                      "cursor-pointer border-b border-line/50",
                      selected.has(run.run_id) ? "bg-accent/15" : "hover:bg-panel2",
                    )}
                  >
                    <td className="px-3 py-1">
                      <span
                        className={cn(
                          "grid size-3.5 place-items-center rounded-sm border",
                          selected.has(run.run_id) ? "border-accent bg-accent/30" : "border-line",
                        )}
                      >
                        {selected.has(run.run_id) && (
                          <span className="size-1.5 rounded-sm bg-accent" />
                        )}
                      </span>
                    </td>
                    <td className="px-2 py-1">
                      <a
                        href={runUrl(run.run_id)}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 font-medium text-accent hover:underline"
                      >
                        {run.filter_name || "(ad-hoc)"}
                        <ExternalLink size={11} />
                      </a>
                    </td>
                    <td className="px-2 py-1 text-dim">
                      {run.kind === "custom" ? "Custom" : "Filter"}
                    </td>
                    <td className="tnum px-2 py-1 text-dim">{localTime(run.created_at)}</td>
                    <td className="tnum px-2 py-1 text-right">{run.row_count}</td>
                    <td className="truncate px-2 py-1 text-dim">{run.type_labels}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}
