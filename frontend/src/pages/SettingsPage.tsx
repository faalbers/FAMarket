/**
 * /settings — the UI-editable settings, plus the peak-detection tuner.
 *
 * The form is built from a schema the API serves, so adding a setting is a
 * backend change only. Save writes just the CHANGED keys to the machine-local
 * `settings.local.json`; `config/settings.py` stays the committed defaults and
 * is never touched. Delete that file to reset everything.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save } from "lucide-react";
import { get, put } from "@/lib/api";
import { CalibrationTuner } from "@/pages/CalibrationTuner";
import { Collapsible } from "@/components/Collapsible";
import { Button, EmptyState, Input, PageHeader, cn } from "@/components/ui";

type Field = {
  path: string;
  label: string;
  kind: "int" | "float" | "bool" | "slider";
  min: number | null;
  max: number | null;
  step: number;
  group: string;
  help: string;
  value: number | boolean;
};

type Section = { section: string; fields: Field[] };

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [changes, setChanges] = useState<Record<string, number | boolean>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({ "Scoring weights": true });
  const [showTuner, setShowTuner] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => get<{ sections: Section[] }>("/settings"),
    staleTime: 60_000,
  });

  const save = useMutation({
    mutationFn: () => put<{ saved: number; changed: string[] }>("/settings", { changes }),
    onSuccess: (res) => {
      setChanges({});
      setNote(
        res.saved
          ? `Saved ${res.saved} setting${res.saved === 1 ? "" : "s"}: ${res.changed.join(", ")}`
          : "No changes to save.",
      );
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (err: Error) => setNote(err.message),
  });

  const valueOf = (field: Field) =>
    field.path in changes ? changes[field.path]! : field.value;

  const set = (path: string, value: number | boolean) =>
    setChanges((prev) => ({ ...prev, [path]: value }));

  // The Overall weights are a blend, so their sum is worth seeing while editing.
  const weightSum = useMemo(() => {
    const fields =
      data?.sections.find((s) => s.section === "Scoring weights")?.fields ?? [];
    return fields.reduce((total, f) => total + Number(valueOf(f)), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, changes]);

  if (isLoading) return <EmptyState title="Loading settings…" />;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Settings"
        caption={
          Object.keys(changes).length
            ? `${Object.keys(changes).length} unsaved change${Object.keys(changes).length === 1 ? "" : "s"}`
            : "Defaults live in config/settings.py; only your changes are saved."
        }
        actions={
          <div className="flex items-center gap-2">
            <Button
              disabled={Object.keys(changes).length === 0}
              onClick={() => setChanges({})}
              title="Discard unsaved edits"
            >
              <RotateCcw size={12} /> Revert
            </Button>
            <Button
              variant="primary"
              disabled={Object.keys(changes).length === 0}
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              <Save size={12} /> Save changes
            </Button>
          </div>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        {(data?.sections ?? []).map((section) => {
          const groups = [...new Set(section.fields.map((f) => f.group))];
          return (
            <Collapsible
              key={section.section}
              title={section.section}
              meta={`${section.fields.length} settings`}
              open={open[section.section] ?? false}
              onToggle={() =>
                setOpen((prev) => ({ ...prev, [section.section]: !prev[section.section] }))
              }
            >
              {section.section === "Scoring weights" && (
                <div className="mb-2 text-[11px] text-dim">
                  Sum: <span className="tnum text-ink">{weightSum.toFixed(2)}</span>
                </div>
              )}
              {groups.map((group) => (
                <div key={group} className="mb-3">
                  {group && (
                    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-dim">
                      {group}
                    </div>
                  )}
                  <div className="grid grid-cols-3 gap-2">
                    {section.fields
                      .filter((f) => f.group === group)
                      .map((field) => {
                        const value = valueOf(field);
                        const edited = field.path in changes;
                        return (
                          <label
                            key={field.path}
                            title={field.help}
                            className={cn(
                              "flex items-center justify-between gap-2 rounded border px-2 py-1 text-[12px]",
                              edited ? "border-accent/50 bg-accent/5" : "border-line",
                            )}
                          >
                            <span className="truncate text-dim">{field.label}</span>
                            {field.kind === "bool" ? (
                              <input
                                type="checkbox"
                                checked={Boolean(value)}
                                onChange={(e) => set(field.path, e.target.checked)}
                                className="size-3.5 shrink-0 accent-[#6ea8fe]"
                              />
                            ) : field.kind === "slider" ? (
                              <span className="flex shrink-0 items-center gap-1.5">
                                <input
                                  type="range"
                                  min={field.min ?? 0}
                                  max={field.max ?? 1}
                                  step={field.step}
                                  value={Number(value)}
                                  onChange={(e) => set(field.path, Number(e.target.value))}
                                  className="w-20 accent-[#6ea8fe]"
                                />
                                <span className="tnum w-9 text-right text-ink">
                                  {Number(value).toFixed(2)}
                                </span>
                              </span>
                            ) : (
                              <span className="w-20 shrink-0">
                                <Input
                                  type="number"
                                  value={String(value)}
                                  min={field.min ?? undefined}
                                  max={field.max ?? undefined}
                                  step={field.step}
                                  onChange={(e) => set(field.path, Number(e.target.value))}
                                  className="px-1.5 py-1 text-right"
                                />
                              </span>
                            )}
                          </label>
                        );
                      })}
                  </div>
                </div>
              ))}
            </Collapsible>
          );
        })}

        <Collapsible
          title="Peak-detection calibration (visual tuner)"
          meta="tunes prominence & distance"
          open={showTuner}
          onToggle={() => setShowTuner((v) => !v)}
        >
          {showTuner && <CalibrationTuner />}
        </Collapsible>
      </div>

      {note && (
        <div className="border-t border-line bg-panel px-3 py-1.5 text-[11px] text-dim">{note}</div>
      )}
    </div>
  );
}
