/**
 * The ONE place a `config/param_hints.py` entry gets turned into on-screen text
 * — the React counterpart of that module's `hint_markdown()` / `hint_html()`.
 * Every surface that explains a parameter (reference page, picker info panel,
 * column header popover) renders through this, so the shape is defined once.
 *
 * Content always comes from the registry; no page ever writes its own
 * description of a metric.
 */
import type { ParamHint } from "@/lib/api";

export function HintBody({ hint }: { hint: ParamHint }) {
  const how = hint.how_to_use;
  const bullets = Array.isArray(how) ? how : how ? [how] : [];

  return (
    <div className="prose-read text-[12px] text-dim">
      {hint.what_it_is && <p className="text-ink/90">{hint.what_it_is}</p>}
      {bullets.length > 0 && (
        <ul>
          {bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}
      {hint.vs_peers && (
        <p>
          <span className="font-semibold text-ink/80">Peers:</span> {hint.vs_peers}
        </p>
      )}
    </div>
  );
}

/** `key · unit` chrome line shown above a hint body. */
export function HintMeta({ paramKey, unit }: { paramKey: string; unit?: string }) {
  return (
    <div className="text-[11px] text-dim">
      key <code className="rounded bg-panel2 px-1 text-ink/80">{paramKey}</code>
      {unit ? (
        <>
          {" · "}unit <code className="rounded bg-panel2 px-1 text-ink/80">{unit}</code>
        </>
      ) : null}
    </div>
  );
}
