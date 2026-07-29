/** Shapes and helpers for the Filter page. Mirrors ui/filter_engine.py. */
import { get, post } from "@/lib/api";

export type Compare = "value" | "vs_sector" | "vs_industry" | "score";
export type VMode = "V" | "P";

/** One filter block. The same shape nests one level deep as an OR child. */
export type Block = {
  /** UI-only; stripped before saving. */
  _id: string;
  enabled: boolean;
  param: string;
  window: string | null;
  compare: Compare;
  op: string;
  vmode: VMode;
  value: string | string[];
  vmode2: VMode;
  value2: string;
  or_children?: Block[];
};

export type Variants = { vs_sector: boolean; vs_industry: boolean; score: boolean };

export type WindowInfo = { key: string; label: string } & Variants;

export type BaseInfo = {
  key: string;
  label: string;
  unit: string;
  growth: boolean;
  windows: WindowInfo[];
} & Partial<Variants>;

export type Registry = {
  screen_types: { key: string; label: string; help: string }[];
  categories: { category: string; bases: BaseInfo[] }[];
  operators: { op: string; needs_value: boolean; needs_second: boolean; text_only: boolean }[];
  categorical_ops: string[];
  multi_ops: string[];
  text_ops: string[];
};

export type FilterSet = {
  selected_types: string[];
  blocks: unknown[];
  comment: string;
  ai_instructions: string;
  name: string;
};

let nextId = 0;
export const newId = () => `b${++nextId}`;

export function newBlock(param = "price"): Block {
  return {
    _id: newId(),
    enabled: true,
    param,
    window: null,
    compare: "value",
    op: ">",
    vmode: "V",
    value: "",
    vmode2: "V",
    value2: "",
    or_children: [],
  };
}

/** Strip UI-only fields before anything leaves the browser. */
export function clean(blocks: Block[]): unknown[] {
  return blocks.map(({ _id: _drop, or_children, ...rest }) => ({
    ...rest,
    or_children: (or_children ?? []).map(({ _id: _drop2, or_children: _drop3, ...child }) => child),
  }));
}

/** Re-attach UI ids to blocks arriving from a .filt file. */
export function hydrate(blocks: Partial<Block>[]): Block[] {
  return blocks.map((b) => ({
    ...newBlock(),
    ...b,
    _id: newId(),
    or_children: (b.or_children ?? []).map((c) => ({ ...newBlock(), ...c, _id: newId() })),
  }));
}

export function findBase(registry: Registry | undefined, key: string): BaseInfo | undefined {
  for (const category of registry?.categories ?? []) {
    const hit = category.bases.find((b) => b.key === key);
    if (hit) return hit;
  }
  return undefined;
}

/** Variants available for a block's current param + window. */
export function variantsFor(registry: Registry | undefined, block: Block): Variants {
  const base = findBase(registry, block.param);
  if (!base) return { vs_sector: false, vs_industry: false, score: false };
  if (!base.growth) {
    return {
      vs_sector: Boolean(base.vs_sector),
      vs_industry: Boolean(base.vs_industry),
      score: Boolean(base.score),
    };
  }
  const window = base.windows.find((w) => w.key === block.window) ?? base.windows[0];
  return {
    vs_sector: Boolean(window?.vs_sector),
    vs_industry: Boolean(window?.vs_industry),
    score: Boolean(window?.score),
  };
}

export const loadRegistry = (types: string[]) =>
  get<Registry>("/filter/registry", { types: types.join(",") });

export const countMatches = (payload: FilterSet) =>
  post<{ count: number; incomplete_blocks: number }>("/filter/count", payload);

export const runFilter = (payload: FilterSet) =>
  post<{ run_id: string | null; count: number }>("/filter/run", payload);

export const saveFilter = (payload: FilterSet) =>
  post<{ cancelled: boolean; path?: string; name?: string }>("/filter/save", payload);

export const openFilter = (path = "") =>
  post<{
    cancelled: boolean;
    name?: string;
    selected_types?: string[];
    blocks?: Partial<Block>[];
    comment?: string;
    ai_instructions?: string;
  }>("/filter/load", { path });

export const categoricalValues = (param: string, window: string | null, compare: string) =>
  get<{ column: string; values: (string | number | null)[] | null }>("/filter/categorical", {
    param,
    window: window ?? "",
    compare,
  });
