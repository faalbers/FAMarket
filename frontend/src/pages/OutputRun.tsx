/**
 * Output run view (`/output?run=<id>`) — one saved output's results.
 *
 * The whole result frame arrives in one columnar response, so sorting, column
 * show/hide and selection are all instant and local. Cell values are read out
 * of the columnar arrays by row index (`accessorFn`), so no per-row objects are
 * built for a 240-column frame.
 *
 * Multi-level sort is shift-click on the headers (up to 4 levels) — the
 * Streamlit page needed a separate sort panel only because st.dataframe's
 * canvas headers couldn't do it.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef, SortingState, VisibilityState } from "@tanstack/react-table";
import { Eye, EyeOff, FileDown, FolderOpen, Save, X } from "lucide-react";
import { DataTable } from "@/components/DataTable";
import { ColumnPicker } from "@/components/ColumnPicker";
import { ActionMenu } from "@/components/ActionMenu";
import { Collapsible } from "@/components/Collapsible";
import { Markdown } from "@/components/Markdown";
import { Button, EmptyState, PageHeader, Panel, cn } from "@/components/ui";
import { byUnit, localTime } from "@/lib/format";
import { post } from "@/lib/api";
import {
  columnOptions,
  describeColumns,
  loadRun,
  loadSelection,
  saveSelection,
  type ColumnInfo,
} from "@/lib/runs";

/** Identity columns always shown, ahead of the parameter columns. */
const LEAD: { key: string; label: string; size: number }[] = [
  { key: "symbol", label: "Symbol", size: 84 },
  { key: "name", label: "Company", size: 190 },
  { key: "sector", label: "Sector", size: 150 },
  { key: "industry", label: "Industry", size: 170 },
];

type Row = { i: number; symbol: string };

export function OutputRun({ runId }: { runId: string }) {
  const [paramCols, setParamCols] = useState<string[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sorting, setSorting] = useState<SortingState>([]);
  const [order, setOrder] = useState<string[]>([]);
  const [showComment, setShowComment] = useState(true);
  const [showInstructions, setShowInstructions] = useState(false);
  const [showFilter, setShowFilter] = useState(false);
  const [showColumns, setShowColumns] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [reportBusy, setReportBusy] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["run", runId],
    // Run files are immutable once written, so this never needs refetching.
    queryFn: () => loadRun(runId),
    staleTime: Infinity,
  });

  const meta = data?.meta;
  const table = data?.table;

  // The run's own param columns are the starting column set.
  useEffect(() => {
    if (meta) setParamCols(meta.param_cols ?? []);
  }, [meta]);

  const { data: optionData } = useQuery({
    queryKey: ["column-options", meta?.screen_types, paramCols],
    queryFn: () => columnOptions(meta?.screen_types ?? [], paramCols),
    enabled: Boolean(meta),
    staleTime: 5 * 60_000,
  });

  const { data: describedData } = useQuery({
    queryKey: ["columns-describe", paramCols],
    queryFn: () => describeColumns(paramCols),
    enabled: paramCols.length > 0,
    staleTime: Infinity,
  });

  const described = useMemo(() => {
    const map = new Map<string, ColumnInfo>();
    for (const c of describedData?.columns ?? []) map.set(c.key, c);
    return map;
  }, [describedData]);

  const colIndex = useMemo(() => {
    const map = new Map<string, number>();
    (table?.columns ?? []).forEach((c, i) => map.set(c, i));
    return map;
  }, [table]);

  const rows = useMemo<Row[]>(() => {
    const symbolCol = colIndex.get("symbol") ?? 0;
    return (table?.data ?? []).map((r, i) => ({ i, symbol: String(r[symbolCol]) }));
  }, [table, colIndex]);

  const cell = useCallback(
    (key: string) => {
      const idx = colIndex.get(key);
      if (idx === undefined) return () => null;
      return (row: Row) => table?.data[row.i]?.[idx] ?? null;
    },
    [colIndex, table],
  );

  const columns = useMemo<ColumnDef<Row, unknown>[]>(() => {
    const lead = LEAD.filter((c) => colIndex.has(c.key)).map(
      (c): ColumnDef<Row, unknown> => ({
        id: c.key,
        header: c.label,
        accessorFn: cell(c.key),
        size: c.size,
        cell: (ctx) => {
          const value = ctx.getValue();
          const text = value === null || value === undefined ? "—" : String(value);
          return c.key === "symbol" ? (
            <span className="tnum font-semibold text-ink">{text}</span>
          ) : (
            <span className="block truncate text-dim">{text}</span>
          );
        },
      }),
    );

    const params = paramCols
      .filter((key) => colIndex.has(key))
      .map((key): ColumnDef<Row, unknown> => {
        const info = described.get(key);
        return {
          id: key,
          header: info?.label ?? key,
          accessorFn: cell(key),
          size: 110,
          sortUndefined: "last",
          cell: (ctx) => {
            const value = ctx.getValue();
            const text =
              typeof value === "number"
                ? byUnit(value, info?.unit)
                : value === null || value === undefined
                  ? "—"
                  : String(value);
            return <span className="tnum block text-right">{text}</span>;
          },
        };
      });

    return [...lead, ...params];
  }, [paramCols, colIndex, described, cell]);

  const visibility = useMemo<VisibilityState>(
    () => Object.fromEntries([...hidden].map((k) => [k, false])),
    [hidden],
  );

  const shownCols = paramCols.filter((c) => !hidden.has(c));
  // Selection follows the table's display order, so the PDF and chart actions
  // get the rows in the order the user is looking at.
  const selectedSymbols = order.filter((s) => selected.has(s));

  const sortSummary =
    sorting.map((s) => `${described.get(s.id)?.label ?? s.id} ${s.desc ? "▼" : "▲"}`).join(" → ") ||
    "none";

  const toggleColumn = (key: string) =>
    setParamCols((prev) => (prev.includes(key) ? prev.filter((c) => c !== key) : [...prev, key]));

  async function generateReport() {
    setReportBusy(true);
    setNote(null);
    try {
      const columnSpecs = [
        ...LEAD.filter((c) => colIndex.has(c.key)).map((c) => ({ key: c.key, label: c.label })),
        ...shownCols.map((key) => ({ key, label: described.get(key)?.label ?? key })),
      ];
      const res = await post<{ filename: string }>("/reports/output", {
        run_id: runId,
        symbols: selectedSymbols,
        columns: columnSpecs,
        sort_summary: sortSummary,
      });
      window.open(`/api/reports/${encodeURIComponent(res.filename)}`, "_blank");
      setNote(`Saved ${res.filename}`);
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Report failed.");
    } finally {
      setReportBusy(false);
    }
  }

  if (isLoading) {
    return <EmptyState title="Loading output…" />;
  }
  if (error || !meta || !table) {
    return (
      <EmptyState
        title="Output not found"
        detail="It may have been pruned — only the newest outputs are kept."
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={meta.filter_name || "(ad-hoc)"}
        caption={
          <>
            {meta.row_count} row{meta.row_count === 1 ? "" : "s"} · {localTime(meta.created_at)}
            {meta.type_labels ? ` · ${meta.type_labels}` : ""}
            {meta.kind === "custom" ? " · Custom symbol set" : ""}
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            <ActionMenu symbols={selectedSymbols} cols={shownCols} />
            <Button
              disabled={selectedSymbols.length === 0}
              loading={reportBusy}
              onClick={generateReport}
            >
              <FileDown size={12} /> Report
            </Button>
          </div>
        }
      />

      {meta.comment && (
        <Collapsible
          title="Comment"
          open={showComment}
          onToggle={() => setShowComment((v) => !v)}
        >
          <Markdown>{meta.comment}</Markdown>
        </Collapsible>
      )}

      {meta.ai_instructions && (
        <Collapsible
          title="AI instructions"
          open={showInstructions}
          onToggle={() => setShowInstructions((v) => !v)}
        >
          <Markdown>{meta.ai_instructions}</Markdown>
        </Collapsible>
      )}

      <Collapsible
        title={meta.kind === "custom" ? "Symbols in this output" : "Filter used for this run"}
        meta={meta.kind === "filter" ? `${meta.blocks_described.length} conditions` : undefined}
        open={showFilter}
        onToggle={() => setShowFilter((v) => !v)}
      >
        {meta.kind === "custom" ? (
          <div className="tnum text-[12px] text-dim">{(meta.symbols ?? []).join(", ")}</div>
        ) : meta.blocks_described.length === 0 ? (
          <div className="text-[12px] text-dim">
            No filter conditions — every symbol of the selected types.
          </div>
        ) : (
          <ul className="flex flex-col gap-1 text-[12px]">
            {meta.blocks_described.map((block, i) => (
              <li key={i}>
                <Markdown className="text-ink/90">{`- ${block.text}${block.incomplete ? " *(incomplete — ignored)*" : ""}`}</Markdown>
                {block.children.map((child, j) => (
                  <div key={j} className="pl-5">
                    <Markdown className="text-dim">{`- **OR** ${child.text}`}</Markdown>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        )}
      </Collapsible>

      <Collapsible
        title="Parameter columns"
        meta={`${shownCols.length} shown / ${paramCols.length} total`}
        open={showColumns}
        onToggle={() => setShowColumns((v) => !v)}
        actions={
          <div className="flex items-center gap-2 py-1">
            <ColumnPicker
              options={optionData?.options ?? []}
              selected={new Set(paramCols)}
              onToggle={toggleColumn}
            />
            <Button
              size="sm"
              disabled={paramCols.length === 0}
              onClick={async () => {
                const res = await saveSelection("params", paramCols, meta.filter_name ?? "");
                if (!res.cancelled) setNote(`Saved ${res.count} columns as ${res.name}.`);
              }}
            >
              <Save size={12} /> Save
            </Button>
            <Button
              size="sm"
              onClick={async () => {
                const res = await loadSelection("params");
                if (res.cancelled || !res.items) return;
                setParamCols((prev) => [...new Set([...prev, ...res.items!])]);
              }}
            >
              <FolderOpen size={12} /> Add
            </Button>
          </div>
        }
      >
        {paramCols.length === 0 ? (
          <div className="text-[12px] text-dim">No parameter columns — add some above.</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {paramCols.map((key) => {
              const isHidden = hidden.has(key);
              return (
                <span
                  key={key}
                  className={cn(
                    "flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[11px]",
                    isHidden ? "border-line text-dim" : "border-accent/40 bg-accent/10 text-ink",
                  )}
                >
                  <button
                    onClick={() =>
                      setHidden((prev) => {
                        const next = new Set(prev);
                        if (next.has(key)) next.delete(key);
                        else next.add(key);
                        return next;
                      })
                    }
                    title={isHidden ? "Show column" : "Hide column"}
                    className="text-dim hover:text-ink"
                  >
                    {isHidden ? <EyeOff size={11} /> : <Eye size={11} />}
                  </button>
                  {described.get(key)?.label ?? key}
                  <button
                    onClick={() => setParamCols((prev) => prev.filter((c) => c !== key))}
                    title="Remove column"
                    className="text-dim hover:text-down"
                  >
                    <X size={11} />
                  </button>
                </span>
              );
            })}
          </div>
        )}
      </Collapsible>

      <Panel
        title={`Results · ${selectedSymbols.length} of ${rows.length} selected · sorted: ${sortSummary}`}
        className="min-h-0 flex-1"
        // The virtualised table owns its own scroll element.
        bodyClassName="flex min-h-0 flex-1 flex-col"
        actions={
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={selectedSymbols.length === 0}
              onClick={async () => {
                const res = await saveSelection(
                  "symbols",
                  selectedSymbols,
                  meta.filter_name ?? "",
                );
                if (!res.cancelled) setNote(`Saved ${res.count} symbols as ${res.name}.`);
              }}
            >
              <Save size={12} /> Save selection
            </Button>
            <Button
              size="sm"
              onClick={async () => {
                const res = await loadSelection("symbols");
                if (res.cancelled || !res.items) return;
                const present = new Set(rows.map((r) => r.symbol));
                setSelected(new Set(res.items.filter((s) => present.has(s))));
              }}
            >
              <FolderOpen size={12} /> Load selection
            </Button>
            <Button size="sm" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </div>
        }
      >
        <DataTable
          data={rows}
          columns={columns}
          rowId={(r) => r.symbol}
          selected={selected}
          onSelectedChange={setSelected}
          sorting={sorting}
          onSortingChange={setSorting}
          columnVisibility={visibility}
          onOrderChange={setOrder}
        />
      </Panel>

      {note && (
        <div className="border-t border-line bg-panel px-3 py-1.5 text-[11px] text-dim">{note}</div>
      )}
    </div>
  );
}
