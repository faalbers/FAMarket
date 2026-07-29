/**
 * /fetch — start, watch and stop a data run.
 *
 * The run is a DETACHED OS process, so this page is a controller, not an owner:
 * closing the tab, or restarting the server, leaves the fetch running. Progress
 * arrives over SSE — a snapshot on connect, then state changes and new log
 * lines as they land — which is what the Streamlit page could not do.
 *
 * Stop is cooperative: the run checks a flag at safe batch boundaries, and every
 * completed batch is already committed, so a stopped run resumes rather than
 * being wasted.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ClipboardList, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import { get, post } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";
import { loadSelection, saveSelection } from "@/lib/runs";
import { localTime } from "@/lib/format";
import { SymbolPicker } from "@/components/SymbolPicker";
import { Collapsible } from "@/components/Collapsible";
import { Button, EmptyState, Input, PageHeader, Panel, cn } from "@/components/ui";

type RunState = {
  status?: string;
  pid?: number;
  label?: string;
  mode?: string;
  started_at?: string;
  finished_at?: string;
  summary?: Record<string, unknown> | null;
  error?: string | null;
};

type Snapshot = {
  active: boolean;
  ended_unexpectedly: boolean;
  stop_requested: boolean;
  analyze_after_stop: boolean;
  state: RunState | null;
  lines?: string[];
};

type ReportStep = {
  step: string;
  candidates: number;
  locked: number;
  abandoned: number;
  stale: number;
  not_due: number;
  due: number;
};

const MAX_LINES = 2000;

/** Flatten the run summary into readable Parameter / Value rows. */
function flatten(value: unknown, prefix = ""): [string, string][] {
  if (value === null || value === undefined) return [];
  if (typeof value !== "object") return [[prefix, String(value)]];
  const out: [string, string][] = [];
  for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
    out.push(...flatten(inner, prefix ? `${prefix} · ${key}` : key));
  }
  return out;
}

export function FetchPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [scope, setScope] = useState<"full" | "subset">("full");
  const [subset, setSubset] = useState<string[]>([]);
  const [discover, setDiscover] = useState(false);
  const [respectLock, setRespectLock] = useState(true);
  const [runBackup, setRunBackup] = useState(true);
  const [analyzeAfterStop, setAnalyzeAfterStop] = useState(true);
  const [showDanger, setShowDanger] = useState(false);
  const [showLog, setShowLog] = useState(true);
  const [report, setReport] = useState<ReportStep[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [resetConfirm, setResetConfirm] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  const onMessage = useCallback((data: Snapshot) => {
    setSnapshot(data);
    if (data.lines?.length) {
      setLines((prev) => [...prev, ...data.lines!].slice(-MAX_LINES));
    }
  }, []);
  const connected = useEventStream<Snapshot>("/api/fetch/stream", onMessage);

  // Follow the tail, but stop fighting the user once they scroll up.
  useEffect(() => {
    const box = logRef.current;
    if (box && pinnedRef.current) box.scrollTop = box.scrollHeight;
  }, [lines]);

  const { data: meta } = useQuery({
    queryKey: ["analysis-meta"],
    queryFn: () => get<{ available: boolean; analyzed_at?: string; prices_as_of?: string; n_symbols?: number }>("/meta/analysis"),
    refetchInterval: snapshot?.active ? 30_000 : false,
  });

  const { data: snapshots } = useQuery({
    queryKey: ["db-snapshots"],
    queryFn: () => get<{ snapshots: { stamp: string; saved_at: string; count: number }[] }>("/fetch/snapshots"),
    enabled: showDanger,
  });

  const active = snapshot?.active ?? false;
  const state = snapshot?.state ?? null;

  const start = useMutation({
    mutationFn: (analysisOnly: boolean) =>
      post<{ launched: boolean; pid?: number }>("/fetch/start", {
        discover: analysisOnly ? false : discover,
        subset: scope === "subset" && subset.length ? subset : null,
        respect_lock: respectLock,
        run_backup: analysisOnly ? false : runBackup,
        analysis_only: analysisOnly,
        label: analysisOnly
          ? "analysis only"
          : `${scope === "subset" ? `subset (${subset.length})` : "full universe"}`,
      }),
    onSuccess: (res) => setNote(`Run started (pid ${res.pid}).`),
    onError: (err: Error) => setNote(err.message),
  });

  const stop = useMutation({
    mutationFn: () => post("/fetch/stop", { analyze_after: analyzeAfterStop }),
    onSuccess: () => setNote("Stop requested — the run will finish its current batch first."),
  });

  const clear = useMutation({
    mutationFn: () => post("/fetch/clear"),
    onSuccess: () => setNote("Cleared."),
  });

  const dryRun = useMutation({
    mutationFn: () =>
      get<{ report: { steps: ReportStep[] } }>("/fetch/report", {
        subset: scope === "subset" ? subset.join(",") : "",
        respect_lock: String(respectLock),
      }),
    onSuccess: (res) => setReport(res.report.steps),
    onError: (err: Error) => setNote(err.message),
  });

  const restore = useMutation({
    mutationFn: (stamp: string) => post("/fetch/restore", { stamp }),
    onSuccess: () => setNote("Databases reverted. The previous ones are in backups/pre_restore."),
    onError: (err: Error) => setNote(err.message),
  });

  const reset = useMutation({
    mutationFn: () => post("/fetch/reset", { confirm: "RESET" }),
    onSuccess: () => {
      setNote("All data reset.");
      setResetConfirm("");
    },
    onError: (err: Error) => setNote(err.message),
  });

  const statusLabel = active
    ? snapshot?.stop_requested
      ? "Stopping…"
      : "Running"
    : (state?.status ?? "idle");

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Fetch Control"
        caption={
          meta?.available ? (
            <>
              {meta.n_symbols?.toLocaleString()} symbols analysed · prices as of {meta.prices_as_of}{" "}
              · last analysed {localTime(meta.analyzed_at)}
            </>
          ) : (
            "No analysis yet."
          )
        }
        actions={
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "flex items-center gap-1.5 rounded px-2 py-1 text-[11px]",
                active ? "bg-accent/15 text-accent" : "bg-line text-dim",
              )}
              title={connected ? "Live updates connected" : "Reconnecting…"}
            >
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  active ? "animate-pulse bg-accent" : connected ? "bg-dim" : "bg-down",
                )}
              />
              {statusLabel}
            </span>
            <Button
              variant="primary"
              disabled={active}
              loading={start.isPending}
              onClick={() => start.mutate(false)}
            >
              <Play size={12} /> Run fetch
            </Button>
            <Button variant="danger" disabled={!active} onClick={() => stop.mutate()}>
              <Square size={12} /> Stop
            </Button>
          </div>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr] gap-px bg-line">
        <div className="flex flex-col gap-px overflow-auto bg-line">
          <Panel title="Run options">
            <div className="flex flex-col gap-2 p-3 text-[12px]">
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  checked={scope === "full"}
                  onChange={() => setScope("full")}
                  className="accent-[#6ea8fe]"
                />
                Full universe
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  checked={scope === "subset"}
                  onChange={() => setScope("subset")}
                  className="accent-[#6ea8fe]"
                />
                Dev subset
              </label>

              {scope === "subset" && (
                <div className="flex flex-col gap-2 rounded border border-line p-2">
                  <SymbolPicker symbols={subset} onChange={setSubset} placeholder="Symbols…" />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={!subset.length}
                      onClick={async () => {
                        const res = await saveSelection("symbols", subset, "dev-subset");
                        if (!res.cancelled) setNote(`Saved ${res.count} symbols.`);
                      }}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      onClick={async () => {
                        const res = await loadSelection("symbols");
                        if (!res.cancelled && res.items) setSubset(res.items);
                      }}
                    >
                      Load
                    </Button>
                  </div>
                </div>
              )}

              <label className="mt-1 flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={discover}
                  onChange={(e) => setDiscover(e.target.checked)}
                  className="size-3.5 accent-[#6ea8fe]"
                />
                Run symbol discovery first
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={respectLock}
                  onChange={(e) => setRespectLock(e.target.checked)}
                  className="size-3.5 accent-[#6ea8fe]"
                />
                Respect the fetch lock
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={runBackup}
                  onChange={(e) => setRunBackup(e.target.checked)}
                  className="size-3.5 accent-[#6ea8fe]"
                />
                Back up databases first
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={analyzeAfterStop}
                  onChange={(e) => setAnalyzeAfterStop(e.target.checked)}
                  className="size-3.5 accent-[#6ea8fe]"
                />
                Analyse after a stop
              </label>

              <div className="mt-2 flex flex-col gap-2">
                <Button disabled={active} loading={dryRun.isPending} onClick={() => dryRun.mutate()}>
                  <ClipboardList size={12} /> What would it fetch?
                </Button>
                <Button disabled={active} onClick={() => start.mutate(true)}>
                  <RotateCcw size={12} /> Rebuild analysis only
                </Button>
              </div>
            </div>
          </Panel>

          {report && (
            <Panel title="Next fetch — gate report">
              <table className="w-full border-collapse text-[11px]">
                <thead>
                  <tr className="border-b border-line text-left text-dim">
                    <th className="px-2 py-1">Step</th>
                    <th className="px-2 py-1 text-right">Due</th>
                    <th className="px-2 py-1 text-right">Locked</th>
                    <th className="px-2 py-1 text-right">Not due</th>
                  </tr>
                </thead>
                <tbody>
                  {report.map((step) => (
                    <tr key={step.step} className="border-b border-line/50">
                      <td className="px-2 py-1 text-ink">{step.step}</td>
                      <td className="tnum px-2 py-1 text-right text-accent">{step.due}</td>
                      <td className="tnum px-2 py-1 text-right text-dim">{step.locked}</td>
                      <td className="tnum px-2 py-1 text-right text-dim">{step.not_due}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}

          <Collapsible
            title="Danger zone"
            open={showDanger}
            onToggle={() => setShowDanger((v) => !v)}
          >
            <div className="flex flex-col gap-3">
              <div>
                <div className="mb-1 flex items-center gap-1.5 text-[11px] text-down">
                  <AlertTriangle size={12} /> Revert databases to a backup
                </div>
                <div className="flex flex-col gap-1">
                  {(snapshots?.snapshots ?? []).map((snap) => (
                    <Button
                      key={snap.stamp}
                      size="sm"
                      disabled={active || restore.isPending}
                      onClick={() => restore.mutate(snap.stamp)}
                    >
                      {snap.saved_at} · {snap.count} dbs
                    </Button>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-1 flex items-center gap-1.5 text-[11px] text-down">
                  <Trash2 size={12} /> Reset all data
                </div>
                <div className="flex gap-2">
                  <Input
                    value={resetConfirm}
                    onChange={(e) => setResetConfirm(e.target.value)}
                    placeholder='type RESET'
                  />
                  <Button
                    variant="danger"
                    disabled={active || resetConfirm !== "RESET"}
                    loading={reset.isPending}
                    onClick={() => reset.mutate()}
                  >
                    Reset
                  </Button>
                </div>
              </div>
            </div>
          </Collapsible>
        </div>

        <div className="flex min-h-0 flex-col gap-px bg-line">
          <Panel title={`Run · ${statusLabel}`} className="shrink-0">
            <div className="p-3 text-[12px]">
              {state ? (
                <>
                  <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-dim">
                    {state.label && (
                      <span>
                        <span className="text-dim/70">label</span> {state.label}
                      </span>
                    )}
                    {state.pid && (
                      <span className="tnum">
                        <span className="text-dim/70">pid</span> {state.pid}
                      </span>
                    )}
                    {state.started_at && (
                      <span>
                        <span className="text-dim/70">started</span> {localTime(state.started_at)}
                      </span>
                    )}
                    {state.finished_at && (
                      <span>
                        <span className="text-dim/70">finished</span> {localTime(state.finished_at)}
                      </span>
                    )}
                  </div>

                  {snapshot?.ended_unexpectedly && (
                    <div className="mb-2 rounded border border-down/50 bg-down/10 px-2 py-1 text-down">
                      The last run ended without finishing — check the log below.
                    </div>
                  )}
                  {state.error && (
                    <div className="mb-2 rounded border border-down/50 bg-down/10 px-2 py-1 text-down">
                      {state.error}
                    </div>
                  )}

                  {state.summary && (
                    <table className="border-collapse text-[11px]">
                      <tbody>
                        {flatten(state.summary).map(([key, value]) => (
                          <tr key={key}>
                            <td className="py-0.5 pr-4 text-dim">{key}</td>
                            <td className="tnum py-0.5 text-ink">{value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {!active && (
                    <Button size="sm" className="mt-2" onClick={() => clear.mutate()}>
                      Dismiss
                    </Button>
                  )}
                </>
              ) : (
                <span className="text-dim">No run recorded yet.</span>
              )}
            </div>
          </Panel>

          <Panel
            title={`Run log · ${lines.length} lines`}
            className="min-h-0 flex-1"
            bodyClassName="flex min-h-0 flex-1 flex-col"
            actions={
              <Button size="sm" variant="toggle" active={showLog} onClick={() => setShowLog((v) => !v)}>
                {showLog ? "Hide" : "Show"}
              </Button>
            }
          >
            {showLog ? (
              <div
                ref={logRef}
                onScroll={(e) => {
                  const box = e.currentTarget;
                  pinnedRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
                }}
                className="min-h-0 flex-1 overflow-auto p-2"
              >
                {lines.length === 0 ? (
                  <EmptyState title="No log output yet" />
                ) : (
                  <pre className="whitespace-pre-wrap text-[11px] leading-relaxed text-dim">
                    {lines.join("\n")}
                  </pre>
                )}
              </div>
            ) : null}
          </Panel>
        </div>
      </div>

      {note && (
        <div className="border-t border-line bg-panel px-3 py-1.5 text-[11px] text-dim">{note}</div>
      )}
    </div>
  );
}
