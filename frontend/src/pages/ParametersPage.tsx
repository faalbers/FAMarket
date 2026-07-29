/**
 * Parameter Reference — read-only browse of every `config/param_hints.py` entry,
 * grouped into collapsible categories. Search matches name, key, category and
 * the hint text; a live search auto-opens the categories that still match.
 *
 * Laid out for easy reading: narrow column, large parameter names, roomy line
 * spacing (`.prose-read`).
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { get, type HintRegistry, type ParamHint } from "@/lib/api";
import { HintBody, HintMeta } from "@/components/HintBody";
import { EmptyState, Input, PageHeader } from "@/components/ui";

type Entry = [key: string, hint: ParamHint];

function haystack([key, h]: Entry): string {
  const how = Array.isArray(h.how_to_use) ? h.how_to_use.join(" ") : (h.how_to_use ?? "");
  return [key, h.name, h.category, h.what_it_is ?? "", how, h.vs_peers ?? ""].join(" ").toLowerCase();
}

export function ParametersPage() {
  const [query, setQuery] = useState("");
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  const { data, isLoading, error } = useQuery({
    queryKey: ["hints"],
    queryFn: () => get<HintRegistry>("/hints"),
    staleTime: Infinity,
  });

  // Registry insertion order is already the logical reading order — preserve it.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = new Map<string, Entry[]>();
    for (const entry of Object.entries(data ?? {}) as Entry[]) {
      if (q && !haystack(entry).includes(q)) continue;
      const cat = entry[1].category || "Other";
      const bucket = out.get(cat);
      if (bucket) bucket.push(entry);
      else out.set(cat, [entry]);
    }
    return [...out.entries()];
  }, [data, query]);

  const total = Object.keys(data ?? {}).length;
  const shown = groups.reduce((n, [, items]) => n + items.length, 0);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Parameter Reference"
        caption={
          isLoading
            ? "Loading…"
            : error
              ? "Could not load the parameter registry."
              : query
                ? `${shown} of ${total} parameters match`
                : `Every parameter the system knows about — ${total} in total.`
        }
      />

      <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
        {/* ~60% width keeps lines short and comfortable to read. */}
        <div className="max-w-3xl">
          <div className="relative mb-3">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dim" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="search name, key or description…"
              className="pl-7"
            />
          </div>

          {groups.length === 0 && !isLoading && (
            <EmptyState
              title="No parameters match your search."
              detail="Try a shorter term, or clear the box to browse every category."
            />
          )}

          <div className="flex flex-col gap-2">
            {groups.map(([category, items]) => {
              // Collapsed by default; a live search opens everything still
              // matching. An explicit click always wins over that default.
              const open = toggled[category] ?? Boolean(query);
              return (
                <section key={category} className="rounded-md border border-line bg-panel">
                  <button
                    onClick={() => setToggled((t) => ({ ...t, [category]: !open }))}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] font-semibold text-ink hover:bg-panel2"
                  >
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    {category}
                    <span className="tnum ml-auto rounded bg-line px-1.5 text-[10px] text-dim">
                      {items.length}
                    </span>
                  </button>

                  {open && (
                    <div className="flex flex-col gap-2 border-t border-line p-2.5">
                      {items.map(([key, hint]) => (
                        <article key={key} className="rounded-md border border-line bg-panel2 p-3">
                          <h3 className="text-[14px] font-semibold text-ink">{hint.name || key}</h3>
                          <div className="mt-0.5 mb-1.5">
                            <HintMeta paramKey={key} unit={hint.unit} />
                          </div>
                          <HintBody hint={hint} />
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
