/**
 * Sector → industry tree, single-select, for the relative-strength view.
 *
 * Radix has no tree primitive, so the behaviour (open/close/dismiss/collision)
 * comes from Popover and the two-level list is hand-rolled. Picking the sector
 * name selects the whole sector; picking an industry selects that industry.
 * Clicking the active entry clears it and returns the plain price view.
 */
import { useState } from "react";
import { Popover } from "radix-ui";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { Button, cn } from "@/components/ui";

export type GroupTree = Record<string, string[]>;

export function GroupSelect({
  tree,
  value,
  onChange,
}: {
  tree: GroupTree;
  /** `"S::<sector>"` or `"I::<Sector | Industry>"`, or null for none. */
  value: string | null;
  onChange: (next: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const sectors = Object.keys(tree);
  const label = value
    ? value.startsWith("S::")
      ? value.slice(3)
      : value.slice(3).split(" | ")[1]
    : null;

  function pick(next: string) {
    onChange(value === next ? null : next);
    setOpen(false);
  }

  if (sectors.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger asChild>
          <Button variant={value ? "toggle" : "default"} active={Boolean(value)}>
            <span className="max-w-52 truncate">{label ?? "Compare to sector / industry"}</span>
            <ChevronDown size={12} className="text-dim" />
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            align="start"
            sideOffset={4}
            className="z-50 max-h-96 w-80 overflow-y-auto rounded-md border border-line bg-panel2 p-1 shadow-xl"
          >
            {sectors.map((sector) => {
              const sectorKey = `S::${sector}`;
              const isOpen = expanded.has(sector);
              const industries = tree[sector] ?? [];
              return (
                <div key={sector}>
                  <div className="flex items-center">
                    <button
                      onClick={() =>
                        setExpanded((prev) => {
                          const next = new Set(prev);
                          if (!next.delete(sector)) next.add(sector);
                          return next;
                        })
                      }
                      disabled={industries.length === 0}
                      aria-label={isOpen ? `Collapse ${sector}` : `Expand ${sector}`}
                      className="grid size-6 shrink-0 place-items-center rounded text-dim hover:text-ink disabled:opacity-30"
                    >
                      {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </button>
                    <button
                      onClick={() => pick(sectorKey)}
                      className={cn(
                        "flex-1 truncate rounded px-1.5 py-1 text-left text-[12px] font-medium hover:bg-accent/10",
                        value === sectorKey ? "text-accent" : "text-ink",
                      )}
                    >
                      {sector}
                    </button>
                  </div>
                  {isOpen &&
                    industries.map((full) => {
                      const industryKey = `I::${full}`;
                      return (
                        <button
                          key={full}
                          onClick={() => pick(industryKey)}
                          className={cn(
                            "block w-full truncate rounded py-1 pl-8 pr-2 text-left text-[12px] hover:bg-accent/10",
                            value === industryKey ? "text-accent" : "text-dim hover:text-ink",
                          )}
                        >
                          {full.split(" | ")[1] ?? full}
                        </button>
                      );
                    })}
                </div>
              );
            })}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {value && (
        <Button size="icon" variant="ghost" onClick={() => onChange(null)} title="Clear group">
          <X size={12} />
        </Button>
      )}
    </div>
  );
}
