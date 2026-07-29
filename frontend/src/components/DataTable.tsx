/**
 * Virtualised data table — the Output screen's grid.
 *
 * Rows are windowed with @tanstack/react-virtual from the start: at FAMarket
 * scale a plain render puts tens of thousands of cells in the DOM and every
 * sort or visibility change re-commits all of them.
 *
 * Three things here are load-bearing and easy to break:
 *  - this component owns THE scroll element; never wrap it in another
 *    `overflow-auto` container or scrolling stops working;
 *  - `TableRow` is memoised, otherwise every scroll tick re-renders every
 *    visible row's cells and the table feels sluggish;
 *  - `tableLayout: fixed` with explicit widths, or the browser re-flows
 *    columns as you scroll.
 */
import { memo, useCallback, useEffect, useRef } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnSizingState,
  type Row as TRow,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/components/ui";

export const ROW_HEIGHT = 29; // must match the real row height or scrolling drifts

type Props<T> = {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  rowId: (row: T) => string;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  sorting: SortingState;
  onSortingChange: (next: SortingState) => void;
  columnVisibility?: VisibilityState;
  onColumnVisibilityChange?: (next: VisibilityState) => void;
  /** Row ids in current display order — the order the PDF and actions use. */
  onOrderChange?: (ids: string[]) => void;
  empty?: React.ReactNode;
};

type RowProps<T> = {
  row: TRow<T>;
  id: string;
  isSelected: boolean;
  onRowClick: (rowId: string, shift: boolean) => void;
  // Present only so memo's shallow compare invalidates on resize / show-hide;
  // both objects get a fresh reference when their state changes.
  sizing: ColumnSizingState;
  visibility: VisibilityState;
};

const TableRow = memo(function TableRow<T>({ row, id, isSelected, onRowClick }: RowProps<T>) {
  return (
    <tr
      // preventDefault stops shift-click extending a browser text selection
      onMouseDown={(e) => e.shiftKey && e.preventDefault()}
      onClick={(e) => onRowClick(id, e.shiftKey)}
      className={cn(
        "cursor-pointer border-b border-line/50",
        isSelected ? "bg-accent/15 hover:bg-accent/20" : "hover:bg-panel2",
      )}
    >
      {row.getVisibleCells().map((cell) => (
        <td
          key={cell.id}
          style={{ width: cell.column.getSize() }}
          className="overflow-hidden whitespace-nowrap px-2 py-1"
        >
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </td>
      ))}
    </tr>
  );
}) as <T>(props: RowProps<T>) => React.ReactElement;

export function DataTable<T>({
  data,
  columns,
  rowId,
  selected,
  onSelectedChange,
  sorting,
  onSortingChange,
  columnVisibility,
  onColumnVisibilityChange,
  onOrderChange,
  empty,
}: Props<T>) {
  const anchorRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting, ...(columnVisibility ? { columnVisibility } : {}) },
    onSortingChange: (updater) =>
      onSortingChange(typeof updater === "function" ? updater(sorting) : updater),
    onColumnVisibilityChange: (updater) =>
      onColumnVisibilityChange?.(
        typeof updater === "function" ? updater(columnVisibility ?? {}) : updater,
      ),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    columnResizeMode: "onChange",
    enableMultiSort: true,
    maxMultiSortColCount: 4,
  });

  const rowModel = table.getRowModel().rows;

  const virtualizer = useVirtualizer({
    count: rowModel.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  });

  useEffect(() => {
    onOrderChange?.(rowModel.map((r) => rowId(r.original)));
    // rowModel identity changes whenever sort or data changes — exactly when
    // the display order can differ.
  }, [rowModel, rowId, onOrderChange]);

  /** Shift-click applies the ANCHOR's state to the whole range in current sort
   *  order, so it deselects a block as readily as it selects one. The anchor is
   *  a row id, so re-sorting never scrambles it. */
  const clickRow = useCallback(
    (id: string, shift: boolean) => {
      const next = new Set(selected);
      if (shift && anchorRef.current !== null) {
        // Read through the stable table instance — never a captured row model.
        const ids = table.getRowModel().rows.map((r) => rowId(r.original));
        const a = ids.indexOf(anchorRef.current);
        const b = ids.indexOf(id);
        if (a !== -1 && b !== -1) {
          const turnOn = selected.has(anchorRef.current);
          for (let i = Math.min(a, b); i <= Math.max(a, b); i++) {
            const each = ids[i]!;
            if (turnOn) next.add(each);
            else next.delete(each);
          }
          onSelectedChange(next);
          return;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      anchorRef.current = id;
      onSelectedChange(next);
    },
    [selected, onSelectedChange, table, rowId],
  );

  const visibleCols = table.getVisibleLeafColumns();
  const items = virtualizer.getVirtualItems();
  const padTop = items.length > 0 ? items[0]!.start : 0;
  const padBottom = items.length > 0 ? virtualizer.getTotalSize() - items[items.length - 1]!.end : 0;

  if (rowModel.length === 0 && empty) {
    return <div className="min-h-0 flex-1">{empty}</div>;
  }

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
      <table
        className="border-collapse text-[12px]"
        style={{ width: table.getTotalSize(), tableLayout: "fixed" }}
      >
        <thead className="sticky top-0 z-10 bg-panel">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-line">
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  style={{ width: h.getSize() }}
                  className="relative px-2 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wider text-dim"
                >
                  <span
                    onClick={h.column.getToggleSortingHandler()}
                    className="block cursor-pointer select-none truncate hover:text-ink"
                    title="Click to sort · shift-click to add a second level"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ↑", desc: " ↓" }[h.column.getIsSorted() as string] ?? ""}
                    {h.column.getSortIndex() > -1 && sorting.length > 1 && (
                      <span className="tnum text-accent"> {h.column.getSortIndex() + 1}</span>
                    )}
                  </span>
                  <span
                    onMouseDown={h.getResizeHandler()}
                    onTouchStart={h.getResizeHandler()}
                    className={cn(
                      "absolute right-0 top-0 h-full w-1 cursor-col-resize touch-none select-none",
                      h.column.getIsResizing() ? "bg-accent" : "hover:bg-accent/50",
                    )}
                  />
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {padTop > 0 && (
            <tr aria-hidden>
              <td colSpan={visibleCols.length} style={{ height: padTop, padding: 0 }} />
            </tr>
          )}
          {items.map((it) => {
            const row = rowModel[it.index]!;
            const id = rowId(row.original);
            return (
              <TableRow
                key={row.id}
                row={row}
                id={id}
                isSelected={selected.has(id)}
                onRowClick={clickRow}
                sizing={table.getState().columnSizing}
                visibility={columnVisibility ?? {}}
              />
            );
          })}
          {padBottom > 0 && (
            <tr aria-hidden>
              <td colSpan={visibleCols.length} style={{ height: padBottom, padding: 0 }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
