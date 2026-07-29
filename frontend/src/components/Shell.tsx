/**
 * App shell: sidebar navigation + the routed page.
 *
 * The presence socket is mounted here — every open tab holds one, so the server
 * can exit when the last tab closes (see `api/lifecycle.py`), matching the
 * lifecycle `streamlit run` used to give us.
 */
import { useEffect } from "react";
import { Link, Outlet } from "@tanstack/react-router";
import {
  BookOpen,
  Download,
  Filter,
  LineChart,
  Settings,
  SlidersHorizontal,
  Table2,
  Wrench,
} from "lucide-react";

function usePresence() {
  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${location.host}/api/presence`);
      // Reconnect so a tab left open across a server restart keeps being counted.
      ws.onclose = () => {
        if (!closed) timer = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, []);
}

const NAV = [
  { to: "/fetch", icon: Download, text: "Fetch Control" },
  { to: "/filter", icon: Filter, text: "Filter" },
  { to: "/output", icon: Table2, text: "Output" },
  { to: "/sector-indices", icon: LineChart, text: "Sector Indices" },
  { to: "/scoring-rules", icon: SlidersHorizontal, text: "Scoring Rules" },
  { to: "/parameters", icon: BookOpen, text: "Parameters" },
  { to: "/utilities", icon: Wrench, text: "Utilities" },
  { to: "/settings", icon: Settings, text: "Settings" },
] as const;

export function Shell() {
  usePresence();

  return (
    <div className="grid h-full grid-cols-[188px_1fr] bg-line">
      <nav className="flex flex-col gap-1 bg-panel p-2.5">
        <div className="mb-3 px-2 pt-1">
          <div className="text-[13px] font-bold tracking-tight text-ink">FAMarket</div>
          <div className="text-[10px] uppercase tracking-widest text-dim">Stock screening</div>
        </div>

        {NAV.map(({ to, icon: Icon, text }) => (
          <Link
            key={to}
            to={to}
            className="rounded-md px-2.5 py-1.5 text-[12px] text-dim transition-colors hover:bg-panel2 hover:text-ink"
            activeProps={{ className: "!bg-accent/15 !text-accent font-semibold" }}
          >
            <span className="flex items-center gap-2">
              <Icon size={14} />
              {text}
            </span>
          </Link>
        ))}

        {/* Lightweight Charts is Apache-2.0 on condition of this attribution. */}
        <div className="mt-auto px-2 text-[10px] leading-relaxed text-dim/70">
          Charts by TradingView
          <br />
          Lightweight Charts™
        </div>
      </nav>

      <main className="min-w-0 overflow-hidden bg-bg">
        <Outlet />
      </main>
    </div>
  );
}
