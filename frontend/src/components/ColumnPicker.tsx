/**
 * Filterable multi-select for parameter columns, grouped by category.
 *
 * cmdk supplies filtering + keyboard navigation + ARIA inside a Radix Popover.
 * Built on Popover, not DropdownMenu: menu semantics close on select and fight
 * typing. Descriptions come from `config/param_hints.py` via the API — never
 * written here.
 */
import { useMemo, useState } from "react";
import { Popover } from "radix-ui";
import { Command } from "cmdk";
import { Check, ChevronDown, Info } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { get, type HintRegistry } from "@/lib/api";
import type { ColumnInfo } from "@/lib/runs";
import { HintBody } from "@/components/HintBody";
import { Button, cn } from "@/components/ui";

export function ColumnPicker({
  options,
  selected,
  onToggle,
  label = "Add columns",
}: {
  options: ColumnInfo[];
  selected: Set<string>;
  onToggle: (key: string) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<string | null>(null);

  const { data: hints } = useQuery({
    queryKey: ["hints"],
    queryFn: () => get<HintRegistry>("/hints"),
    staleTime: Infinity,
  });

  const groups = useMemo(() => {
    const out = new Map<string, ColumnInfo[]>();
    for (const opt of options) {
      const bucket = out.get(opt.category);
      if (bucket) bucket.push(opt);
      else out.set(opt.category, [opt]);
    }
    return [...out.entries()];
  }, [options]);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button>
          {label}
          <ChevronDown size={12} className="text-dim" />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className="z-50 w-96 rounded-md border border-line bg-panel2 shadow-xl"
        >
          <Command className="text-[12px]" loop>
            <Command.Input
              autoFocus
              placeholder="Filter parameters…"
              className="w-full border-b border-line bg-transparent px-3 py-2 text-ink outline-none placeholder:text-dim/60"
            />
            <Command.List className="max-h-96 overflow-y-auto p-1">
              <Command.Empty className="px-3 py-4 text-dim">No match.</Command.Empty>
              {groups.map(([category, items]) => (
                <Command.Group
                  key={category}
                  heading={category}
                  className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-dim"
                >
                  {items.map((opt) => {
                    const on = selected.has(opt.key);
                    const hint = opt.hint_key ? hints?.[opt.hint_key] : undefined;
                    return (
                      <div key={opt.key}>
                        <Command.Item
                          value={`${opt.label} ${opt.key} ${category}`}
                          // Fires on click AND enter; the popover stays open so
                          // several columns can be picked in one visit.
                          onSelect={() => onToggle(opt.key)}
                          className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-ink data-[selected=true]:bg-accent/15"
                        >
                          <span
                            className={cn(
                              "grid size-3.5 shrink-0 place-items-center rounded-sm border",
                              on ? "border-accent bg-accent/30" : "border-line",
                            )}
                          >
                            {on && <Check size={10} className="text-accent" />}
                          </span>
                          <span className="flex-1 truncate">{opt.label}</span>
                          {hint && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setInfo(info === opt.key ? null : opt.key);
                              }}
                              className="shrink-0 text-dim hover:text-accent"
                              aria-label={`About ${opt.label}`}
                            >
                              <Info size={12} />
                            </button>
                          )}
                        </Command.Item>
                        {info === opt.key && hint && (
                          <div className="mx-2 mb-1 rounded border border-line bg-panel p-2">
                            <HintBody hint={hint} />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </Command.Group>
              ))}
            </Command.List>
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
