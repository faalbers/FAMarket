/** Header button + body section. Open state lives with the caller. */
import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/components/ui";

export function Collapsible({
  title,
  meta,
  open,
  onToggle,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  meta?: ReactNode;
  open: boolean;
  onToggle: () => void;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("border-b border-line bg-panel", className)}>
      <div className="flex items-center gap-2 pr-3">
        <button
          onClick={onToggle}
          className="flex flex-1 items-center gap-2 px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wider text-dim hover:text-ink"
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {title}
          {meta && <span className="ml-1 normal-case tracking-normal text-dim/70">{meta}</span>}
        </button>
        {actions}
      </div>
      {open && <div className="px-3 pb-3">{children}</div>}
    </section>
  );
}
