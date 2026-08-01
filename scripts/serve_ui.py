"""
Single-command launcher for the React UI.

    python scripts/serve_ui.py               # serve built frontend + API, open browser
    python scripts/serve_ui.py --no-browser
    python scripts/serve_ui.py --no-exit     # keep serving with zero tabs open

Serves the built Vite frontend and the API from one FastAPI process. Every open
tab holds a presence WebSocket; when the last one closes (plus a grace period
that covers reloads) the server exits, so the app does not linger once you
are done with it.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import lifecycle  # noqa: E402
from api.main import DIST, app  # noqa: E402
from data_layer import run_state  # noqa: E402


def _port_owner(port: int) -> str | None:
    """Best-effort 'PID N (name.exe)' for whatever already holds `port`.

    Shells out to netstat/tasklist (both stdlib-free, built into Windows) rather
    than adding psutil as a dependency — same call as `core/meminfo.py` makes for
    RAM stats without a new package."""
    try:
        netstat = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return None
    pid = None
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
            pid = parts[-1]
            break
    if not pid:
        return None
    try:
        tasklist = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        name = tasklist.split(",")[0].strip('"') if tasklist.strip() else "unknown"
    except Exception:
        name = "unknown"
    return f"PID {pid} ({name})"


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    # 8000 is left alone deliberately — a local MCP server owns it on this machine.
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-exit", action="store_true", help="keep serving with zero tabs")
    args = parser.parse_args()

    if not DIST.is_dir():
        raise SystemExit("frontend/dist not found — run `npm run build` in frontend/ first")

    # Preflight: uvicorn's own bind failure is a bare WinError with no way to tell
    # "something old is still running" from "something else entirely is on this
    # port" — this gives an actionable message (and the PID to stop) up front.
    if _port_in_use("127.0.0.1", args.port):
        owner = _port_owner(args.port)
        detail = f" — looks like {owner}" if owner else ""
        raise SystemExit(
            f"Port {args.port} is already in use{detail}. "
            f"Stop that process first, or pass --port to use a different one."
        )

    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info")
    server = uvicorn.Server(config)

    if not args.no_exit:

        def shutdown() -> None:
            # A detached fetch outlives the app, so say so rather than leaving
            # the user wondering whether closing the tab killed their run.
            run_state.announce_on_shutdown()
            # should_exit triggers uvicorn's graceful shutdown from any thread/task.
            server.should_exit = True

        lifecycle.set_shutdown(shutdown)

    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, [f"http://127.0.0.1:{args.port}/"]).start()

    server.run()


if __name__ == "__main__":
    main()
