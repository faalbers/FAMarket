/**
 * /utilities — small tools that don't belong to a data page.
 *
 * Today just one: email a symbol selection. More slot in as further
 * collapsible sections.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FolderOpen, Mail } from "lucide-react";
import { get, post } from "@/lib/api";
import { loadSelection } from "@/lib/runs";
import { SymbolPicker } from "@/components/SymbolPicker";
import { Collapsible } from "@/components/Collapsible";
import { Button, Input, PageHeader } from "@/components/ui";

export function UtilitiesPage() {
  const [open, setOpen] = useState(true);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [intro, setIntro] = useState("");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);

  const { data: status } = useQuery({
    queryKey: ["email-status"],
    queryFn: () => get<{ configured: boolean }>("/utilities/email/status"),
    staleTime: Infinity,
  });

  // Recipients split on commas, semicolons or whitespace.
  const recipients = to.split(/[,;\s]+/).filter(Boolean);

  const send = useMutation({
    mutationFn: () =>
      post<{ sent: boolean; recipients: number; symbols: number }>("/utilities/email/send", {
        to: recipients,
        subject,
        intro,
        symbols,
      }),
    onSuccess: (res) =>
      setNote(`Sent ${res.symbols} symbols to ${res.recipients} recipient${res.recipients === 1 ? "" : "s"}.`),
    onError: (err: Error) => setNote(err.message),
  });

  const ready = recipients.length > 0 && subject.trim() !== "" && symbols.length > 0;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Utilities"
        caption={
          status?.configured
            ? "Email is configured."
            : "Email isn't set up — add GMAIL_USER and GMAIL_APP_PASSWORD to .env."
        }
      />

      <div className="min-h-0 flex-1 overflow-auto">
        <Collapsible
          title="Email a symbol selection"
          meta={symbols.length ? `${symbols.length} symbols` : undefined}
          open={open}
          onToggle={() => setOpen((v) => !v)}
        >
          <div className="flex max-w-3xl flex-col gap-2">
            <Input
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="To — one or more addresses"
            />
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject"
            />
            <textarea
              value={intro}
              onChange={(e) => setIntro(e.target.value)}
              placeholder="Intro paragraph (optional)"
              className="min-h-20 rounded-md border border-line bg-panel2 p-2 text-[12px] text-ink outline-none focus:border-accent/60"
            />

            <SymbolPicker symbols={symbols} onChange={setSymbols} />

            <div className="flex gap-2">
              <Button
                onClick={async () => {
                  const res = await loadSelection("symbols");
                  if (res.cancelled || !res.items) return;
                  setSymbols(res.items);
                  if (!subject.trim() && res.name) setSubject(res.name);
                }}
              >
                <FolderOpen size={12} /> Load .syms
              </Button>
              <Button
                variant="primary"
                disabled={!ready || !status?.configured}
                loading={send.isPending}
                onClick={() => send.mutate()}
              >
                <Mail size={12} /> Send email
              </Button>
            </div>

            {note && <div className="text-[11px] text-dim">{note}</div>}
          </div>
        </Collapsible>
      </div>
    </div>
  );
}
