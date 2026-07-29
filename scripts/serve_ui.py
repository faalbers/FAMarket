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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-exit", action="store_true", help="keep serving with zero tabs")
    args = parser.parse_args()

    if not DIST.is_dir():
        raise SystemExit("frontend/dist not found — run `npm run build` in frontend/ first")

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
