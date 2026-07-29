/** Shapes and helpers for saved outputs ("runs"). */
import { get, post } from "@/lib/api";

export type BlockDescription = {
  text: string;
  incomplete: boolean;
  children: { text: string; incomplete: boolean }[];
};

export type RunMeta = {
  run_id: string;
  created_at: string;
  row_count: number;
  kind: "filter" | "custom";
  filter_name?: string | null;
  comment?: string | null;
  ai_instructions?: string | null;
  screen_types?: string[];
  param_cols?: string[];
  symbols?: string[];
  type_labels: string;
  blocks_described: BlockDescription[];
};

/** Columnar payload — column names aren't repeated per row. */
export type Table = { columns: string[]; data: unknown[][] };

export type RunPayload = { meta: RunMeta; table: Table };

export type ColumnInfo = {
  key: string;
  label: string;
  category: string;
  unit?: string;
  hint_key?: string | null;
};

export const listRuns = () => get<{ runs: RunMeta[]; keep: number }>("/runs");
export const loadRun = (runId: string) => get<RunPayload>(`/runs/${runId}`);
export const deleteRuns = (runIds: string[]) =>
  post<{ deleted: number }>("/runs/delete", { run_ids: runIds });
export const createCustomRun = (name: string, symbols: string[]) =>
  post<{ run_id: string; row_count: number; missing: string[] }>("/runs/custom", { name, symbols });

export const columnOptions = (types: string[], extra: string[]) =>
  get<{ options: ColumnInfo[] }>("/columns/options", {
    types: types.join(","),
    extra: extra.join(","),
  });
export const describeColumns = (cols: string[]) =>
  get<{ columns: ColumnInfo[] }>("/columns/describe", { cols: cols.join(",") });
export const externalSites = () => get<Record<string, string>>("/columns/external-sites");

export type SelectionKind = "symbols" | "params";
export const saveSelection = (kind: SelectionKind, items: string[], defaultName = "") =>
  post<{ cancelled: boolean; path?: string; name?: string; count?: number }>("/selections/save", {
    kind,
    items,
    default_name: defaultName,
  });
export const loadSelection = (kind: SelectionKind) =>
  post<{ cancelled: boolean; name?: string; items?: string[] }>("/selections/load", { kind });

/** Comma/space/newline separated tickers -> upper-cased list. */
export function parseSymbols(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
}
