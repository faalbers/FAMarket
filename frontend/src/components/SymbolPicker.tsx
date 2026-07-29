/**
 * Symbol multi-select with live search against analysis.db.
 *
 * One control covers both ways people name symbols: type a ticker or a company
 * name to search, or paste a comma/space separated list and add it in one go
 * (the paste option appears at the top of the results whenever the text holds a
 * separator).
 *
 * Filtering is server-side (`GET /api/symbols`), so cmdk isn't used here — its
 * value is client-side filtering. Keyboard navigation is handled directly:
 * ↑/↓ move, Enter takes the highlighted row, Escape closes.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Popover } from "radix-ui";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { get } from "@/lib/api";
import { parseSymbols } from "@/lib/runs";
import { useDebounced } from "@/lib/useDebounced";
import { Input, cn } from "@/components/ui";

type Match = {
  symbol: string;
  name: string | null;
  sector: string | null;
  industry: string | null;
};

export function SymbolPicker({
  symbols,
  onChange,
  placeholder = "Search ticker or company, or paste a list…",
}: {
  symbols: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const query = useDebounced(text.trim(), 200);

  // A separator in the text means the user is pasting a list, not searching.
  const pasted = useMemo(() => (/[\s,;]/.test(text) ? parseSymbols(text) : []), [text]);

  const { data: matches = [], isFetching } = useQuery({
    queryKey: ["symbol-search", query],
    queryFn: () => get<Match[]>("/symbols", { q: query, limit: 30 }),
    enabled: open && query.length > 0 && pasted.length === 0,
    staleTime: 60_000,
  });

  const rows = useMemo(
    () => matches.filter((m) => !symbols.includes(m.symbol)),
    [matches, symbols],
  );

  // Options are [paste-all?, ...matches]; keep the cursor inside that range.
  const optionCount = (pasted.length > 0 ? 1 : 0) + rows.length;
  useEffect(() => setCursor(0), [query, pasted.length]);

  function add(newSymbols: string[]) {
    const next = [...new Set([...symbols, ...newSymbols])];
    onChange(next);
    setText("");
    setOpen(false);
    inputRef.current?.focus();
  }

  function take(index: number) {
    if (pasted.length > 0 && index === 0) return add(pasted);
    const match = rows[index - (pasted.length > 0 ? 1 : 0)];
    if (match) add([match.symbol]);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setCursor((c) => Math.min(c + 1, Math.max(optionCount - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (optionCount > 0) take(cursor);
      // No results but a plain ticker typed — take it at face value; the API
      // reports back which symbols weren't found.
      else if (text.trim()) add(parseSymbols(text));
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Backspace" && !text && symbols.length > 0) {
      onChange(symbols.slice(0, -1));
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <Popover.Root open={open && (optionCount > 0 || query.length > 0)} onOpenChange={setOpen}>
        <Popover.Anchor asChild>
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dim" />
            <Input
              ref={inputRef}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setOpen(true);
              }}
              onFocus={() => text && setOpen(true)}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              className="pl-7"
            />
          </div>
        </Popover.Anchor>

        <Popover.Portal>
          <Popover.Content
            align="start"
            sideOffset={4}
            // Keep the caret in the input — the popover must not steal focus.
            onOpenAutoFocus={(e) => e.preventDefault()}
            onCloseAutoFocus={(e) => e.preventDefault()}
            // Tailwind v4 no longer auto-wraps a bare custom property in var().
            className="z-50 max-h-72 w-[var(--radix-popover-trigger-width)] min-w-80 overflow-y-auto rounded-md border border-line bg-panel2 p-1 shadow-xl"
          >
            {pasted.length > 0 && (
              <button
                onMouseEnter={() => setCursor(0)}
                onClick={() => add(pasted)}
                className={cn(
                  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px]",
                  cursor === 0 ? "bg-accent/15 text-ink" : "text-ink hover:bg-accent/10",
                )}
              >
                Add {pasted.length} pasted symbol{pasted.length === 1 ? "" : "s"}
                <span className="tnum truncate text-dim">{pasted.join(", ")}</span>
              </button>
            )}

            {pasted.length === 0 &&
              rows.map((match, i) => {
                const index = i;
                return (
                  <button
                    key={match.symbol}
                    onMouseEnter={() => setCursor(index)}
                    onClick={() => add([match.symbol])}
                    className={cn(
                      "flex w-full items-baseline gap-2 rounded px-2 py-1 text-left text-[12px]",
                      cursor === index ? "bg-accent/15" : "hover:bg-accent/10",
                    )}
                  >
                    <span className="tnum w-16 shrink-0 font-semibold text-ink">
                      {match.symbol}
                    </span>
                    <span className="flex-1 truncate text-ink/90">{match.name ?? "—"}</span>
                    <span className="shrink-0 truncate text-[11px] text-dim">
                      {match.sector ?? ""}
                    </span>
                  </button>
                );
              })}

            {pasted.length === 0 && rows.length === 0 && (
              <div className="px-2 py-3 text-[12px] text-dim">
                {isFetching ? "Searching…" : `No match for "${query}" — press Enter to add anyway.`}
              </div>
            )}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {symbols.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {symbols.map((sym) => (
            <span
              key={sym}
              className="tnum flex items-center gap-1 rounded bg-accent/15 px-1.5 py-0.5 text-[11px] text-accent"
            >
              {sym}
              <button
                onClick={() => onChange(symbols.filter((s) => s !== sym))}
                aria-label={`Remove ${sym}`}
                className="hover:text-ink"
              >
                <X size={10} />
              </button>
            </span>
          ))}
          <button
            onClick={() => onChange([])}
            className="rounded px-1.5 py-0.5 text-[11px] text-dim hover:text-ink"
          >
            clear all
          </button>
        </div>
      )}
    </div>
  );
}
