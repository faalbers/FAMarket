/**
 * /charts?view=news — on-demand headlines per symbol.
 *
 * Fetched live from yfinance + Polygon + finviz, never from a database; the
 * server caches for 15 minutes because Polygon's free tier allows 5 requests a
 * minute. Each symbol splits into company-specific and broader-context news.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, FileDown, Sparkles } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button, EmptyState, PageHeader, Panel, cn } from "@/components/ui";

type Article = {
  Title: string;
  Url: string;
  Publisher: string | null;
  Published: string | null;
  Sources: string | null;
  Sentiment: string | null;
};

type Group = {
  symbol: string;
  company: string;
  company_news: Article[];
  context_news: Article[];
};

type NewsResponse = { symbols: string[]; groups: Group[]; sources: string[] };

function ArticleTable({ rows }: { rows: Article[] }) {
  if (rows.length === 0) return <div className="px-3 py-2 text-[11px] text-dim">None.</div>;
  return (
    <table className="w-full border-collapse text-[12px]">
      <thead>
        <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-dim">
          <th className="w-8 px-2 py-1" />
          <th className="px-2 py-1">Title</th>
          <th className="w-40 px-2 py-1">Published</th>
          <th className="w-36 px-2 py-1">Publisher</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((article, i) => (
          <tr key={`${article.Url}-${i}`} className="border-b border-line/50 hover:bg-panel2">
            <td className="px-2 py-1">
              <a href={article.Url} target="_blank" rel="noreferrer" className="text-accent">
                <ExternalLink size={12} />
              </a>
            </td>
            <td className="px-2 py-1 text-ink">{article.Title}</td>
            <td className="tnum px-2 py-1 text-dim">{article.Published?.slice(0, 16) ?? "—"}</td>
            <td className="truncate px-2 py-1 text-dim">{article.Publisher ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function NewsView({ symbols }: { symbols: string[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set(symbols.slice(0, 1)));
  const [note, setNote] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["news", symbols],
    queryFn: () => get<NewsResponse>("/news", { symbols: symbols.join(",") }),
    staleTime: 15 * 60_000,
    retry: false,
  });

  const pdf = useMutation({
    mutationFn: () => post<{ filename: string }>("/news/pdf", { symbols }),
    onSuccess: (res) => {
      window.open(`/api/reports/${encodeURIComponent(res.filename)}`, "_blank");
      setNote(`Saved ${res.filename}`);
    },
    onError: (err: Error) => setNote(err.message),
  });

  const aiReports = useMutation({
    mutationFn: () => post<{ files: string[]; directory: string }>("/news/ai-reports", { symbols }),
    onSuccess: (res) =>
      setNote(`Wrote ${res.files.length} markdown reports to ${res.directory}. Run /make_news_reports to turn them into summary PDFs.`),
    onError: (err: Error) => setNote(err.message),
  });

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Latest news"
        caption={
          isLoading
            ? "Fetching headlines — Polygon is rate-limited to 5 requests a minute, so this can take a moment."
            : `${symbols.length} symbols · sources: ${data?.sources.join(", ") ?? "—"}`
        }
        actions={
          <div className="flex items-center gap-2">
            <Button loading={pdf.isPending} onClick={() => pdf.mutate()}>
              <FileDown size={12} /> News PDF
            </Button>
            <Button
              loading={aiReports.isPending}
              onClick={() => aiReports.mutate()}
              title="Scrapes full article bodies — slow"
            >
              <Sparkles size={12} /> AI news reports
            </Button>
          </div>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <EmptyState title="Fetching news…" />
        ) : error ? (
          <EmptyState title="Could not fetch news" detail={String(error)} />
        ) : (
          (data?.groups ?? []).map((group) => {
            const isOpen = open.has(group.symbol);
            const total = group.company_news.length + group.context_news.length;
            return (
              <Panel key={group.symbol} className="border-b border-line">
                <button
                  onClick={() =>
                    setOpen((prev) => {
                      const next = new Set(prev);
                      if (!next.delete(group.symbol)) next.add(group.symbol);
                      return next;
                    })
                  }
                  className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-panel2"
                >
                  {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  <span className="tnum font-semibold text-ink">{group.symbol}</span>
                  <span className="truncate text-[12px] text-dim">{group.company}</span>
                  <span
                    className={cn(
                      "tnum ml-auto rounded px-1.5 text-[10px]",
                      total ? "bg-accent/15 text-accent" : "bg-line text-dim",
                    )}
                  >
                    {total}
                  </span>
                </button>

                {isOpen && (
                  <div className="border-t border-line">
                    <div className="px-3 pt-2 text-[10px] font-semibold uppercase tracking-wider text-dim">
                      About {group.symbol}
                    </div>
                    <ArticleTable rows={group.company_news} />
                    <div className="px-3 pt-3 text-[10px] font-semibold uppercase tracking-wider text-dim">
                      Broader context
                    </div>
                    <ArticleTable rows={group.context_news} />
                  </div>
                )}
              </Panel>
            );
          })
        )}
      </div>

      {note && (
        <div className="border-t border-line bg-panel px-3 py-1.5 text-[11px] text-dim">{note}</div>
      )}
    </div>
  );
}
