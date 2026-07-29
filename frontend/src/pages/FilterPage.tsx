/**
 * /filter — build a screen, see how many symbols it matches, then run it.
 *
 * Top-level blocks are ANDed; a block passes when its own condition OR any of
 * its OR children passes (children are fallbacks). An enabled-but-incomplete
 * block is SKIPPED rather than failing every row, so adding a fresh block never
 * empties the result — the match readout says when that is happening.
 *
 * Run saves a run file and opens /output?run=<id> in a new tab, the same URL
 * contract the Streamlit page used.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Eraser, FolderOpen, Play, Plus, Save } from "lucide-react";
import {
  clean,
  countMatches,
  hydrate,
  loadRegistry,
  newBlock,
  openFilter,
  runFilter,
  saveFilter,
  type Block,
  type FilterSet,
} from "@/lib/filters";
import { useDebounced } from "@/lib/useDebounced";
import { FilterBlock } from "@/components/FilterBlock";
import { Collapsible } from "@/components/Collapsible";
import { Markdown } from "@/components/Markdown";
import { Button, Input, PageHeader, Panel, cn } from "@/components/ui";

export function FilterPage() {
  const [types, setTypes] = useState<string[]>(["standard"]);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [name, setName] = useState("");
  const [comment, setComment] = useState("");
  const [aiInstructions, setAiInstructions] = useState("");
  const [showTypes, setShowTypes] = useState(true);
  const [showComment, setShowComment] = useState(false);
  const [showInstructions, setShowInstructions] = useState(false);
  const [split, setSplit] = useState(50);
  const [note, setNote] = useState<string | null>(null);

  const { data: registry } = useQuery({
    queryKey: ["filter-registry", types],
    queryFn: () => loadRegistry(types),
    staleTime: 5 * 60_000,
  });

  const payload: FilterSet = useMemo(
    () => ({
      selected_types: types,
      blocks: clean(blocks),
      comment,
      ai_instructions: aiInstructions,
      name,
    }),
    [types, blocks, comment, aiInstructions, name],
  );

  // Debounced so typing a threshold doesn't fire a query per keystroke.
  const settled = useDebounced(payload, 350);
  const { data: matches, isFetching } = useQuery({
    queryKey: ["filter-count", settled],
    queryFn: () => countMatches(settled),
    enabled: types.length > 0,
    staleTime: 30_000,
  });

  const run = useMutation({
    mutationFn: () => runFilter(payload),
    onSuccess: (res) => {
      if (!res.run_id) {
        setNote("No symbols matched — nothing was saved.");
        return;
      }
      window.open(`/output?run=${encodeURIComponent(res.run_id)}`, "_blank");
      setNote(`${res.count} matches — opened in a new tab.`);
    },
    onError: (err: Error) => setNote(err.message),
  });

  useEffect(() => {
    if (blocks.length === 0) setBlocks([newBlock()]);
    // Start with one empty block so the page isn't a blank slate.
  }, [blocks.length]);

  const patch = (id: string, next: Block) =>
    setBlocks((prev) => prev.map((b) => (b._id === id ? next : b)));

  const patchChild = (parentId: string, child: Block) =>
    setBlocks((prev) =>
      prev.map((b) =>
        b._id === parentId
          ? { ...b, or_children: (b.or_children ?? []).map((c) => (c._id === child._id ? child : c)) }
          : b,
      ),
    );

  const move = (id: string, delta: number) =>
    setBlocks((prev) => {
      const index = prev.findIndex((b) => b._id === id);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved!);
      return next;
    });

  async function doSave() {
    const res = await saveFilter(payload);
    if (res.cancelled) return;
    if (res.name) setName(res.name);
    setNote(`Saved as ${res.name}.`);
  }

  async function doLoad(replace: boolean) {
    const res = await openFilter();
    if (res.cancelled) return;
    const loaded = hydrate(res.blocks ?? []);
    setBlocks((prev) => (replace ? loaded : [...prev, ...loaded]));
    if (replace) {
      setTypes(res.selected_types ?? []);
      setComment(res.comment ?? "");
      setAiInstructions(res.ai_instructions ?? "");
      setName(res.name ?? "");
    }
    setNote(`${replace ? "Loaded" : "Added"} ${loaded.length} conditions from ${res.name}.`);
  }

  const enabledCount = blocks.filter((b) => b.enabled).length;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Filter"
        caption={
          types.length === 0 ? (
            "Pick at least one security type to start."
          ) : (
            <>
              <span className={cn("tnum font-semibold", isFetching ? "text-dim" : "text-accent")}>
                {matches?.count ?? "—"}
              </span>{" "}
              symbols match · {enabledCount} condition{enabledCount === 1 ? "" : "s"}
              {matches?.incomplete_blocks
                ? ` · ${matches.incomplete_blocks} incomplete condition${matches.incomplete_blocks === 1 ? "" : "s"} skipped`
                : ""}
            </>
          )
        }
        actions={
          <div className="flex items-center gap-2">
            <div className="w-44">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Filter name"
              />
            </div>
            <Button onClick={doSave} disabled={blocks.length === 0}>
              <Save size={12} /> Save
            </Button>
            <Button onClick={() => doLoad(true)}>
              <FolderOpen size={12} /> Load
            </Button>
            <Button onClick={() => doLoad(false)} title="Append another filter's conditions">
              <Plus size={12} /> Add
            </Button>
            <Button
              variant="primary"
              loading={run.isPending}
              disabled={types.length === 0 || !matches?.count}
              onClick={() => run.mutate()}
            >
              <Play size={12} /> Run ({matches?.count ?? 0})
            </Button>
          </div>
        }
      />

      <Collapsible
        title="Security type"
        meta={`${types.length} selected`}
        open={showTypes}
        onToggle={() => setShowTypes((v) => !v)}
      >
        <div className="grid grid-cols-3 gap-1">
          {(registry?.screen_types ?? []).map((type) => (
            <label
              key={type.key}
              title={type.help}
              className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-[12px] text-ink hover:bg-panel2"
            >
              <input
                type="checkbox"
                checked={types.includes(type.key)}
                onChange={(e) =>
                  setTypes((prev) =>
                    e.target.checked ? [...prev, type.key] : prev.filter((t) => t !== type.key),
                  )
                }
                className="size-3.5 accent-[#6ea8fe]"
              />
              {type.label}
            </label>
          ))}
        </div>
      </Collapsible>

      <Collapsible
        title="Comment"
        meta={comment ? `${comment.length} chars` : "none"}
        open={showComment}
        onToggle={() => setShowComment((v) => !v)}
      >
        <div className="mb-2 flex items-center gap-2">
          <span className="text-[11px] text-dim">preview</span>
          <input
            type="range"
            min={0}
            max={100}
            value={split}
            onChange={(e) => setSplit(Number(e.target.value))}
            className="w-40 accent-[#6ea8fe]"
          />
          <span className="text-[11px] text-dim">editor</span>
        </div>
        <div className="flex gap-2" style={{ minHeight: 160 }}>
          {split > 0 && (
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="What this filter does · how to tweak it · how to sort the results"
              className="min-h-40 rounded-md border border-line bg-panel2 p-2 font-mono text-[12px] text-ink outline-none focus:border-accent/60"
              style={{ flexBasis: `${split}%` }}
            />
          )}
          {split < 100 && (
            <div
              className="min-h-40 overflow-auto rounded-md border border-line bg-panel p-2"
              style={{ flexBasis: `${100 - split}%` }}
            >
              <Markdown>{comment || "_Nothing written yet._"}</Markdown>
            </div>
          )}
        </div>
      </Collapsible>

      {aiInstructions && (
        <Collapsible
          title="AI instructions"
          meta="read-only"
          open={showInstructions}
          onToggle={() => setShowInstructions((v) => !v)}
        >
          <Markdown>{aiInstructions}</Markdown>
        </Collapsible>
      )}

      <Panel
        title="Conditions"
        className="min-h-0 flex-1"
        actions={
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => setBlocks((prev) => [...prev, newBlock()])}>
              <Plus size={12} /> Add condition
            </Button>
            <Button size="sm" variant="danger" onClick={() => setBlocks([newBlock()])}>
              <Eraser size={12} /> Clear
            </Button>
          </div>
        }
      >
        <div className="flex flex-col gap-2 p-3">
          {blocks.map((block) => (
            <div key={block._id} className="flex flex-col gap-1">
              <FilterBlock
                block={block}
                registry={registry}
                onChange={(next) => patch(block._id, next)}
                onRemove={() => setBlocks((prev) => prev.filter((b) => b._id !== block._id))}
                onMove={(delta) => move(block._id, delta)}
                onAddOr={() =>
                  patch(block._id, {
                    ...block,
                    or_children: [...(block.or_children ?? []), newBlock(block.param)],
                  })
                }
              />

              {(block.or_children ?? []).length > 0 && (
                <div className="ml-6 flex flex-col gap-1 border-l border-line pl-3">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-dim">
                    OR — fallbacks
                  </div>
                  {(block.or_children ?? []).map((child) => (
                    <FilterBlock
                      key={child._id}
                      block={child}
                      registry={registry}
                      isChild
                      onChange={(next) => patchChild(block._id, next)}
                      onRemove={() =>
                        patch(block._id, {
                          ...block,
                          or_children: (block.or_children ?? []).filter(
                            (c) => c._id !== child._id,
                          ),
                        })
                      }
                    />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </Panel>

      {note && (
        <div className="border-t border-line bg-panel px-3 py-1.5 text-[11px] text-dim">{note}</div>
      )}
    </div>
  );
}
