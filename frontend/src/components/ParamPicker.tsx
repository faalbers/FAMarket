/**
 * Single-select parameter picker, grouped by category — the chart views'
 * counterpart to the Output column chooser. Closes on pick.
 */
import { useMemo, useState } from "react";
import { Popover } from "radix-ui";
import { Command } from "cmdk";
import { Check, ChevronDown } from "lucide-react";
import { Button, cn } from "@/components/ui";

export type ParamOption = { key: string; label: string; category: string; unit?: string };

export function ParamPicker({
  options,
  value,
  onPick,
}: {
  options: ParamOption[];
  value: string;
  onPick: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);

  const groups = useMemo(() => {
    const out = new Map<string, ParamOption[]>();
    for (const option of options) {
      const bucket = out.get(option.category);
      if (bucket) bucket.push(option);
      else out.set(option.category, [option]);
    }
    return [...out.entries()];
  }, [options]);

  const current = options.find((o) => o.key === value);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button>
          <span className="max-w-52 truncate">{current?.label ?? "Pick a parameter"}</span>
          <ChevronDown size={12} className="text-dim" />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={4}
          className="z-50 w-80 rounded-md border border-line bg-panel2 shadow-xl"
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
                  {items.map((option) => (
                    <Command.Item
                      key={option.key}
                      value={`${option.label} ${option.key}`}
                      onSelect={() => {
                        onPick(option.key);
                        setOpen(false);
                      }}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded px-2 py-1 data-[selected=true]:bg-accent/15",
                        option.key === value ? "text-accent" : "text-ink",
                      )}
                    >
                      <span className="w-3.5 shrink-0">
                        {option.key === value && <Check size={11} />}
                      </span>
                      <span className="flex-1 truncate">{option.label}</span>
                      {option.unit && <span className="text-dim">{option.unit}</span>}
                    </Command.Item>
                  ))}
                </Command.Group>
              ))}
            </Command.List>
          </Command>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
