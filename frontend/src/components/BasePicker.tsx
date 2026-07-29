/**
 * Base-metric picker for a filter block — searchable, grouped by category, with
 * the parameter's canonical hint on demand.
 *
 * Descriptions come from `config/param_hints.py` through the API; nothing here
 * writes its own copy of what a metric means.
 */
import { useMemo, useState } from "react";
import { Popover } from "radix-ui";
import { Command } from "cmdk";
import { ChevronDown, Info } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { get, type HintRegistry } from "@/lib/api";
import { findBase, type Registry } from "@/lib/filters";
import { HintBody } from "@/components/HintBody";
import { Button, cn } from "@/components/ui";

export function BasePicker({
  registry,
  value,
  onPick,
  placeholder = "parameter…",
}: {
  registry: Registry | undefined;
  value: string;
  onPick: (key: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<string | null>(null);

  const { data: hints } = useQuery({
    queryKey: ["hints"],
    queryFn: () => get<HintRegistry>("/hints"),
    staleTime: Infinity,
  });

  const current = findBase(registry, value);
  const categories = useMemo(() => registry?.categories ?? [], [registry]);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button className="min-w-44 justify-between">
          <span className={cn("truncate", !current && "text-dim")}>
            {current?.label ?? placeholder}
          </span>
          <ChevronDown size={12} className="shrink-0 text-dim" />
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
              {categories.map(({ category, bases }) => (
                <Command.Group
                  key={category}
                  heading={category}
                  className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-dim"
                >
                  {bases.map((base) => {
                    const hint = hints?.[base.key];
                    return (
                      <div key={base.key}>
                        <Command.Item
                          value={`${base.label} ${base.key} ${category}`}
                          onSelect={() => {
                            onPick(base.key);
                            setOpen(false);
                          }}
                          className={cn(
                            "flex cursor-pointer items-center gap-2 rounded px-2 py-1 data-[selected=true]:bg-accent/15",
                            base.key === value ? "text-accent" : "text-ink",
                          )}
                        >
                          <span className="flex-1 truncate">{base.label}</span>
                          {base.unit && <span className="text-dim">{base.unit}</span>}
                          {hint && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setInfo(info === base.key ? null : base.key);
                              }}
                              className="shrink-0 text-dim hover:text-accent"
                              aria-label={`About ${base.label}`}
                            >
                              <Info size={12} />
                            </button>
                          )}
                        </Command.Item>
                        {info === base.key && hint && (
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
