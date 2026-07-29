/**
 * Markdown rendering for filter Comments and AI instructions.
 *
 * react-markdown ignores raw HTML by default, so a pasted `<script>` simply
 * isn't rendered — no sanitiser needed for this local single-user app. Don't
 * add `rehype-raw` without revisiting that. `remark-gfm` is required or tables
 * and strikethrough render blank.
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/components/ui";

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div
      className={cn(
        "prose-read text-[12px] text-dim",
        "[&_h1]:mt-2 [&_h1]:mb-1 [&_h1]:text-[15px] [&_h1]:font-semibold [&_h1]:text-ink",
        "[&_h2]:mt-2 [&_h2]:mb-1 [&_h2]:text-[14px] [&_h2]:font-semibold [&_h2]:text-ink",
        "[&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:text-[13px] [&_h3]:font-semibold [&_h3]:text-ink",
        "[&_strong]:text-ink [&_a]:text-accent [&_a]:underline",
        "[&_code]:rounded [&_code]:bg-panel2 [&_code]:px-1 [&_code]:text-ink",
        "[&_table]:my-2 [&_table]:border-collapse",
        "[&_th]:border [&_th]:border-line [&_th]:px-2 [&_th]:py-1 [&_th]:text-ink",
        "[&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-line [&_blockquote]:pl-3",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
