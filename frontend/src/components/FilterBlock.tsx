/**
 * One filter block row: parameter, growth window, comparison variant, operator,
 * value(s) and the row controls. The same component renders an OR child, just
 * indented and without its own OR button.
 *
 * Which controls appear is driven by the registry — a growth window only for
 * growth bases, a variant only when that column actually exists, a second value
 * only for `between`, a value picker only when the column is categorical.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import {
  categoricalValues,
  findBase,
  variantsFor,
  type Block,
  type Compare,
  type Registry,
} from "@/lib/filters";
import { BasePicker } from "@/components/BasePicker";
import { Button, Input, cn } from "@/components/ui";

const COMPARE_LABELS: Record<Compare, string> = {
  value: "Value",
  vs_sector: "vs Sector",
  vs_industry: "vs Industry",
  score: "Score",
};

function Select({
  value,
  onChange,
  options,
  className,
  title,
}: {
  value: string;
  onChange: (next: string) => void;
  options: { value: string; label: string }[];
  className?: string;
  title?: string;
}) {
  return (
    <select
      value={value}
      title={title}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "rounded-md border border-line bg-panel2 px-1.5 py-1.5 text-[12px] text-ink",
        "focus:border-accent/60 focus:outline-none",
        className,
      )}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function FilterBlock({
  block,
  registry,
  onChange,
  onRemove,
  onAddOr,
  onMove,
  isChild = false,
}: {
  block: Block;
  registry: Registry | undefined;
  onChange: (next: Block) => void;
  onRemove: () => void;
  onAddOr?: () => void;
  onMove?: (delta: number) => void;
  isChild?: boolean;
}) {
  const base = findBase(registry, block.param);
  const variants = variantsFor(registry, block);
  const operatorInfo = registry?.operators.find((o) => o.op === block.op);

  // A low-cardinality column swaps the value box for a pick list and the
  // operators for membership tests.
  const { data: categorical } = useQuery({
    queryKey: ["categorical", block.param, block.window, block.compare],
    queryFn: () => categoricalValues(block.param, block.window, block.compare),
    enabled: Boolean(registry && block.param),
    staleTime: 5 * 60_000,
  });
  const picks = categorical?.values ?? null;
  const isCategorical = Array.isArray(picks);

  const set = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  const operators = isCategorical
    ? (registry?.categorical_ops ?? [])
    : (registry?.operators ?? []).map((o) => o.op);

  const isMulti = registry?.multi_ops.includes(block.op) ?? false;
  const isText = registry?.text_ops.includes(block.op) ?? false;
  const showValue = operatorInfo?.needs_value ?? true;
  const showSecond = operatorInfo?.needs_second ?? false;
  // The V/P toggle makes no sense for text or membership operators.
  const showVP = showValue && !isText && !isMulti;

  const selectedPicks = Array.isArray(block.value) ? block.value : [];

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1.5 rounded-md border px-2 py-1.5",
        isChild ? "border-line bg-panel" : "border-line bg-panel2",
        !block.enabled && "opacity-50",
      )}
    >
      <input
        type="checkbox"
        checked={block.enabled}
        onChange={(e) => set({ enabled: e.target.checked })}
        title={block.enabled ? "Disable this condition" : "Enable this condition"}
        className="size-3.5 accent-[#6ea8fe]"
      />

      <BasePicker
        registry={registry}
        value={block.param}
        onPick={(key) => {
          const next = findBase(registry, key);
          set({
            param: key,
            // Reset the window and variant: they may not exist on the new base.
            window: next?.growth ? (next.windows[0]?.key ?? null) : null,
            compare: "value",
          });
        }}
      />

      {base?.growth && base.windows.length > 0 && (
        <Select
          value={block.window ?? base.windows[0]!.key}
          onChange={(window) => set({ window, compare: "value" })}
          options={base.windows.map((w) => ({ value: w.key, label: w.label }))}
          title="Growth window"
        />
      )}

      <Select
        value={block.compare}
        onChange={(compare) => set({ compare: compare as Compare })}
        title="Compare against"
        options={[
          { value: "value", label: COMPARE_LABELS.value },
          ...(variants.vs_sector ? [{ value: "vs_sector", label: COMPARE_LABELS.vs_sector }] : []),
          ...(variants.vs_industry
            ? [{ value: "vs_industry", label: COMPARE_LABELS.vs_industry }]
            : []),
          ...(variants.score ? [{ value: "score", label: COMPARE_LABELS.score }] : []),
        ]}
      />

      <Select
        value={block.op}
        onChange={(op) => set({ op, value: registry?.multi_ops.includes(op) ? [] : "" })}
        options={operators.map((op) => ({ value: op, label: op }))}
        className="w-24"
        title="Operator"
      />

      {showVP && (
        <Button
          size="sm"
          variant="toggle"
          active={block.vmode === "P"}
          onClick={() => set({ vmode: block.vmode === "P" ? "V" : "P", value: "" })}
          title={
            block.vmode === "P"
              ? "Comparing against another parameter — click for a fixed value"
              : "Comparing against a fixed value — click to compare against another parameter"
          }
        >
          {block.vmode}
        </Button>
      )}

      {showValue &&
        (isMulti && isCategorical ? (
          <select
            multiple
            value={selectedPicks}
            onChange={(e) =>
              set({ value: [...e.target.selectedOptions].map((o) => o.value) })
            }
            className="min-w-40 rounded-md border border-line bg-panel2 px-1.5 py-1 text-[12px] text-ink focus:border-accent/60 focus:outline-none"
            size={Math.min(picks!.length, 4)}
          >
            {picks!.map((v) => (
              <option key={String(v)} value={String(v)}>
                {String(v)}
              </option>
            ))}
          </select>
        ) : block.vmode === "P" ? (
          <BasePicker
            registry={registry}
            value={String(block.value)}
            onPick={(key) => set({ value: key })}
            placeholder="another parameter…"
          />
        ) : (
          <div className="w-28">
            <Input
              value={String(block.value ?? "")}
              onChange={(e) => set({ value: e.target.value })}
              placeholder={isText ? "text" : base?.unit || "value"}
            />
          </div>
        ))}

      {showSecond && (
        <>
          <span className="text-[11px] text-dim">and</span>
          <div className="w-28">
            <Input
              value={block.value2 ?? ""}
              onChange={(e) => set({ value2: e.target.value })}
              placeholder={base?.unit || "value"}
            />
          </div>
        </>
      )}

      <div className="ml-auto flex items-center gap-1">
        {onAddOr && (
          <Button size="icon" variant="ghost" onClick={onAddOr} title="Add an OR fallback">
            <Plus size={12} />
          </Button>
        )}
        {onMove && (
          <>
            <Button size="icon" variant="ghost" onClick={() => onMove(-1)} title="Move up">
              <ArrowUp size={12} />
            </Button>
            <Button size="icon" variant="ghost" onClick={() => onMove(1)} title="Move down">
              <ArrowDown size={12} />
            </Button>
          </>
        )}
        <Button size="icon" variant="ghost" onClick={onRemove} title="Remove">
          <X size={12} className="text-down" />
        </Button>
      </div>
    </div>
  );
}
