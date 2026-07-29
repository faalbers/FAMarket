/**
 * Shared primitives. Own the source (the shadcn model) — these are copied in,
 * not imported from a styled component library, so restyling never fights a
 * framework.
 */
import type { ButtonHTMLAttributes, ComponentPropsWithRef, ReactNode } from "react";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...parts: unknown[]) => twMerge(clsx(parts));

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger" | "toggle";
  size?: "sm" | "md" | "lg" | "icon";
  active?: boolean;
  loading?: boolean;
};

const BUTTON_SIZES = {
  sm: "px-2 py-1 text-[11px]",
  md: "px-2.5 py-1.5 text-[12px]",
  lg: "px-3.5 py-2 text-[13px]",
  icon: "size-7 p-0 text-[12px]",
} as const;

export function Button({
  variant = "default",
  size = "md",
  active,
  loading,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium",
        "transition-colors select-none",
        "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
        "disabled:pointer-events-none disabled:opacity-40",
        BUTTON_SIZES[size],
        variant === "default" && "border border-line bg-panel2 text-ink hover:bg-line",
        variant === "primary" &&
          "border border-accent/50 bg-accent/20 text-accent hover:bg-accent/30",
        variant === "ghost" && "text-dim hover:bg-panel2 hover:text-ink",
        // Destructive uses amber, not red — see the colour rule in index.css.
        variant === "danger" && "border border-down/50 bg-down/15 text-down hover:bg-down/25",
        variant === "toggle" &&
          (active
            ? "border border-accent/40 bg-accent/15 text-accent"
            : "border border-line bg-panel2 text-dim hover:text-ink"),
        className,
      )}
    >
      {loading && (
        <span className="inline-block size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  );
}

/** Buttons welded into a single control. */
export function ButtonGroup({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex [&>button]:rounded-none [&>button]:border-r-0 [&>button:first-child]:rounded-l-md [&>button:last-child]:rounded-r-md [&>button:last-child]:border-r">
      {children}
    </div>
  );
}

export function Input({ className, ...props }: ComponentPropsWithRef<"input">) {
  return (
    <input
      {...props}
      className={cn(
        "w-full rounded-md border border-line bg-panel2 px-2.5 py-1.5 text-[12px]",
        "text-ink placeholder:text-dim/70",
        "focus:border-accent/60 focus:outline-none",
        className,
      )}
    />
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Pass "" to opt out of the scroll container — a virtualised table must own
      its own scroll element, and nesting one inside another breaks it. */
  bodyClassName?: string;
}) {
  return (
    <section className={cn("flex min-h-0 flex-col bg-panel", className)}>
      {(title || actions) && (
        <header className="flex h-9 shrink-0 items-center justify-between gap-3 border-b border-line px-3">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-dim">{title}</h2>
          {actions}
        </header>
      )}
      <div className={bodyClassName ?? "min-h-0 flex-1 overflow-auto"}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  caption,
  actions,
}: {
  title: string;
  caption?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex shrink-0 items-end justify-between gap-4 border-b border-line px-4 py-3">
      <div>
        <h1 className="text-[15px] font-semibold text-ink">{title}</h1>
        {caption && <p className="mt-0.5 text-[11px] text-dim">{caption}</p>}
      </div>
      {actions}
    </header>
  );
}

/** Empty states get an icon, a title, one line of explanation and an action. */
export function EmptyState({
  icon,
  title,
  detail,
  action,
}: {
  icon?: ReactNode;
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon && <div className="text-dim/60">{icon}</div>}
      <div className="text-[13px] font-medium text-ink">{title}</div>
      {detail && <div className="max-w-sm text-[12px] text-dim">{detail}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Direction is carried by the glyph and sign as well as the colour. */
export function Delta({ value, digits = 2 }: { value: number | null; digits?: number }) {
  if (value === null || !Number.isFinite(value)) return <span className="text-dim">—</span>;
  const up = value >= 0;
  return (
    <span className={cn("tnum", up ? "text-up" : "text-down")}>
      {up ? "▲" : "▼"} {up ? "+" : ""}
      {value.toFixed(digits)}%
    </span>
  );
}
