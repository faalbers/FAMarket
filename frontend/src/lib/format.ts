/** Number/date formatting. Nulls always render as an em dash, never blank or NaN. */

export const DASH = "—";

export function num(v: unknown, digits = 2): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return DASH;
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Large values as 4.72T / 930.10B — full digit strings are unreadable in a table. */
export function compact(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return DASH;
  const abs = Math.abs(v);
  const [div, suffix] =
    abs >= 1e12 ? [1e12, "T"] : abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : [1, ""];
  return `${(v / div).toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
}

/** Percent values are STORED as percent numbers (12.5 means 12.5%), not fractions. */
export function pct(v: unknown, digits = 1): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return DASH;
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** Render a value according to its param_hints `unit`. */
export function byUnit(v: unknown, unit: string | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return DASH;
  switch (unit) {
    case "%":
      return pct(v);
    case "$":
      return Math.abs(v) >= 1e6 ? compact(v) : num(v);
    case "x":
      return `${num(v)}x`;
    case "yr":
      return `${num(v, 0)} yr`;
    default:
      return num(v);
  }
}

/** UTC ISO timestamp -> local, minute precision. */
export function localTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? DASH : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
